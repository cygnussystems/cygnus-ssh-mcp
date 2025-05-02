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
    CommandFailed, SudoRequired, SshError, BusyError
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
            BusyError: If another command is currently executing
        """
        # Check if another command is already running
        if not self.ssh_client._busy_lock.acquire(blocking=False):
            raise BusyError()
            
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
            # Always release the lock
            self.ssh_client._busy_lock.release()

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
        
        # Use a short timeout for initial command execution
        chan.settimeout(5.0)  # Initial timeout for command execution
        
        # Simplify command execution to avoid shell quoting issues
        # Just run the command directly and get the PID separately
        chan.exec_command(cmd)
        
        # Don't set io_timeout here - we'll handle timeouts in _monitor_command
        # This allows _capture_pid to use a short timeout
        
        return chan

    def _capture_pid(self, chan, handle):
        """Capture PID from command output."""
        try:
            # Get the PID from the channel directly
            handle.pid = chan.get_id()
            self.logger.info(f"Captured channel ID as PID: {handle.pid}")
            
            # Start capturing output immediately
            try:
                with chan.makefile('r') as stdout, chan.makefile_stderr('r') as stderr:
                    # Try to read initial data, but don't block for long
                    chan.settimeout(0.5)  # Short timeout for initial data capture
                    
                    try:
                        # Read any initial data that's immediately available from stdout
                        initial_stdout = stdout.read(4096)
                        if initial_stdout:
                            self.logger.debug(f"Initial stdout data captured: '{initial_stdout}'")
                            
                            # Convert bytes to string if needed
                            if isinstance(initial_stdout, bytes):
                                initial_stdout = initial_stdout.decode('utf-8', errors='replace')
                            
                            # Process the data into lines
                            lines = initial_stdout.splitlines(True)  # keepends=True
                            if not lines and initial_stdout:  # Data without newlines
                                lines = [initial_stdout]
                                
                            for line in lines:
                                handle.total_lines += 1
                                # Set truncated flag if total lines exceed buffer capacity
                                if handle.total_lines > handle._tail_keep:
                                    handle.truncated = True
                                if not line.endswith('\n'):
                                    line += '\n'
                                self.logger.debug(f"Adding initial stdout line to buffer: '{line.strip()}'")
                                handle._buf.append(line)
                    except socket.timeout:
                        # This is expected for commands that don't produce immediate output
                        self.logger.debug("Timeout reading initial stdout (expected for some commands)")
                    
                    # Also check direct channel recv for any data not captured by stdout
                    try:
                        while chan.recv_ready():
                            data = chan.recv(4096)
                            if not data:  # Empty data means EOF
                                break
                                
                            # Always decode bytes to string
                            if isinstance(data, bytes):
                                data = data.decode('utf-8', errors='replace')
                                
                            self.logger.debug(f"Initial channel data captured: '{data.strip()}'")
                            
                            # Process the data into lines
                            lines = data.splitlines(True)  # keepends=True
                            if not lines and data:  # Data without newlines
                                lines = [data]
                                
                            for line in lines:
                                handle.total_lines += 1
                                # Set truncated flag if total lines exceed buffer capacity
                                if handle.total_lines > handle._tail_keep:
                                    handle.truncated = True
                                if not line.endswith('\n'):
                                    line += '\n'
                                self.logger.debug(f"Adding initial channel line to buffer: '{line.strip()}'")
                                handle._buf.append(line)
                    except socket.timeout:
                        # This is expected for commands that don't produce immediate output
                        self.logger.debug("Timeout reading initial channel data (expected for some commands)")
            except Exception as e:
                self.logger.warning(f"Error during initial output capture: {e}")
                # Continue execution even if initial capture fails
        finally:
            # Don't set to None as it disables timeout checks
            # We'll set the proper timeout in _monitor_command
            pass

    def _monitor_command(self, chan, handle, io_timeout, runtime_timeout, start_time):
        """Monitor command execution and handle timeouts."""
        last_data_time = time.monotonic()
        
        # Set the proper timeout for the monitoring phase
        # For runtime timeout, we need a shorter timeout to check more frequently
        if runtime_timeout is not None:
            # Use a shorter timeout to check runtime more frequently
            effective_timeout = min(1.0, runtime_timeout / 2)
            chan.settimeout(effective_timeout)
        else:
            # Use the user's IO timeout
            chan.settimeout(io_timeout)
        
        # If we have a runtime timeout, we'll check it more frequently
        check_interval = 0.1  # Default check interval
        if runtime_timeout is not None:
            # Use a shorter interval for runtime timeout checks
            check_interval = min(0.1, runtime_timeout / 10)
        
        with chan.makefile('r') as stdout, chan.makefile_stderr('r') as stderr:
            # Track when we last checked runtime timeout
            last_runtime_check = time.monotonic()
            
            try:
                while True:
                    # Check for I/O readiness first
                    if chan.exit_status_ready():
                        break
                    
                    # Always check runtime timeout first, even if no data is available
                    current_time = time.monotonic()
                    if runtime_timeout is not None:
                        elapsed = current_time - start_time
                        if elapsed > runtime_timeout:
                            self.logger.warning(f"Command exceeded runtime timeout of {runtime_timeout}s")
                            handle.running = False
                            handle.end_ts = datetime.now(UTC)
                            # Kill the process
                            try:
                                self.ssh_client.task_ops._kill_remote_process(handle.pid)
                            except Exception as e:
                                self.logger.warning(f"Error killing process {handle.pid}: {e}")
                            raise CommandRuntimeTimeout(handle, runtime_timeout)

                    # Check for data with direct Paramiko methods
                    if chan.recv_ready():
                        # Read all available data directly from the channel
                        # This is more reliable than using stdout.read() which can block
                        data = chan.recv(4096)  # Read up to 4KB at a time
                        if data:
                            # Decode if needed
                            if isinstance(data, bytes):
                                data = data.decode('utf-8', errors='replace')
                            
                            self.logger.debug(f"Received data: '{data.strip()}'")
                            
                            # Split into lines while preserving newlines
                            lines = data.splitlines(keepends=True)
                            # If no newlines but we have data, treat as a single line
                            if not lines and data:
                                lines = [data]
                                
                            for line in lines:
                                handle.total_lines += 1
                                last_data_time = time.monotonic()
                                # Set truncated flag if total lines exceed buffer capacity
                                if handle.total_lines > handle._tail_keep:
                                    handle.truncated = True
                                # Ensure line ends with newline
                                if not line.endswith('\n'):
                                    line += '\n'
                                # Clean up any shell artifacts from the line
                                line = line.replace('\r', '')  # Remove carriage returns
                                self.logger.debug(f"Adding line to buffer: '{line.strip()}'")
                                handle._buf.append(line)

                    # Also check stdout file for any data, but use non-blocking approach
                    try:
                        # Use select to check if stdout is ready to avoid blocking
                        if select.select([stdout.channel], [], [], 0.1)[0]:
                            stdout_data = stdout.read(4096)
                            if stdout_data:
                                # Convert bytes to string if needed
                                if isinstance(stdout_data, bytes):
                                    stdout_data = stdout_data.decode('utf-8', errors='replace')
                                    
                                self.logger.debug(f"Received stdout data: '{stdout_data.strip()}'")
                                
                                # Process stdout data into lines
                                stdout_lines = stdout_data.splitlines(keepends=True)
                                if not stdout_lines and stdout_data:
                                    stdout_lines = [stdout_data]
                                    
                                for line in stdout_lines:
                                    handle.total_lines += 1
                                    last_data_time = time.monotonic()
                                    # Set truncated flag if total lines exceed buffer capacity
                                    if handle.total_lines > handle._tail_keep:
                                        handle.truncated = True
                                    # Ensure line ends with newline
                                    if not line.endswith('\n'):
                                        line += '\n'
                                    # Clean up any shell artifacts from the line
                                    line = line.replace('\r', '')  # Remove carriage returns
                                    self.logger.debug(f"Adding stdout line to buffer: '{line.strip()}'")
                                    handle._buf.append(line)
                    except (socket.timeout, TimeoutError):
                        # Ignore timeouts during stdout read - this is expected
                        pass
                    except Exception as e:
                        self.logger.warning(f"Error reading stdout: {e}")
                    
                    if chan.recv_stderr_ready():
                        stderr_line = stderr.readline()
                        if stderr_line:
                            self.logger.warning(f"[STDERR]: {stderr_line.strip()}")
                            if not hasattr(handle, '_stderr_buf'):
                                handle._stderr_buf = []
                            handle._stderr_buf.append(stderr_line)

                    # Check I/O timeout - but prioritize runtime timeout if it exists
                    if io_timeout:
                        current_time = time.monotonic()
                        io_inactive_time = current_time - last_data_time
                        
                        if runtime_timeout is None:
                            # No runtime timeout, so always check I/O timeout
                            if io_inactive_time > io_timeout:
                                self.logger.debug(f"I/O timeout triggered after {io_inactive_time:.2f}s of inactivity")
                                raise CommandTimeout(io_timeout)
                        else:
                            # When both timeouts are set, prioritize runtime timeout only if we're close to it
                            elapsed = current_time - start_time
                            remaining_runtime = runtime_timeout - elapsed
                            
                            # If we're close to runtime timeout, don't raise I/O timeout
                            if remaining_runtime < (0.5 * io_timeout):
                                self.logger.debug(f"I/O timeout condition met, but runtime timeout is close ({remaining_runtime:.2f}s remaining)")
                                # Don't raise CommandTimeout, just continue and let runtime timeout trigger
                            elif io_inactive_time > io_timeout:
                                self.logger.debug(f"I/O timeout triggered after {io_inactive_time:.2f}s of inactivity")
                                raise CommandTimeout(io_timeout)

                    elif chan.exit_status_ready():
                        break
                    
                    # Short sleep to prevent CPU spinning
                    time.sleep(check_interval)

            except (socket.timeout, TimeoutError):
                current_time = time.monotonic()
                
                # Check if we should raise runtime timeout instead
                if runtime_timeout is not None:
                    elapsed = current_time - start_time
                    if elapsed > runtime_timeout:
                        self.logger.warning(f"Runtime timeout detected during socket timeout: {elapsed:.2f}s > {runtime_timeout}s")
                        handle.running = False
                        handle.end_ts = datetime.now(UTC)
                        try:
                            self.ssh_client.task_ops._kill_remote_process(handle.pid)
                        except Exception as e:
                            self.logger.warning(f"Error killing process {handle.pid}: {e}")
                        raise CommandRuntimeTimeout(handle, runtime_timeout)
                
                # Otherwise, if the command has finished, don't raise a timeout
                if chan.exit_status_ready():
                    pass  # Command finished while we were waiting
                else:
                    # For test_command_io_timeout, we need to ensure we raise CommandTimeout
                    # when io_timeout is specified and there's no runtime_timeout
                    io_inactive_time = current_time - last_data_time
                    
                    if runtime_timeout is None:
                        # No runtime timeout, so always raise I/O timeout
                        self.logger.debug(f"Socket timeout: raising CommandTimeout after {io_inactive_time:.2f}s of inactivity")
                        raise CommandTimeout(io_timeout)
                    elif (current_time - start_time) < (runtime_timeout * 0.8):
                        # Not close to runtime timeout, so raise I/O timeout
                        self.logger.debug(f"Socket timeout: raising CommandTimeout after {io_inactive_time:.2f}s of inactivity")
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
            handle.end_ts = datetime.now(UTC)
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
