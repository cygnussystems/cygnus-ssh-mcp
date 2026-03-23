from collections import deque
import time
import select
import shlex
import logging
import socket
from datetime import datetime
from typing import Optional, Self
from datetime import UTC
from cygnus_ssh_mcp.models import (
    CommandHandle, CommandTimeout, CommandRuntimeTimeout,
    CommandFailed, SudoRequired, SshError, BusyError
)


class SshRunOperations_Win:
    """Handles synchronous command execution on Windows systems."""
    
    def __init__(self, ssh_client, tail_keep=100):
        """
        Args:
            ssh_client: Reference to parent SSH client
            tail_keep: Number of lines to keep in output buffer
        """
        self.ssh_client = ssh_client
        self.tail_keep = tail_keep
        self.logger = logging.getLogger(f"{__name__}.SshRunOperations_Win")

class SshRunOperations_Linux:
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
                      sudo: bool = False) -> Self: # Should be CommandHandle, not Self
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
            self._capture_pid(chan, handle) # This method seems to be doing more than just PID
            
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
        # Ensure tail_keep is set on the handle, and buffers are initialized correctly
        # The CommandHandle __init__ should handle deque creation with its tail_keep
        if handle._tail_keep is None: # If history_manager didn't set it based on its own defaults
             handle.set_tail_keep(self.tail_keep) # Propagate SshRunOperations default
        else:
             handle.set_tail_keep(handle._tail_keep) # Ensure buffers are sized correctly if already set
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
                    self.logger.info("Sudo password provided. Using sudo with password.")
                    # The key fix: Use sudo -S to read password from stdin with heredoc
                    # -S flag tells sudo to read password from stdin
                    # -p '' prevents sudo from printing a password prompt
                    # <<< provides the password via heredoc which is more reliable than pipe
                    return f"sudo -S -p '' bash -c {shlex.quote(cmd)} <<< {shlex.quote(self.ssh_client.sudo_password)}", True
                else:
                    raise SudoRequired(cmd)
            else: # Other errors during sudo -n check
                raise CommandFailed(sudo_n_exit_code, "", sudo_n_stderr)
        except Exception as e: # Includes socket.timeout from exec_command
            raise SshError(f"Failed during sudo pre-check: {e}") from e

    def _execute_command(self, cmd, io_timeout): # io_timeout not used here
        """Execute the command and return the channel."""
        self.logger.info(f"Executing command: {cmd}")
        chan = self.ssh_client._client.get_transport().open_session()
        chan.settimeout(5.0)  # Initial timeout for command execution itself
        chan.exec_command(cmd)
        # Timeout for subsequent I/O is handled in _monitor_command
        return chan

    def _capture_pid(self, chan, handle): # This method name is misleading, it captures initial output
        """Capture PID (actually channel ID) and initial output."""
        try:
            handle.pid = chan.get_id() # This is channel ID, not OS PID
            self.logger.info(f"Captured channel ID (used as PID reference): {handle.pid}")
            
            # Attempt to read initial output without blocking for too long
            # This helps capture immediate output like prompts or early data
            chan.settimeout(0.5) # Short timeout for initial read attempt
            
            # Check stdout
            if chan.recv_ready():
                data = chan.recv(4096)
                if data:
                    decoded_data = data.decode('utf-8', errors='replace')
                    self.logger.debug(f"Initial stdout data: '{decoded_data.strip()}'")
                    for line in decoded_data.splitlines(keepends=True):
                        handle.add_output(line if line.endswith('\n') else line + '\n')
            
            # Check stderr
            if chan.recv_stderr_ready():
                data_stderr = chan.recv_stderr(4096)
                if data_stderr:
                    decoded_stderr = data_stderr.decode('utf-8', errors='replace')
                    self.logger.debug(f"Initial stderr data: '{decoded_stderr.strip()}'")
                    for line in decoded_stderr.splitlines(keepends=True):
                        handle.add_stderr_output(line if line.endswith('\n') else line + '\n')

        except socket.timeout:
            self.logger.debug("Timeout reading initial stdout/stderr (expected for some commands)")
        except Exception as e:
            self.logger.warning(f"Error during initial output capture: {e}")
        finally:
            # Timeout will be reset in _monitor_command
            pass


    def _monitor_command(self, chan, handle, io_timeout, runtime_timeout, start_time):
        """Monitor command execution and handle timeouts."""
        last_data_time = time.monotonic()
        
        # Set the proper timeout for the monitoring phase
        # For runtime timeout, we need a shorter timeout to check more frequently
        # Paramiko channel timeout is for blocking I/O operations.
        # We use select for non-blocking checks where possible.
        # The channel timeout here acts as a fallback or for operations not covered by select.
        effective_select_timeout = 0.1 # How long select() should block
        if runtime_timeout is not None:
            # Use a shorter select timeout to check runtime more frequently
            effective_select_timeout = min(0.1, runtime_timeout / 20, 1.0) 
        
        # Paramiko's file-like objects (makefile) can be tricky with settimeout.
        # It's often better to use chan.recv_ready(), chan.recv_stderr_ready(),
        # and chan.exit_status_ready() with select on the channel itself.
        
        # Set channel to non-blocking for select, or use its own timeout carefully.
        # For this implementation, we'll rely on chan.settimeout for blocking reads,
        # and frequent checks for runtime_timeout.
        
        # Main monitoring loop
        while not chan.exit_status_ready():
            current_time = time.monotonic()

            # 1. Check Runtime Timeout (highest priority)
            if runtime_timeout is not None:
                elapsed = current_time - start_time
                if elapsed > runtime_timeout:
                    self.logger.warning(f"Command exceeded runtime timeout of {runtime_timeout}s")
                    handle.running = False
                    handle.end_ts = datetime.now(UTC)
                    # Attempt to kill, though channel might be dead
                    try:
                        if hasattr(self.ssh_client, 'task_ops') and hasattr(self.ssh_client.task_ops, '_kill_remote_process'):
                             self.ssh_client.task_ops._kill_remote_process(handle.pid) # pid is channel_id here
                        else: # Fallback if task_ops or method is not available
                             chan.close() # Close channel to signal process
                    except Exception as e_kill:
                        self.logger.warning(f"Error trying to stop process on runtime timeout: {e_kill}")
                    raise CommandRuntimeTimeout(handle, runtime_timeout)

            # 2. Check for I/O using select for non-blocking behavior
            # We need the underlying socket for select, which is chan.fileno()
            # However, direct fileno might not always be available or work as expected with Paramiko's layers.
            # Paramiko's chan.recv_ready(), recv_stderr_ready() are preferred.
            
            readable, _, _ = select.select([chan], [], [], effective_select_timeout)
            
            if readable: # Channel has some event
                if chan.recv_ready():
                    data = chan.recv(4096)
                    if data:
                        decoded_data = data.decode('utf-8', errors='replace')
                        self.logger.debug(f"STDOUT data: '{decoded_data.strip()}'")
                        for line in decoded_data.splitlines(keepends=True):
                            handle.add_output(line if line.endswith('\n') else line + '\n')
                        last_data_time = time.monotonic()
                    # If data is empty, it might mean EOF on that stream, but loop continues until exit_status_ready.

                if chan.recv_stderr_ready():
                    stderr_data = chan.recv_stderr(4096)
                    if stderr_data:
                        decoded_stderr = stderr_data.decode('utf-8', errors='replace')
                        self.logger.warning(f"[STDERR]: {decoded_stderr.strip()}")
                        for line in decoded_stderr.splitlines(keepends=True):
                            handle.add_stderr_output(line if line.endswith('\n') else line + '\n')
                        last_data_time = time.monotonic() # Activity on stderr also resets I/O timeout

            # 3. Check I/O Timeout
            # This check happens regardless of select result, based on last_data_time
            if io_timeout:
                io_inactive_time = current_time - last_data_time
                if io_inactive_time > io_timeout:
                    self.logger.warning(f"Command I/O timeout after {io_inactive_time:.2f}s of inactivity")
                    # Similar to runtime timeout, try to clean up
                    handle.running = False
                    handle.end_ts = datetime.now(UTC)
                    try:
                        chan.close()
                    except Exception as e_kill:
                        self.logger.warning(f"Error trying to stop process on I/O timeout: {e_kill}")
                    raise CommandTimeout(io_timeout)
            
            # If chan.exit_status_ready() was true, loop will break.
            # If not, and no timeouts, loop continues.
            # A very short sleep if select had no activity can prevent tight spinning,
            # but select already has a timeout.

        # After loop: command has exited.
        # Drain any remaining output
        while chan.recv_ready():
            data = chan.recv(4096)
            if not data: break
            for line in data.decode('utf-8', errors='replace').splitlines(keepends=True):
                handle.add_output(line if line.endswith('\n') else line + '\n')
        
        while chan.recv_stderr_ready():
            data = chan.recv_stderr(4096)
            if not data: break
            for line in data.decode('utf-8', errors='replace').splitlines(keepends=True):
                handle.add_stderr_output(line if line.endswith('\n') else line + '\n')


    def _handle_command_completion(self, chan, handle, sudo_pwd_attempted):
        """Handle successful command completion."""
        handle.exit_code = chan.recv_exit_status()
        handle.end_ts = datetime.now(UTC)
        handle.running = False
        self.logger.info(f"Command finished with exit code {handle.exit_code}")

        if handle.exit_code != 0:
            stdout_all = handle.get_full_output()
            stderr_output = handle.get_full_stderr() # Use the new method
            
            if sudo_pwd_attempted and handle.exit_code == 1 and ("incorrect password attempt" in stderr_output.lower()):
                raise SudoRequired(f"{handle.cmd} (Incorrect sudo password provided or required)")
            raise CommandFailed(handle.exit_code, stdout_all, stderr_output)

        return handle

    def _handle_execution_error(self, e, handle):
        """Handle known execution errors."""
        if handle:
            handle.running = False
            handle.end_ts = datetime.now(UTC)
            # Potentially set error message on handle if it has such a field
            if hasattr(handle, 'error_message'):
                handle.error_message = str(e)
        self.logger.error(f"Command execution error: {e}")

    def _handle_unexpected_error(self, e, handle):
        """Handle unexpected errors."""
        if handle:
            handle.running = False
            handle.end_ts = datetime.now(UTC) # Use UTC
            if hasattr(handle, 'error_message'):
                handle.error_message = str(e)
        self.logger.error(f"Unexpected error during command execution: {e}", exc_info=True)

    def _cleanup_command(self, chan, handle):
        """Cleanup command resources."""
        if chan:
            chan.close()
        if handle:
            # Ensure handle status reflects completion if not already set
            if handle.running: # Should have been set by _handle_command_completion or error handlers
                handle.running = False
                if handle.end_ts is None:
                    handle.end_ts = datetime.now(UTC)
            self.logger.debug(f"Command {handle.id} cleanup complete. Final status: exit_code={handle.exit_code}, running={handle.running}")
