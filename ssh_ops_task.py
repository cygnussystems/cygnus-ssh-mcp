import time
import logging
import shlex
from typing import Optional
from ssh_models import (
    CommandHandle, SshError, TaskNotFound, SudoRequired
)

class SshTaskOperations_Win:
    """Handles background task management on Windows systems."""
    
    def __init__(self, ssh_client):
        """
        Args:
            ssh_client: Reference to parent SSH client
        """
        self.ssh_client = ssh_client
        self.logger = logging.getLogger(f"{__name__}.SshTaskOperations_Win")


class SshTaskOperations_Linux:
    """Handles background task management including launch, status, and kill operations."""
    
    def __init__(self, ssh_client):
        """
        Args:
            ssh_client: Reference to parent SSH client
        """
        self.ssh_client = ssh_client
        self.logger = logging.getLogger(f"{__name__}.SshTaskOperations")
        
    def launch_task(self, cmd, stdout_log=None, stderr_log=None, log_output=True, sudo=False):
        """
        Launch a command in the background and return a CommandHandle with the PID.
        
        Args:
            cmd: Command to execute
            stdout_log: Path to redirect stdout
            stderr_log: Path to redirect stderr
            log_output: Whether to enable default logging
            sudo: Whether to run with sudo
            
        Returns:
            CommandHandle with task PID
            
        Raises:
            SshError: If task launch fails
            SudoRequired: If sudo password is required but not provided
        """
        try:
            # Determine log paths
            effective_stdout_log = stdout_log
            effective_stderr_log = stderr_log
            pid_placeholder = f"pid_{int(time.time())}"
            default_log_path = f"/tmp/task-{pid_placeholder}.log"

            if log_output and stdout_log is None and stderr_log is None:
                effective_stdout_log = default_log_path
                effective_stderr_log = default_log_path
                self.logger.info(f"Defaulting log output to {default_log_path} (placeholder)")
            elif log_output and stdout_log is None:
                effective_stdout_log = "/dev/null"
            elif log_output and stderr_log is None:
                effective_stderr_log = "/dev/null"

            # Build the command with backgrounding and PID echo
            # Use a more robust approach to ensure command output doesn't interfere with PID
            bg_cmd_part = f"{cmd}"
            
            # Create a separate subshell for the command with its redirections
            if effective_stdout_log:
                if effective_stderr_log and effective_stderr_log != effective_stdout_log:
                    # Different stdout and stderr destinations
                    redirect_part = f"1>{shlex.quote(effective_stdout_log)} 2>{shlex.quote(effective_stderr_log)}"
                else:
                    # Same destination for both or stderr not specified
                    redirect_part = f"1>{shlex.quote(effective_stdout_log)} 2>&1"
            else:
                # No stdout specified
                if effective_stderr_log:
                    redirect_part = f"1>/dev/null 2>{shlex.quote(effective_stderr_log)}"
                else:
                    redirect_part = "1>/dev/null 2>/dev/null"
            
            # Use a completely different approach to avoid any interference:
            # Create a temporary script that:
            # 1. Launches the command in background with proper redirection
            # 2. Captures PID
            # 3. Outputs ONLY the PID
            # 4. Removes itself
            script_name = f"/tmp/launch_script_{int(time.time())}.sh"
            
            # For sudo commands, we need to make sure the sudo is part of the command being backgrounded
            # not just applied to the script itself
            if sudo:
                bg_cmd_with_sudo = f"sudo -n {bg_cmd_part}"
                script_content = f"""#!/bin/bash
# Launch command in background with proper redirection
# Use explicit redirection to ensure output goes to the right files
# Execute command directly without subshell to ensure redirection works properly
{bg_cmd_with_sudo} {redirect_part} &
# Store PID
pid=$!
# Output only the PID with marker
echo "PID:$pid"
# Clean up this script
rm -f {script_name}
exit 0
"""
            else:
                script_content = f"""#!/bin/bash
# Launch command in background with proper redirection
# Use explicit redirection to ensure output goes to the right files
# Execute command directly without subshell to ensure redirection works properly
bash -c {shlex.quote(bg_cmd_part)} {redirect_part} &
# Store PID
pid=$!
# Output only the PID with marker
echo "PID:$pid"
# Clean up this script
rm -f {script_name}
exit 0
"""
            # First create the script
            create_script_cmd = f"cat > {script_name} << 'EOFSCRIPT'\n{script_content}\nEOFSCRIPT\nchmod +x {script_name}"
            stdin, stdout, stderr = self.ssh_client._client.exec_command(create_script_cmd, timeout=5)
            exit_status = stdout.channel.recv_exit_status()
            if exit_status != 0:
                err_msg = f"Failed to create launch script: {stderr.read().decode('utf-8', errors='replace')}"
                self.logger.error(err_msg)
                raise SshError(err_msg)
            
            # Then execute the script
            # We don't need to add sudo here if we've already included it in the script content
            full_cmd = script_name

            self.logger.info(f"Launching background task using script: {full_cmd}")
            stdin, stdout, stderr = self.ssh_client._client.exec_command(full_cmd, timeout=10)

            # Read all output
            stdout_data = stdout.read().decode('utf-8', errors='replace').strip()
            stderr_output = stderr.read().decode('utf-8', errors='replace').strip()
            exit_status = stdout.channel.recv_exit_status()

            stdin.close()
            stdout.close()
            stderr.close()

            if exit_status != 0:
                err_msg = f"Task launch failed with exit code {exit_status}. stdout: '{stdout_data}', stderr: '{stderr_output}'"
                self.logger.error(err_msg)
                if sudo and "password is required" in stderr_output:
                    raise SudoRequired(cmd)
                raise SshError(err_msg)

            # Extract PID from the "PID:12345" format
            pid_match = None
            for line in stdout_data.splitlines():
                if line.startswith("PID:"):
                    pid_match = line[4:].strip()  # Remove "PID:" prefix
                    break
            
            if not pid_match or not pid_match.isdigit():
                err_msg = f"Failed to parse PID from task launch. stdout: '{stdout_data}', stderr: '{stderr_output}'"
                self.logger.error(err_msg)
                raise SshError(err_msg)

            pid = int(pid_match)
            self.logger.info(f"Task launched successfully with PID: {pid}")

            # Rename default log file if used
            if effective_stdout_log == default_log_path:
                final_log_path = f"/tmp/task-{pid}.log"
                try:
                    rename_cmd = f"mv {shlex.quote(default_log_path)} {shlex.quote(final_log_path)}"
                    self.ssh_client.run(rename_cmd, io_timeout=5, runtime_timeout=10, sudo=sudo)
                    self.logger.info(f"Renamed default log to {final_log_path}")
                except Exception as rename_err:
                    self.logger.warning(f"Failed to rename default log file: {rename_err}")

            # Create and return handle using the history manager
            handle = self.ssh_client.history_manager.add_command(cmd, pid)
            return handle

        except Exception as e:
            self.logger.error(f"Failed to launch task: {e}", exc_info=True)
            if isinstance(e, SudoRequired):
                raise
            raise SshError(f"Failed to launch task: {e}") from e

    def get_task_status(self, pid):
        """
        Check the status of a background task.
        
        Args:
            pid: Process ID to check
            
        Returns:
            'running', 'exited', 'invalid', or 'error'
        """
        if not isinstance(pid, int) or pid <= 0:
            self.logger.warning(f"Invalid PID provided: {pid}")
            return "invalid"

        cmd = f"kill -0 {pid}"
        self.logger.debug(f"Checking status for PID {pid} using command: {cmd}")
        chan = None
        try:
            chan = self.ssh_client._client.get_transport().open_session()
            chan.settimeout(5.0)
            chan.exec_command(cmd)
            stderr_output = chan.makefile_stderr('r').read().decode('utf-8', errors='replace')
            exit_status = chan.recv_exit_status()
            chan.close()

            if exit_status == 0:
                self.logger.debug(f"Status check for PID {pid}: running")
                return "running"
            else:
                self.logger.debug(f"Status check for PID {pid}: exited (exit code {exit_status})")
                return "exited"

        except Exception as e:
            self.logger.error(f"Error checking status for PID {pid}: {e}", exc_info=True)
            if chan and not chan.closed:
                chan.close()
            return "error"

    def _kill_remote_process(self, pid, sudo=False):
        """Internal helper to attempt killing a remote PID. Avoids self.run()."""
        if not pid:
            return False
            
        self.logger.warning(f"Attempting to kill remote process PID {pid} (sudo={sudo}).")
        killed = False
        
        for signal in [15, 9]: # Try TERM then KILL
            cmd = f"kill -{signal} {pid}"
            full_cmd = f"sudo -n bash -c {shlex.quote(cmd)}" if sudo else cmd
            
            with self.ssh_client._client.get_transport().open_session() as chan:
                chan.settimeout(5.0)
                try:
                    chan.exec_command(full_cmd)
                    # Read stderr before checking exit status
                    stderr_file = chan.makefile_stderr('r')
                    stderr = stderr_file.read()
                    if isinstance(stderr, bytes):
                        stderr = stderr.decode('utf-8', errors='replace')
                    stderr_file.close()
                    exit_status = chan.recv_exit_status()
                    
                    if exit_status == 0:
                        self.logger.info(f"Kill command (signal {signal}) for PID {pid} succeeded.")
                        killed = True
                        break
                    else:
                        self.logger.warning(f"Kill command failed with exit code {exit_status}. Stderr: {stderr.strip()}")
                except Exception as e:
                    self.logger.error(f"Error executing kill command: {e}", exc_info=True)
                    break

        return killed

    def kill_task(self, pid, signal=15, sudo=False, force_kill_signal=9, wait_seconds=1.0):
        """
        Kill a background task.
        
        Args:
            pid: Process ID to kill
            signal: Initial signal to send (default: 15/SIGTERM)
            sudo: Whether to use sudo
            force_kill_signal: Fallback signal if initial fails (default: 9/SIGKILL)
            wait_seconds: Time to wait after initial signal before checking status
            
        Returns:
            'killed', 'already_exited', 'failed_to_kill', 'invalid_pid', or 'error'
        """
        if not isinstance(pid, int) or pid <= 0:
            self.logger.warning(f"Invalid PID provided: {pid}")
            return "invalid_pid"
        if not isinstance(signal, int):
            raise ValueError("Signal must be an integer.")
        if force_kill_signal is not None and not isinstance(force_kill_signal, int):
            raise ValueError("force_kill_signal must be an integer or None.")

        self.logger.info(f"Attempting to kill PID {pid} with signal {signal} (sudo={sudo}). Fallback signal: {force_kill_signal}")

        # Check initial status
        initial_status = self.get_task_status(pid)
        if initial_status == "exited":
            self.logger.info(f"PID {pid} was already exited before sending signal.")
            return "already_exited"
        if initial_status == "error":
            self.logger.warning(f"Could not determine initial status for PID {pid}. Proceeding with kill attempt.")

        # Try initial signal
        cmd = f"kill -{signal} {pid}"
        kill_cmd_succeeded = False
        try:
            handle = self.ssh_client.run(cmd, io_timeout=10, runtime_timeout=15, sudo=sudo)
            if handle.exit_code == 0:
                self.logger.info(f"Successfully sent signal {signal} to PID {pid}.")
                kill_cmd_succeeded = True
            else:
                self.logger.warning(f"Command 'kill -{signal} {pid}' failed with exit code {handle.exit_code}.")
        except Exception as e:
            self.logger.warning(f"Error sending signal {signal} to PID {pid}: {e}")

        # Wait and check status
        if wait_seconds > 0:
            self.logger.debug(f"Waiting {wait_seconds}s after signal {signal} attempt...")
            time.sleep(wait_seconds)

        current_status = self.get_task_status(pid)
        if current_status == "exited":
            self.logger.info(f"PID {pid} confirmed exited after signal {signal} attempt.")
            return "killed"
        if current_status == "error":
            self.logger.warning(f"Could not determine status for PID {pid} after signal {signal}.")

        # Try force kill if needed
        if force_kill_signal is not None and current_status == "running":
            self.logger.warning(f"PID {pid} still running after signal {signal}. Attempting force kill with signal {force_kill_signal}.")
            cmd_force = f"kill -{force_kill_signal} {pid}"
            try:
                handle_force = self.ssh_client.run(cmd_force, io_timeout=10, runtime_timeout=15, sudo=sudo)
                if handle_force.exit_code == 0:
                    self.logger.info(f"Successfully sent force signal {force_kill_signal} to PID {pid}.")
                    time.sleep(0.5)
                    final_status = self.get_task_status(pid)
                    if final_status == "exited":
                        self.logger.info(f"PID {pid} confirmed exited after force signal {force_kill_signal}.")
                        return "killed"
                    else:
                        self.logger.error(f"PID {pid} still not exited after force signal {force_kill_signal}.")
                        return "failed_to_kill"
                else:
                    self.logger.error(f"Force kill command 'kill -{force_kill_signal} {pid}' failed with exit code {handle_force.exit_code}.")
                    return "failed_to_kill"
            except Exception as e_force:
                self.logger.error(f"Error sending force signal {force_kill_signal} to PID {pid}: {e_force}")
                return "error"
        elif current_status == "running":
            self.logger.warning(f"PID {pid} still running after signal {signal}, no force kill attempted.")
            return "failed_to_kill"

        return "failed_to_kill"
