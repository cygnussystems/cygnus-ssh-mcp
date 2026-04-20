import time
import base64
import logging
import shlex
from abc import ABC, abstractmethod
from typing import Optional
from datetime import datetime, UTC
from cygnus_ssh_mcp.models import (
    CommandHandle, SshError, TaskNotFound, SudoRequired
)


class SshTaskOperations(ABC):
    """Base class for background task management. Platform-specific commands are abstract."""

    def __init__(self, ssh_client):
        """
        Args:
            ssh_client: Reference to parent SSH client
        """
        self.ssh_client = ssh_client
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    # ==========================================================================
    # Abstract methods - implemented by platform-specific subclasses
    # ==========================================================================

    @abstractmethod
    def _get_default_log_dir(self) -> str:
        """Return the default directory for task log files."""
        pass

    @abstractmethod
    def _build_launch_script(self, cmd: str, stdout_log: str, stderr_log: str, sudo: bool) -> tuple:
        """
        Build the script content and path to launch a background task.

        Args:
            cmd: Command to execute
            stdout_log: Path to redirect stdout
            stderr_log: Path to redirect stderr
            sudo: Whether to run with elevated privileges

        Returns:
            Tuple of (script_path, script_content, create_script_command)
        """
        pass

    @abstractmethod
    def _cmd_check_process_running(self, pid: int) -> str:
        """
        Return command to check if a process is running.
        Command should exit 0 if running, non-zero if not.
        """
        pass

    @abstractmethod
    def _cmd_kill_process(self, pid: int, signal: int, sudo: bool) -> str:
        """
        Return command to kill a process with the given signal.

        Args:
            pid: Process ID
            signal: Signal number (15=TERM, 9=KILL on Linux)
            sudo: Whether to use elevated privileges
        """
        pass

    @abstractmethod
    def _cmd_rename_log(self, old_path: str, new_path: str) -> str:
        """Return command to rename a log file."""
        pass

    # ==========================================================================
    # Shared implementation methods
    # ==========================================================================

    def launch_task(self, cmd, stdout_log=None, stderr_log=None, log_output=True, sudo=False, add_to_history=False):
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
            log_dir = self._get_default_log_dir()
            default_log_path = f"{log_dir}/task-{pid_placeholder}.log"

            if log_output and stdout_log is None and stderr_log is None:
                effective_stdout_log = default_log_path
                effective_stderr_log = default_log_path
                self.logger.info(f"Defaulting log output to {default_log_path} (placeholder)")
            elif log_output and stdout_log is None:
                effective_stdout_log = f"{log_dir}/null" if log_dir.startswith("C:") else "/dev/null"
            elif log_output and stderr_log is None:
                effective_stderr_log = f"{log_dir}/null" if log_dir.startswith("C:") else "/dev/null"

            # Build platform-specific launch script
            script_path, script_content, create_script_cmd = self._build_launch_script(
                cmd, effective_stdout_log, effective_stderr_log, sudo
            )

            # Create the script
            stdin, stdout, stderr = self.ssh_client._client.exec_command(create_script_cmd, timeout=5)
            exit_status = stdout.channel.recv_exit_status()
            if exit_status != 0:
                err_msg = f"Failed to create launch script: {stderr.read().decode('utf-8', errors='replace')}"
                self.logger.error(err_msg)
                raise SshError(err_msg)

            # Execute the script
            self.logger.info(f"Launching background task using script: {script_path}")
            stdin, stdout, stderr = self.ssh_client._client.exec_command(script_path, timeout=10)

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
                    pid_match = line[4:].strip()
                    break

            if not pid_match or not pid_match.isdigit():
                err_msg = f"Failed to parse PID from task launch. stdout: '{stdout_data}', stderr: '{stderr_output}'"
                self.logger.error(err_msg)
                raise SshError(err_msg)

            pid = int(pid_match)
            self.logger.info(f"Task launched successfully with PID: {pid}")

            # Rename default log file if used
            if effective_stdout_log == default_log_path:
                final_log_path = f"{log_dir}/task-{pid}.log"
                try:
                    rename_cmd = self._cmd_rename_log(default_log_path, final_log_path)
                    stdin, stdout, stderr = self.ssh_client._client.exec_command(rename_cmd, timeout=10)
                    exit_status = stdout.channel.recv_exit_status()
                    if exit_status == 0:
                        self.logger.info(f"Renamed default log to {final_log_path}")
                    else:
                        stderr_output = stderr.read().decode('utf-8', errors='replace')
                        self.logger.warning(f"Failed to rename default log file: exit code {exit_status}, stderr: {stderr_output}")
                except Exception as rename_err:
                    self.logger.warning(f"Failed to rename default log file: {rename_err}")

            # Create a handle for the task
            if add_to_history:
                handle = self.ssh_client.history_manager.add_command(cmd, pid)
            else:
                handle = CommandHandle(self.ssh_client.history_manager._next_id, cmd)
                handle.pid = pid
                handle.start_ts = datetime.now(UTC)
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

        cmd = self._cmd_check_process_running(pid)
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
        """Internal helper to attempt killing a remote PID."""
        if not pid:
            return False

        self.logger.warning(f"Attempting to kill remote process PID {pid} (sudo={sudo}).")
        killed = False

        for signal in [15, 9]:  # Try TERM then KILL
            cmd = self._cmd_kill_process(pid, signal, sudo)

            with self.ssh_client._client.get_transport().open_session() as chan:
                chan.settimeout(5.0)
                try:
                    chan.exec_command(cmd)
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
        # Use base kill command and let ssh_client.run() handle sudo (supports password-based sudo)
        cmd = self._cmd_kill_process(pid, signal, sudo=False)
        kill_cmd_succeeded = False
        try:
            handle = self.ssh_client.run(cmd, io_timeout=10, runtime_timeout=15, sudo=sudo)
            if handle.exit_code == 0:
                self.logger.info(f"Successfully sent signal {signal} to PID {pid}.")
                kill_cmd_succeeded = True
            else:
                self.logger.warning(f"Command 'kill' for PID {pid} failed with exit code {handle.exit_code}.")
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
            cmd_force = self._cmd_kill_process(pid, force_kill_signal, sudo=False)
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
                    self.logger.error(f"Force kill command for PID {pid} failed with exit code {handle_force.exit_code}.")
                    return "failed_to_kill"
            except Exception as e_force:
                self.logger.error(f"Error sending force signal {force_kill_signal} to PID {pid}: {e_force}")
                return "error"
        elif current_status == "running":
            self.logger.warning(f"PID {pid} still running after signal {signal}, no force kill attempted.")
            return "failed_to_kill"

        return "failed_to_kill"


class SshTaskOperations_Linux(SshTaskOperations):
    """Linux implementation using bash scripts and kill signals."""

    def _get_default_log_dir(self) -> str:
        return "/tmp"

    def _build_launch_script(self, cmd: str, stdout_log: str, stderr_log: str, sudo: bool) -> tuple:
        """Build bash script to launch background task."""
        timestamp = int(time.time())
        script_path = f"/tmp/launch_script_{timestamp}.sh"

        # Build redirection part
        if stdout_log:
            if stderr_log and stderr_log != stdout_log:
                redirect_part = f"1>{shlex.quote(stdout_log)} 2>{shlex.quote(stderr_log)}"
            else:
                redirect_part = f"1>{shlex.quote(stdout_log)} 2>&1"
        else:
            if stderr_log:
                redirect_part = f"1>/dev/null 2>{shlex.quote(stderr_log)}"
            else:
                redirect_part = "1>/dev/null 2>/dev/null"

        if sudo:
            # Check if sudo password is available for non-interactive sudo with password
            sudo_password = getattr(self.ssh_client, 'sudo_password', None)
            if sudo_password:
                # Use sudo -S to read password from stdin via echo
                # Store command in environment variable to avoid nested quoting issues
                # nohup ensures process survives script exit
                script_content = f"""#!/bin/bash
# Store command in variable to avoid nested quoting issues
export __SUDO_CMD={shlex.quote(cmd)}
# Launch command in background with proper redirection
nohup bash -c 'echo {shlex.quote(sudo_password)} | sudo -S -p "" bash -c "$__SUDO_CMD"' {redirect_part} &
# Store PID
pid=$!
# Output only the PID with marker
echo "PID:$pid"
# Clean up this script
rm -f {script_path}
exit 0
"""
            else:
                # Try passwordless sudo
                script_content = f"""#!/bin/bash
# Store command in variable to avoid quoting issues
export __SUDO_CMD={shlex.quote(cmd)}
# Launch command in background with proper redirection
nohup sudo -n bash -c "$__SUDO_CMD" {redirect_part} &
# Store PID
pid=$!
# Output only the PID with marker
echo "PID:$pid"
# Clean up this script
rm -f {script_path}
exit 0
"""
        else:
            script_content = f"""#!/bin/bash
# Launch command in background with proper redirection
bash -c {shlex.quote(cmd)} {redirect_part} &
# Store PID
pid=$!
# Output only the PID with marker
echo "PID:$pid"
# Clean up this script
rm -f {script_path}
exit 0
"""

        create_script_cmd = f"cat > {script_path} << 'EOFSCRIPT'\n{script_content}\nEOFSCRIPT\nchmod +x {script_path}"
        return script_path, script_content, create_script_cmd

    def _cmd_check_process_running(self, pid: int) -> str:
        return f"kill -0 {pid}"

    def _cmd_kill_process(self, pid: int, signal: int, sudo: bool) -> str:
        cmd = f"kill -{signal} {pid}"
        if sudo:
            return f"sudo -n bash -c {shlex.quote(cmd)}"
        return cmd

    def _cmd_rename_log(self, old_path: str, new_path: str) -> str:
        return f"mv {shlex.quote(old_path)} {shlex.quote(new_path)}"


class SshTaskOperations_Win(SshTaskOperations):
    """Windows implementation using PowerShell."""

    def _get_default_log_dir(self) -> str:
        return "C:\\Windows\\Temp"

    def _build_launch_script(self, cmd: str, stdout_log: str, stderr_log: str, sudo: bool) -> tuple:
        """Build PowerShell command to launch background task.

        For Windows, we use PowerShell's -EncodedCommand to avoid needing a script file,
        which simplifies execution over SSH.
        """
        timestamp = int(time.time())
        # We don't actually use a script file anymore, but keep path format for log naming
        script_path = f"powershell_direct_{timestamp}"

        # Escape the command for cmd.exe's /c argument
        ps_escaped_cmd = cmd.replace('"', '\\"')

        # Build the PowerShell script content
        # Use -WindowStyle Hidden for completely detached execution
        # Note: Start-Process doesn't allow stdout and stderr to be the same file
        if stdout_log and stderr_log and stdout_log != stderr_log:
            script_content = f'''$proc = Start-Process -FilePath "cmd.exe" -ArgumentList "/c {ps_escaped_cmd}" -PassThru -WindowStyle Hidden -RedirectStandardOutput "{stdout_log}" -RedirectStandardError "{stderr_log}"; Write-Output "PID:$($proc.Id)"'''
        elif stdout_log:
            # PowerShell requires different files for stdout/stderr, so redirect stderr to separate file
            stderr_path = stdout_log.replace('.log', '_err.log') if '.log' in stdout_log else stdout_log + '_err'
            script_content = f'''$proc = Start-Process -FilePath "cmd.exe" -ArgumentList "/c {ps_escaped_cmd}" -PassThru -WindowStyle Hidden -RedirectStandardOutput "{stdout_log}" -RedirectStandardError "{stderr_path}"; Write-Output "PID:$($proc.Id)"'''
        else:
            # No logging - but still need different files for NUL workaround
            # Use cmd.exe's internal redirection: command >nul 2>&1
            script_content = f'''$proc = Start-Process -FilePath "cmd.exe" -ArgumentList "/c {ps_escaped_cmd} >nul 2>&1" -PassThru -WindowStyle Hidden; Write-Output "PID:$($proc.Id)"'''

        # Encode the script for -EncodedCommand (requires UTF-16LE encoding)
        script_bytes = script_content.encode('utf-16-le')
        script_b64 = base64.b64encode(script_bytes).decode('ascii')

        # For Windows, create_script_cmd is empty (no file to create)
        # and script_path is actually the full execution command
        create_script_cmd = "echo OK"  # No-op, just needs to succeed
        execution_cmd = f'powershell -ExecutionPolicy Bypass -EncodedCommand {script_b64}'

        return execution_cmd, script_content, create_script_cmd

    def _cmd_check_process_running(self, pid: int) -> str:
        # PowerShell command to check if process exists
        # Exit 0 if running, exit 1 if not
        return f'powershell -Command "if (Get-Process -Id {pid} -ErrorAction SilentlyContinue) {{ exit 0 }} else {{ exit 1 }}"'

    def _cmd_kill_process(self, pid: int, signal: int, sudo: bool) -> str:
        # Windows doesn't have signals, but we can simulate:
        # signal 15 (TERM) = normal Stop-Process
        # signal 9 (KILL) = Stop-Process -Force
        if signal == 9:
            return f'powershell -Command "Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue"'
        else:
            return f'powershell -Command "Stop-Process -Id {pid} -ErrorAction SilentlyContinue"'

    def _cmd_rename_log(self, old_path: str, new_path: str) -> str:
        return f'powershell -Command "Move-Item -Path \'{old_path}\' -Destination \'{new_path}\' -Force"'
