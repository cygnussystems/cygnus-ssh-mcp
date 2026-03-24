from collections import deque
import time
import select
import shlex
import logging
import socket
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional, Self
from datetime import UTC
from cygnus_ssh_mcp.models import (
    CommandHandle, CommandTimeout, CommandRuntimeTimeout,
    CommandFailed, SudoRequired, SshError, BusyError
)


class SshRunOperations(ABC):
    """Base class for synchronous command execution. Platform-specific sudo handling is abstract."""

    def __init__(self, ssh_client, tail_keep=100):
        """
        Args:
            ssh_client: Reference to parent SSH client
            tail_keep: Number of lines to keep in output buffer
        """
        self.ssh_client = ssh_client
        self.tail_keep = tail_keep
        self.logger = logging.getLogger(f"{__name__}.SshRunOperations")

    # ==========================================================================
    # Abstract methods - implemented by platform-specific subclasses
    # ==========================================================================

    @abstractmethod
    def _handle_sudo(self, cmd: str) -> tuple:
        """
        Handle sudo/elevation for command execution.

        Args:
            cmd: Command to execute with elevated privileges

        Returns:
            Tuple of (modified_command, sudo_attempted_flag)

        Raises:
            SudoRequired: If elevation is required but not available
            SshError: If elevation check fails
        """
        pass

    @abstractmethod
    def _check_sudo_error(self, handle, sudo_pwd_attempted: bool) -> bool:
        """
        Check if command failure was due to sudo/elevation issues.

        Args:
            handle: CommandHandle with execution results
            sudo_pwd_attempted: Whether sudo password was attempted

        Returns:
            True if this was a sudo-related error (and exception was raised)
        """
        pass

    # ==========================================================================
    # Shared implementation methods
    # ==========================================================================

    def execute_command(self, cmd: str, io_timeout: float = 60.0,
                        runtime_timeout: Optional[float] = None,
                        sudo: bool = False) -> CommandHandle:
        """
        Execute a command synchronously with timeout management.

        Args:
            cmd: Command to execute
            io_timeout: I/O inactivity timeout in seconds
            runtime_timeout: Total execution timeout in seconds
            sudo: Whether to run with elevated privileges

        Returns:
            CommandHandle with command results

        Raises:
            CommandTimeout: If I/O timeout occurs
            CommandRuntimeTimeout: If runtime timeout occurs
            CommandFailed: If command fails
            SudoRequired: If elevation is required but not available
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

            # Handle sudo/elevation if needed
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
        if handle._tail_keep is None:
            handle.set_tail_keep(self.tail_keep)
        else:
            handle.set_tail_keep(handle._tail_keep)
        return handle

    def _execute_command(self, cmd, io_timeout):
        """Execute the command and return the channel."""
        self.logger.info(f"Executing command: {cmd}")
        chan = self.ssh_client._client.get_transport().open_session()
        chan.settimeout(5.0)
        chan.exec_command(cmd)
        return chan

    def _capture_pid(self, chan, handle):
        """Capture channel ID and initial output."""
        try:
            handle.pid = chan.get_id()
            self.logger.info(f"Captured channel ID (used as PID reference): {handle.pid}")

            chan.settimeout(0.5)

            if chan.recv_ready():
                data = chan.recv(4096)
                if data:
                    decoded_data = data.decode('utf-8', errors='replace')
                    self.logger.debug(f"Initial stdout data: '{decoded_data.strip()}'")
                    for line in decoded_data.splitlines(keepends=True):
                        handle.add_output(line if line.endswith('\n') else line + '\n')

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

    def _monitor_command(self, chan, handle, io_timeout, runtime_timeout, start_time):
        """Monitor command execution and handle timeouts."""
        last_data_time = time.monotonic()

        effective_select_timeout = 0.1
        if runtime_timeout is not None:
            effective_select_timeout = min(0.1, runtime_timeout / 20, 1.0)

        while not chan.exit_status_ready():
            current_time = time.monotonic()

            # Check Runtime Timeout
            if runtime_timeout is not None:
                elapsed = current_time - start_time
                if elapsed > runtime_timeout:
                    self.logger.warning(f"Command exceeded runtime timeout of {runtime_timeout}s")
                    handle.running = False
                    handle.end_ts = datetime.now(UTC)
                    try:
                        if hasattr(self.ssh_client, 'task_ops') and hasattr(self.ssh_client.task_ops, '_kill_remote_process'):
                            self.ssh_client.task_ops._kill_remote_process(handle.pid)
                        else:
                            chan.close()
                    except Exception as e_kill:
                        self.logger.warning(f"Error trying to stop process on runtime timeout: {e_kill}")
                    raise CommandRuntimeTimeout(handle, runtime_timeout)

            # Check for I/O using select
            readable, _, _ = select.select([chan], [], [], effective_select_timeout)

            if readable:
                if chan.recv_ready():
                    data = chan.recv(4096)
                    if data:
                        decoded_data = data.decode('utf-8', errors='replace')
                        self.logger.debug(f"STDOUT data: '{decoded_data.strip()}'")
                        for line in decoded_data.splitlines(keepends=True):
                            handle.add_output(line if line.endswith('\n') else line + '\n')
                        last_data_time = time.monotonic()

                if chan.recv_stderr_ready():
                    stderr_data = chan.recv_stderr(4096)
                    if stderr_data:
                        decoded_stderr = stderr_data.decode('utf-8', errors='replace')
                        self.logger.warning(f"[STDERR]: {decoded_stderr.strip()}")
                        for line in decoded_stderr.splitlines(keepends=True):
                            handle.add_stderr_output(line if line.endswith('\n') else line + '\n')
                        last_data_time = time.monotonic()

            # Check I/O Timeout
            if io_timeout:
                io_inactive_time = current_time - last_data_time
                if io_inactive_time > io_timeout:
                    self.logger.warning(f"Command I/O timeout after {io_inactive_time:.2f}s of inactivity")
                    handle.running = False
                    handle.end_ts = datetime.now(UTC)
                    try:
                        chan.close()
                    except Exception as e_kill:
                        self.logger.warning(f"Error trying to stop process on I/O timeout: {e_kill}")
                    raise CommandTimeout(io_timeout)

        # Drain remaining output
        while chan.recv_ready():
            data = chan.recv(4096)
            if not data:
                break
            for line in data.decode('utf-8', errors='replace').splitlines(keepends=True):
                handle.add_output(line if line.endswith('\n') else line + '\n')

        while chan.recv_stderr_ready():
            data = chan.recv_stderr(4096)
            if not data:
                break
            for line in data.decode('utf-8', errors='replace').splitlines(keepends=True):
                handle.add_stderr_output(line if line.endswith('\n') else line + '\n')

    def _handle_command_completion(self, chan, handle, sudo_pwd_attempted):
        """Handle successful command completion."""
        handle.exit_code = chan.recv_exit_status()
        handle.end_ts = datetime.now(UTC)
        handle.running = False
        self.logger.info(f"Command finished with exit code {handle.exit_code}")

        if handle.exit_code != 0:
            # Check for platform-specific sudo errors
            if self._check_sudo_error(handle, sudo_pwd_attempted):
                pass  # Exception already raised by _check_sudo_error

            stdout_all = handle.get_full_output()
            stderr_output = handle.get_full_stderr()
            raise CommandFailed(handle.exit_code, stdout_all, stderr_output)

        return handle

    def _handle_execution_error(self, e, handle):
        """Handle known execution errors."""
        if handle:
            handle.running = False
            handle.end_ts = datetime.now(UTC)
            if hasattr(handle, 'error_message'):
                handle.error_message = str(e)
        self.logger.error(f"Command execution error: {e}")

    def _handle_unexpected_error(self, e, handle):
        """Handle unexpected errors."""
        if handle:
            handle.running = False
            handle.end_ts = datetime.now(UTC)
            if hasattr(handle, 'error_message'):
                handle.error_message = str(e)
        self.logger.error(f"Unexpected error during command execution: {e}", exc_info=True)

    def _cleanup_command(self, chan, handle):
        """Cleanup command resources."""
        if chan:
            chan.close()
        if handle:
            if handle.running:
                handle.running = False
                if handle.end_ts is None:
                    handle.end_ts = datetime.now(UTC)
            self.logger.debug(f"Command {handle.id} cleanup complete. Final status: exit_code={handle.exit_code}, running={handle.running}")


class SshRunOperations_Linux(SshRunOperations):
    """Linux implementation of command execution using bash and sudo."""

    def _handle_sudo(self, cmd: str) -> tuple:
        """Handle sudo command preparation for Linux."""
        self.logger.info(f"Attempting passwordless sudo for: {cmd}")
        test_sudo_cmd = "sudo -n whoami"
        try:
            stdin_t, stdout_t, stderr_t = self.ssh_client._client.exec_command(test_sudo_cmd, timeout=5)
            sudo_n_stderr = stderr_t.read().decode('utf-8', errors='replace')
            sudo_n_exit_code = stdout_t.channel.recv_exit_status()
            stdin_t.close()
            stdout_t.close()
            stderr_t.close()

            if sudo_n_exit_code == 0:
                self.logger.info("Passwordless sudo successful.")
                return f"sudo -n bash -c {shlex.quote(cmd)}", False
            elif sudo_n_exit_code == 1 and ("sudo:" in sudo_n_stderr or "password is required" in sudo_n_stderr.lower()):
                if self.ssh_client.sudo_password:
                    self.logger.info("Sudo password provided. Using sudo with password.")
                    return f"sudo -S -p '' bash -c {shlex.quote(cmd)} <<< {shlex.quote(self.ssh_client.sudo_password)}", True
                else:
                    raise SudoRequired(cmd)
            else:
                raise CommandFailed(sudo_n_exit_code, "", sudo_n_stderr)
        except Exception as e:
            raise SshError(f"Failed during sudo pre-check: {e}") from e

    def _check_sudo_error(self, handle, sudo_pwd_attempted: bool) -> bool:
        """Check for Linux sudo-related errors."""
        if sudo_pwd_attempted and handle.exit_code == 1:
            stderr_output = handle.get_full_stderr()
            if "incorrect password attempt" in stderr_output.lower():
                raise SudoRequired(f"{handle.cmd} (Incorrect sudo password provided or required)")
        return False


class SshRunOperations_Win(SshRunOperations):
    """Windows implementation of command execution using PowerShell."""

    def _handle_sudo(self, cmd: str) -> tuple:
        """Handle elevation for Windows commands."""
        # Check if session is elevated (stored in ssh_client._is_elevated)
        is_elevated = getattr(self.ssh_client, '_is_elevated', False)

        if is_elevated:
            # Session is elevated, run command normally
            self.logger.info("Windows session is elevated, running command directly.")
            return cmd, False
        else:
            # Session is not elevated, raise error
            raise SshError(
                "This operation requires an elevated session. "
                "Connect with an Administrator account or run the SSH server as Administrator."
            )

    def _check_sudo_error(self, handle, sudo_pwd_attempted: bool) -> bool:
        """Check for Windows elevation-related errors."""
        # Windows doesn't have sudo password errors in the same way
        # Check for common access denied patterns
        if handle.exit_code != 0:
            stderr_output = handle.get_full_stderr().lower()
            if "access denied" in stderr_output or "requires elevation" in stderr_output:
                raise SshError(
                    "Access denied. This operation may require Administrator privileges."
                )
        return False
