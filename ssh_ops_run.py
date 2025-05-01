from collections import deque
import time
import select
import shlex
import logging
import socket
from datetime import datetime
from typing import Optional, Self
from datetime import UTC
from ssh_models import (
    CommandHandle, CommandTimeout, CommandRuntimeTimeout,
    CommandFailed, SudoRequired, SshError
)

class SshRunOperations:
    """Handles synchronous command execution and related operations."""
    
    def __init__(self, ssh_client, tail_keep=100):
        """
        Args:
            ssh_client: Reference to parent SSH client
            tail_keep: Number of lines to keep in output buffer
        """
        self.ssh_client = ssh_client
        self.tail_keep = tail_keep
        self.logger = logging.getLogger(f"{__name__}.SshRunOperations")
        
    def execute_command(self, cmd: str, io_timeout: float = 60.0, 
                      runtime_timeout: Optional[float] = None, 
                      sudo: bool = False) -> Self:
        """
        Execute a command synchronously with timeout management.
        
        Args:
            cmd: Command to execute
            io_timeout: I/O inactivity timeout in seconds
            runtime_timeout: Total execution timeout in seconds
            sudo: Whether to run with sudo
            
        Returns:
            CommandHandle with command results
            
        Raises:
            CommandTimeout: If I/O timeout occurs
            CommandRuntimeTimeout: If runtime timeout occurs
            CommandFailed: If command fails
            SudoRequired: If sudo password is required but not provided
            SshError: For other SSH-related errors
        """
        handle = None
        chan = None
        start_time = time.monotonic()
        sudo_pwd_attempted = False
        
        try:
            # Create command handle
            handle = self._create_command_handle(cmd)
            
            # Handle sudo if needed
            if sudo:
                cmd, sudo_pwd_attempted = self._handle_sudo(cmd)
                
            # Execute command and capture PID
            chan = self._execute_command(cmd, io_timeout)
            self._capture_pid(chan, handle)
            
            # Monitor command execution
            self._monitor_command(chan, handle, io_timeout, runtime_timeout, start_time)
            
            # Handle command completion
            return self._handle_command_completion(chan, handle, sudo_pwd_attempted)
            
        except (CommandTimeout, CommandRuntimeTimeout, CommandFailed, SudoRequired, SshError) as e:
            self._handle_execution_error(e, handle)
            raise
        except Exception as e:
            self._handle_unexpected_error(e, handle)
            raise SshError(f"Unexpected error during command execution: {e}") from e
        finally:
            self._cleanup_command(chan, handle)

    def _create_command_handle(self, cmd):
        """Create and track a new CommandHandle."""
        handle = self.ssh_client.history_manager.add_command(cmd)
        handle._buf = deque(maxlen=self.tail_keep)  # Set buffer size for this handle
        return handle

    def _handle_sudo(self, cmd):
        """Handle sudo command preparation."""
        # Try passwordless sudo first
        self.logger.info(f"Attempting passwordless sudo for: {cmd}")
        test_sudo_cmd = "sudo -n whoami"
        try:
            stdin_t, stdout_t, stderr_t = self.ssh_client._client.exec_command(test_sudo_cmd, timeout=5)
            sudo_n_stderr = stderr_t.read().decode('utf-8', errors='replace')
            sudo_n_exit_code = stdout_t.channel.recv_exit_status()
            stdin_t.close(); stdout_t.close(); stderr_t.close()

            if sudo_n_exit_code == 0:
                self.logger.info("Passwordless sudo successful.")
                return f"sudo -n bash -c {shlex.quote(cmd)}", False
            elif sudo_n_exit_code == 1 and ("sudo:" in sudo_n_stderr or "password is required" in sudo_n_stderr.lower()):
                if self.ssh_client.sudo_password:
                    self.logger.info("Sudo password provided. Will attempt interactive sudo.")
                    return f"sudo -S -p '' bash -c {shlex.quote(cmd)}", True
                else:
                    raise SudoRequired(cmd)
            else:
                raise CommandFailed(sudo_n_exit_code, "", sudo_n_stderr)
        except Exception as e:
            raise SshError(f"Failed during sudo pre-check: {e}") from e

    def _execute_command(self, cmd, io_timeout):
        """Execute the command and return the channel."""
        self.logger.info(f"Executing command: {cmd}")
        chan = self.ssh_client._client.get_transport().open_session()
        chan.settimeout(5.0)  # Initial timeout for command execution
        # More reliable PID capture
        wrapped_cmd = f"bash -c 'echo $$; exec {shlex.quote(cmd)}'"
        chan.exec_command(wrapped_cmd)
        chan.settimeout(io_timeout)  # Set to user's IO timeout
        return chan

    def _capture_pid(self, chan, handle):
        """Capture PID from command output."""
        try:
            with chan.makefile('r') as stdout, chan.makefile_stderr('r') as stderr:
                # First line is PID
                pid_str = stdout.readline().strip()
                if pid_str.isdigit():
                    handle.pid = int(pid_str)
                    self.logger.info(f"Captured remote PID {handle.pid}")
                else:
                    self.logger.warning(f"Failed to capture PID. First line: '{pid_str}'")
        finally:
            if 'stdout' in locals():
                stdout.close()

    def _monitor_command(self, chan, handle, io_timeout, runtime_timeout, start_time):
        """Monitor command execution and handle timeouts."""
        last_data_time = time.monotonic()
        
        with chan.makefile('r') as stdout, chan.makefile_stderr('r') as stderr:
            try:
                while True:
                    # Check runtime timeout
                    if runtime_timeout is not None:
                        elapsed = time.monotonic() - start_time
                        if elapsed > runtime_timeout:
                            self.logger.warning(f"Command exceeded runtime timeout of {runtime_timeout}s")
                            handle.running = False
                            handle.end_ts = datetime.utcnow()
                            self.ssh_client.task_ops._kill_remote_process(handle.pid)
                            raise CommandRuntimeTimeout(handle, runtime_timeout)

                    # Check for I/O readiness
                    if chan.exit_status_ready():
                        break

                    # Check for data with direct Paramiko methods
                    if chan.recv_ready():
                        line = stdout.readline()
                        if line:
                            handle.total_lines += 1
                            last_data_time = time.monotonic()
                            if handle.total_lines > handle._buf.maxlen:
                                handle.truncated = True
                            # Ensure line ends with newline and decode if needed
                            if isinstance(line, bytes):
                                line = line.decode('utf-8', errors='replace')
                            if not line.endswith('\n'):
                                line += '\n'
                            handle._buf.append(line)

                    if chan.recv_stderr_ready():
                        stderr_line = stderr.readline()
                        if stderr_line:
                            self.logger.warning(f"[STDERR]: {stderr_line.strip()}")
                            if not hasattr(handle, '_stderr_buf'):
                                handle._stderr_buf = []
                            handle._stderr_buf.append(stderr_line)

                    # Check I/O timeout
                    if (time.monotonic() - last_data_time) > io_timeout:
                        raise CommandTimeout(io_timeout)

                    elif chan.exit_status_ready():
                        break

            except socket.timeout:
                if chan.exit_status_ready():
                    pass  # Command finished while we were waiting
                else:
                    raise CommandTimeout(io_timeout)
            finally:
                stdout.close()
                stderr.close()

    def _handle_command_completion(self, chan, handle, sudo_pwd_attempted):
        """Handle successful command completion."""
        handle.exit_code = chan.recv_exit_status()
        handle.end_ts = datetime.now(UTC)
        handle.running = False
        self.logger.info(f"Command finished with exit code {handle.exit_code}")

        if handle.exit_code != 0:
            stdout_all = ''.join(handle.tail(handle.total_lines))
            # Use collected stderr if available
            stderr_output = getattr(handle, '_stderr_buf', [])
            stderr_output = ''.join(stderr_output) if stderr_output else ''
            if sudo_pwd_attempted and handle.exit_code == 1 and ("incorrect password attempt" in stderr_output.lower()):
                raise SudoRequired(f"{handle.cmd} (Incorrect sudo password provided or required)")
            raise CommandFailed(handle.exit_code, stdout_all, stderr_output)

        return handle

    def _handle_execution_error(self, e, handle):
        """Handle known execution errors."""
        if handle:
            handle.running = False
            handle.end_ts = datetime.utcnow()
        self.logger.error(f"Command execution error: {e}")

    def _handle_unexpected_error(self, e, handle):
        """Handle unexpected errors."""
        if handle:
            handle.running = False
            handle.end_ts = datetime.utcnow()
        self.logger.error(f"Unexpected error during command execution: {e}", exc_info=True)

    def _cleanup_command(self, chan, handle):
        """Cleanup command resources."""
        if chan:
            chan.close()
        if handle:
            self.logger.debug(f"Command {handle.id} cleanup complete")
