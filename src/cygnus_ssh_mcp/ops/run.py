from collections import deque
import time
import select
import shlex
import logging
import socket
import base64
import threading
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional, Self
from datetime import UTC
from cygnus_ssh_mcp.models import (
    CommandHandle, CommandTimeout, CommandRuntimeTimeout,
    CommandFailed, SudoRequired, SshError, BusyError, CwdNotFound
)
from cygnus_ssh_mcp.ps_encode import powershell_encoded_command


class SshRunOperations(ABC):
    """Base class for synchronous command execution. Platform-specific sudo handling is abstract."""

    # Internal safety ceiling for background-monitored commands (io_timeout/wait_timeout
    # handoff) that were never given an explicit runtime_timeout - without this, a command
    # that hangs forever with no runtime_timeout would leave its background monitoring
    # thread running forever too. This is deliberately generous (a real ceiling, not a UX
    # timeout) - callers who care about a tighter bound should pass runtime_timeout themselves.
    MAX_BACKGROUND_RUNTIME_SECONDS = 24 * 3600

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

    CWD_INVALID_MARKER = None  # Set by subclasses that support the explicit cwd param (see _Linux)
    CWD_INVALID_EXIT_CODE = 77

    def _wrap_for_explicit_cwd(self, cmd: str, cwd: Optional[str]) -> str:
        """Wrap cmd to run inside cwd for THIS call only - fails closed: if cwd
        doesn't exist, cmd never runs at all (no ambiguity about where anything ran).
        No state is remembered across calls. No-op if cwd is None.

        Not implemented for Windows by default - there's no single reliable target
        shell to wrap against (cmd.exe vs PowerShell DefaultShell ambiguity), see
        docs_internal/CMD-EXECUTION-MODEL.md. Subclasses that do support it should
        set CWD_INVALID_MARKER and override this.
        """
        if cwd is None:
            return cmd
        raise SshError(
            "The cwd parameter is not supported on this platform. Use an absolute "
            "path in the command itself, or chain e.g. \"cd 'dir' && <command>\"."
        )

    def _is_cwd_invalid(self, handle) -> bool:
        """True if this call's failure was our own fail-closed cwd guard tripping,
        not the user's command. Checked via a distinct marker in stderr, gated
        behind a reserved exit code, to avoid mistaking a command's own legitimate
        use of that exit code for a cwd failure.
        """
        if not self.CWD_INVALID_MARKER:
            return False
        return (
            handle.exit_code == self.CWD_INVALID_EXIT_CODE
            and self.CWD_INVALID_MARKER in handle.get_full_stderr()
        )

    def _feed_output_chunk(self, handle, chunk, is_stderr=False):
        """Decode a raw recv() chunk (or accept an already-decoded str) and emit
        only genuinely newline-terminated lines to the handle, buffering any
        trailing partial line (handle._pending_stdout/_pending_stderr) until a
        later chunk completes it or _flush_pending_output is called. Never
        synthesizes a fake newline onto a fragment that just hasn't finished
        arriving yet - the root cause of the character-split output bug that
        this method (and every recv() call site) now routes through.
        """
        if not chunk:
            return
        text = chunk.decode('utf-8', errors='replace') if isinstance(chunk, (bytes, bytearray)) else chunk
        pending_attr = '_pending_stderr' if is_stderr else '_pending_stdout'
        add = handle.add_stderr_output if is_stderr else handle.add_output
        text = getattr(handle, pending_attr) + text
        lines = text.splitlines(keepends=True)
        if lines and not lines[-1].endswith('\n'):
            setattr(handle, pending_attr, lines.pop())
        else:
            setattr(handle, pending_attr, '')
        for line in lines:
            add(line)

    def _flush_pending_output(self, handle):
        """Emit whatever partial line is still buffered for either stream, as-is
        (no synthetic newline added - correctly handles output that just doesn't
        end in a newline, e.g. a prompt). Called once a command is confirmed
        fully done. Safe to call more than once (no-op once both buffers are
        empty).
        """
        if handle._pending_stdout:
            handle.add_output(handle._pending_stdout)
            handle._pending_stdout = ''
        if handle._pending_stderr:
            handle.add_stderr_output(handle._pending_stderr)
            handle._pending_stderr = ''

    PID_MARKER = None  # Set by subclasses that can capture a real remote PID (see _Linux)

    def _wrap_for_pid_capture(self, cmd: str) -> str:
        """Wrap cmd to report its own real remote PID as the first thing it does.

        No-op by default - handle.pid then stays paramiko's local channel id
        (see _capture_pid), which is NOT a real remote PID and cannot be used to
        signal/kill the remote process. Not implemented for Windows; see
        docs_internal/CMD-EXECUTION-MODEL.md.
        """
        return cmd

    def execute_command(self, cmd: str, io_timeout: float = 60.0,
                        runtime_timeout: Optional[float] = None,
                        sudo: bool = False, cwd: Optional[str] = None,
                        wait_timeout: Optional[float] = None,
                        origin: str = 'user', parent_tool: Optional[str] = None) -> CommandHandle:
        """
        Execute a command synchronously with timeout management.

        Args:
            cmd: Command to execute
            io_timeout: I/O inactivity (silence) timeout in seconds
            runtime_timeout: Total execution timeout in seconds - the only knob that
                ever kills the remote command
            sudo: Whether to run with elevated privileges
            cwd: Run in this directory for this call only (Linux/macOS). Fails closed -
                the command never runs if the directory doesn't exist. Not remembered
                across calls; see docs_internal/CMD-EXECUTION-MODEL.md.
            wait_timeout: Total elapsed wait in seconds, regardless of output activity -
                unlike io_timeout, fires even if the command is actively producing
                output. Same non-killing handoff behavior as io_timeout.
            origin: 'user' (default) for a directly user-requested command, or an
                internal-plumbing label ('tool_internal', 'connection_probe',
                'sudo_probe') for helper commands issued by other tools - see
                ssh_cmd_history's include_internal filter.
            parent_tool: Name of the MCP tool that triggered this command, when
                origin != 'user'.

        Returns:
            CommandHandle with command results

        Raises:
            CommandTimeout: If io_timeout or wait_timeout fires (see .reason)
            CommandRuntimeTimeout: If runtime timeout occurs
            CommandFailed: If command fails
            SudoRequired: If elevation is required but not available
            CwdNotFound: If cwd was given and doesn't exist on the remote host
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
            handle = self._create_command_handle(cmd, sudo=sudo, origin=origin, parent_tool=parent_tool)
            handle.requested_cwd = cwd

            # Handle sudo/elevation if needed
            if sudo:
                cmd, sudo_pwd_attempted = self._handle_sudo(cmd)

            # Explicit, per-call working directory - fails closed, nothing remembered
            cmd = self._wrap_for_explicit_cwd(cmd, cwd)

            # Report a real remote PID as the first thing the command does, so
            # runtime_timeout/ssh_cmd_kill can actually signal the right process
            # (outermost wrap - must run before any cwd-guard/sudo wrapping)
            cmd = self._wrap_for_pid_capture(cmd)

            # Execute command and capture PID
            chan = self._execute_command(cmd, io_timeout)
            self._capture_pid(chan, handle)

            # Monitor command execution
            self._monitor_command(chan, handle, io_timeout, runtime_timeout, start_time,
                                   wait_timeout=wait_timeout, sudo_pwd_attempted=sudo_pwd_attempted)

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

    def _create_command_handle(self, cmd, sudo=False, origin='user', parent_tool=None):
        """Create and track a new CommandHandle."""
        handle = self.ssh_client.history_manager.add_command(cmd, sudo=sudo, origin=origin, parent_tool=parent_tool)
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
                    self.logger.debug(f"Initial stdout data: '{data.decode('utf-8', errors='replace').strip()}'")
                    self._feed_output_chunk(handle, data, is_stderr=False)

            if chan.recv_stderr_ready():
                data_stderr = chan.recv_stderr(4096)
                if data_stderr:
                    self.logger.debug(f"Initial stderr data: '{data_stderr.decode('utf-8', errors='replace').strip()}'")
                    self._feed_output_chunk(handle, data_stderr, is_stderr=True)

        except socket.timeout:
            self.logger.debug("Timeout reading initial stdout/stderr (expected for some commands)")
        except Exception as e:
            self.logger.warning(f"Error during initial output capture: {e}")

    def _monitor_command(self, chan, handle, io_timeout, runtime_timeout, start_time,
                          wait_timeout=None, sudo_pwd_attempted=False):
        """Monitor command execution and handle timeouts.

        io_timeout (silence) and wait_timeout (total elapsed, regardless of activity)
        are both "soft" - neither kills the remote command. Whichever fires first hands
        monitoring off to a background thread (_handoff_to_background) instead of
        closing the channel, so the remote command genuinely survives and
        ssh_cmd_check_status/ssh_cmd_output can see real output/exit code later.
        runtime_timeout is the only "hard" timeout - it always kills.
        """
        last_data_time = time.monotonic()

        effective_select_timeout = 0.1
        if runtime_timeout is not None:
            effective_select_timeout = min(0.1, runtime_timeout / 20, 1.0)

        while not chan.exit_status_ready():
            current_time = time.monotonic()

            # Check Runtime Timeout (hard cap - the only one allowed to kill)
            if runtime_timeout is not None:
                elapsed = current_time - start_time
                if elapsed > runtime_timeout:
                    self.logger.warning(f"Command exceeded runtime timeout of {runtime_timeout}s")
                    self._kill_on_runtime_timeout(chan, handle)
                    raise CommandRuntimeTimeout(handle, runtime_timeout)

            # Check for I/O using select
            readable, _, _ = select.select([chan], [], [], effective_select_timeout)

            if readable:
                if chan.recv_ready():
                    data = chan.recv(4096)
                    if data:
                        self.logger.debug(f"STDOUT data: '{data.decode('utf-8', errors='replace').strip()}'")
                        self._feed_output_chunk(handle, data, is_stderr=False)
                        last_data_time = time.monotonic()

                if chan.recv_stderr_ready():
                    stderr_data = chan.recv_stderr(4096)
                    if stderr_data:
                        self.logger.warning(f"[STDERR]: {stderr_data.decode('utf-8', errors='replace').strip()}")
                        self._feed_output_chunk(handle, stderr_data, is_stderr=True)
                        last_data_time = time.monotonic()

            # Check I/O Timeout (silence-based, soft - hands off, does not kill)
            if io_timeout:
                io_inactive_time = current_time - last_data_time
                if io_inactive_time > io_timeout:
                    self.logger.warning(f"Command I/O timeout after {io_inactive_time:.2f}s of inactivity "
                                         f"- handing off to background monitoring")
                    self._handoff_to_background(chan, handle, runtime_timeout, start_time,
                                                 sudo_pwd_attempted, 'io_timeout', io_timeout)

            # Check wait_timeout (elapsed-based, soft - fires even if actively producing
            # output, unlike io_timeout - hands off the same way, does not kill)
            if wait_timeout:
                total_elapsed = current_time - start_time
                if total_elapsed > wait_timeout:
                    self.logger.warning(f"Command wait_timeout of {wait_timeout}s reached "
                                         f"- handing off to background monitoring")
                    self._handoff_to_background(chan, handle, runtime_timeout, start_time,
                                                 sudo_pwd_attempted, 'wait_timeout', wait_timeout)

        self._drain_remaining_output(chan, handle)

    def _drain_remaining_output(self, chan, handle):
        """Read whatever's left in the channel's buffers after it's confirmed
        done, then flush any still-pending partial line (no synthetic newline
        added). Covers both completion paths that call this method:
        _monitor_command's synchronous loop and _continue_monitoring_in_background's
        daemon-thread loop.
        """
        while chan.recv_ready():
            data = chan.recv(4096)
            if not data:
                break
            self._feed_output_chunk(handle, data, is_stderr=False)

        while chan.recv_stderr_ready():
            data = chan.recv_stderr(4096)
            if not data:
                break
            self._feed_output_chunk(handle, data, is_stderr=True)

        self._flush_pending_output(handle)

    def _kill_on_runtime_timeout(self, chan, handle):
        """Kill the remote process and close the channel - the only path in this whole
        flow allowed to do either, since runtime_timeout is the hard safety net.
        Shared by the foreground loop and background-monitoring loop.
        """
        handle.end_ts = datetime.now(UTC)
        try:
            if hasattr(self.ssh_client, 'task_ops') and hasattr(self.ssh_client.task_ops, '_kill_remote_process'):
                # Elevate the kill itself when the timed-out command was sudo'd -
                # otherwise this never has permission to reach sudo's real (often
                # root-owned) child at all, regardless of PID/group targeting.
                # handle.sudo is the only way this path can know that (unlike
                # ssh_cmd_kill/ssh_task_kill, where the caller passes use_sudo
                # explicitly on the kill call itself).
                killed = self.ssh_client.task_ops._kill_remote_process(handle.pid, sudo=handle.sudo)
                if killed:
                    # We don't know the real exit code (the channel is closed below
                    # without waiting for it), but the kill signal was confirmed
                    # delivered - nothing is left to wait for, so ssh_cmd_check_status
                    # should stop reporting this as merely "unknown_still_running" forever.
                    handle.kill_confirmed = True
        except Exception as e_kill:
            self.logger.warning(f"Error trying to stop process on runtime timeout: {e_kill}")
        finally:
            # This path never calls _drain_remaining_output (the channel is being
            # killed, not drained) - flush whatever partial line was pending at
            # this instant explicitly, or it would silently be dropped.
            self._flush_pending_output(handle)
            try:
                chan.close()
            except Exception as e_close:
                self.logger.warning(f"Error closing channel on runtime timeout: {e_close}")
            handle.running = False

    def _handoff_to_background(self, chan, handle, runtime_timeout, start_time,
                                sudo_pwd_attempted, reason, seconds):
        """Hand off monitoring of a still-running remote command to a background
        thread, instead of closing the channel - the whole point of io_timeout/
        wait_timeout is that the remote command survives, so nothing in this call's
        stack may touch the channel again after this. Always raises CommandTimeout;
        never returns normally.
        """
        handle._background_monitored = True
        thread = threading.Thread(
            target=self._continue_monitoring_in_background,
            args=(chan, handle, runtime_timeout, start_time, sudo_pwd_attempted),
            daemon=True,
            name=f"ssh-cmd-bg-monitor-{handle.id}"
        )
        thread.start()
        raise CommandTimeout(seconds, handle=handle, reason=reason)

    def _continue_monitoring_in_background(self, chan, handle, runtime_timeout, start_time,
                                            sudo_pwd_attempted):
        """Runs in a daemon thread after io_timeout/wait_timeout hands off monitoring.
        Keeps draining the channel and watching for real completion, so
        ssh_cmd_check_status/ssh_cmd_output eventually see the real exit code and full
        output - without ever closing the channel early. Only runtime_timeout (still
        enforced here, with an internal ceiling if the caller never set one) may end
        this early. handle.running/.end_ts/.exit_code are set in that order (running
        last) so a concurrent reader never observes "not running" alongside stale
        exit info - deliberately not using a lock, consistent with how the rest of
        this handle is already accessed across the sync/async boundary elsewhere.
        """
        effective_runtime_timeout = (
            runtime_timeout if runtime_timeout is not None else self.MAX_BACKGROUND_RUNTIME_SECONDS
        )
        try:
            while not chan.exit_status_ready():
                current_time = time.monotonic()
                if current_time - start_time > effective_runtime_timeout:
                    self.logger.warning(
                        f"Backgrounded command {handle.id} exceeded runtime timeout of "
                        f"{effective_runtime_timeout}s"
                    )
                    self._kill_on_runtime_timeout(chan, handle)
                    return

                readable, _, _ = select.select([chan], [], [], 1.0)
                if readable:
                    if chan.recv_ready():
                        data = chan.recv(4096)
                        if data:
                            self._feed_output_chunk(handle, data, is_stderr=False)
                    if chan.recv_stderr_ready():
                        stderr_data = chan.recv_stderr(4096)
                        if stderr_data:
                            self._feed_output_chunk(handle, stderr_data, is_stderr=True)

            self._drain_remaining_output(chan, handle)

            try:
                # Sets handle.exit_code/end_ts (platform-specific recovery, e.g. the
                # Windows exit-code marker) as its first action, before possibly
                # raising CommandFailed (nonzero exit) or CwdNotFound (never actually
                # possible here - cwd is validated before io_timeout/wait_timeout could
                # ever fire) - nobody is waiting synchronously for those anymore, so
                # just log them; the handle state they set is what matters now.
                self._handle_command_completion(chan, handle, sudo_pwd_attempted)
            except Exception as completion_err:
                self.logger.info(
                    f"Backgrounded command {handle.id} finished: {completion_err}"
                )
            finally:
                handle.running = False
                try:
                    chan.close()
                except Exception:
                    pass
        except Exception as e:
            self.logger.error(
                f"Error in background monitoring for command {handle.id}: {e}", exc_info=True
            )
            if handle.end_ts is None:
                handle.end_ts = datetime.now(UTC)
            handle.running = False
            try:
                chan.close()
            except Exception:
                pass

    def _handle_command_completion(self, chan, handle, sudo_pwd_attempted):
        """Handle successful command completion."""
        handle.exit_code = chan.recv_exit_status()
        handle.end_ts = datetime.now(UTC)
        handle.running = False
        self.logger.info(f"Command finished with exit code {handle.exit_code}")

        if self._is_cwd_invalid(handle):
            # Fail closed: the wrapper aborted before the user's command ever ran.
            # The handle so far only reflects the cwd-guard wrapper's own PID/exit
            # code (77), not anything the user's command did - remove it from
            # history so it can't be mistaken for a real execution (the caller was
            # never even given this handle's id, since the cwd_not_found response
            # doesn't include one).
            self.ssh_client.history_manager.remove_command(handle.id)
            raise CwdNotFound(handle.requested_cwd)

        if handle.requested_cwd is not None:
            # Confirmed: the command actually ran in the requested directory.
            handle.cwd = handle.requested_cwd

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
        if handle and not handle._background_monitored:
            handle.running = False
            handle.end_ts = datetime.now(UTC)
            if hasattr(handle, 'error_message'):
                handle.error_message = str(e)
        self.logger.error(f"Command execution error: {e}")

    def _handle_unexpected_error(self, e, handle):
        """Handle unexpected errors."""
        if handle and not handle._background_monitored:
            handle.running = False
            handle.end_ts = datetime.now(UTC)
            if hasattr(handle, 'error_message'):
                handle.error_message = str(e)
        self.logger.error(f"Unexpected error during command execution: {e}", exc_info=True)

    def _cleanup_command(self, chan, handle):
        """Cleanup command resources."""
        if handle is not None and handle._background_monitored:
            # Ownership of the channel and completion state was handed off to a
            # background thread (io_timeout/wait_timeout) - it owns both now. Touching
            # either here would race with it and/or re-introduce the exact bug this
            # mechanism exists to fix (closing a channel whose remote command is
            # supposed to survive).
            return
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

    CWD_INVALID_MARKER = '___SSH_MCP_CWD_INVALID___'

    def _wrap_for_explicit_cwd(self, cmd: str, cwd: Optional[str]) -> str:
        """Run cmd inside cwd for this call only, failing closed: if cwd doesn't
        exist, cmd is never executed at all - the wrapper exits immediately with
        a reserved code plus a distinct stderr marker, checked by _is_cwd_invalid.
        No state is remembered across calls.
        """
        if cwd is None:
            return cmd
        quoted = shlex.quote(cwd)
        return (
            f"cd -- {quoted} 2>/dev/null || {{ "
            f"echo {self.CWD_INVALID_MARKER} 1>&2; exit {self.CWD_INVALID_EXIT_CODE}; }}\n"
            f"{cmd}\n"
        )

    PID_MARKER = '___SSH_MCP_PID___'
    PID_CAPTURE_TIMEOUT = 3.0  # seconds to wait for the marker before falling back

    def _wrap_for_pid_capture(self, cmd: str) -> str:
        """Print the wrapper shell's own PID to stderr as the very first thing,
        before anything else runs (including any cwd-guard or the user's command).
        _capture_pid reads this and uses it as handle.pid instead of paramiko's
        local channel id, so runtime_timeout/ssh_cmd_kill can actually target a
        real process on the remote host.
        """
        return f"printf '{self.PID_MARKER}%s\\n' \"$$\" 1>&2\n{cmd}\n"

    def _capture_pid(self, chan, handle):
        """Capture the real remote PID via the marker _wrap_for_pid_capture prints
        first, instead of paramiko's local channel id. Falls back to the channel
        id (degraded - kill/status by PID won't target the right process, but the
        call still proceeds) if the marker doesn't arrive within PID_CAPTURE_TIMEOUT.

        Stdout is fed through _feed_output_chunk incrementally as it arrives (no
        marker to look for there). Stderr accumulates in a raw local buffer only
        until the very first line is resolved (found to be the marker, or not) -
        after that point (or if the marker never arrives at all), remaining
        stderr is fed through the same incremental path as everything else, so
        an in-progress last line never gets a synthetic newline.
        """
        handle.pid = chan.get_id()  # fallback default; overwritten below if marker found
        stderr_prefix = ''  # raw bytes not yet resolved as marker-or-not
        deadline = time.monotonic() + self.PID_CAPTURE_TIMEOUT
        marker_found = False
        marker_resolved = False  # True once the first stderr line has been checked

        try:
            while time.monotonic() < deadline and not marker_found:
                if chan.exit_status_ready() and not chan.recv_ready() and not chan.recv_stderr_ready():
                    break
                readable, _, _ = select.select([chan], [], [], 0.1)
                if not readable:
                    continue
                if chan.recv_ready():
                    data = chan.recv(4096)
                    if data:
                        self._feed_output_chunk(handle, data, is_stderr=False)
                if chan.recv_stderr_ready():
                    data_stderr = chan.recv_stderr(4096)
                    if data_stderr:
                        if marker_resolved:
                            self._feed_output_chunk(handle, data_stderr, is_stderr=True)
                            continue
                        stderr_prefix += data_stderr.decode('utf-8', errors='replace')
                        if '\n' in stderr_prefix:
                            first_line, _, rest = stderr_prefix.partition('\n')
                            if first_line.startswith(self.PID_MARKER):
                                pid_str = first_line[len(self.PID_MARKER):].strip()
                                if pid_str.isdigit():
                                    handle.pid = int(pid_str)
                                    marker_found = True
                                marker_resolved = True
                                stderr_prefix = ''
                                if rest:
                                    self._feed_output_chunk(handle, rest, is_stderr=True)
        except Exception as e:
            self.logger.warning(f"Error during PID marker capture: {e}")

        if marker_found:
            self.logger.info(f"Captured real remote PID: {handle.pid}")
        else:
            self.logger.warning(
                f"PID marker not received within {self.PID_CAPTURE_TIMEOUT}s, falling back to "
                f"channel id {handle.pid} (kill/status by PID will not target the right process)"
            )

        # Whatever never got resolved as a marker-or-not line (timed out before
        # any newline arrived, or the very first line simply wasn't the marker)
        # still needs to reach the handle.
        if stderr_prefix:
            self._feed_output_chunk(handle, stderr_prefix, is_stderr=True)

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

    PID_MARKER = '___SSH_MCP_PID___'
    PID_CAPTURE_TIMEOUT = 8.0  # PowerShell + .NET Process startup is slower than a bash printf
    EXIT_CODE_MARKER = '___SSH_MCP_EXITCODE___'

    # Placeholders substituted via str.replace() in _wrap_for_pid_capture - avoids
    # fighting Python f-string brace-escaping against PowerShell's own {} script blocks.
    # __CMD_B64__ is base64 of the raw command's UTF-8 bytes, decoded at runtime -
    # NOT string-escaped/interpolated into the script text. A command containing its
    # own nested quoting (e.g. 'powershell -Command "Start-Sleep ...; Write-Output
    # ...'x'..."') cannot survive being escaped-and-embedded as PS/cmd.exe literal text
    # (verified live 2026-07-03: it silently mis-parsed into literally echoing the
    # argument text instead of running it) - decoding a base64 blob at runtime sidesteps
    # that whole class of bug, since base64's alphabet has no shell/PS metacharacters.
    #
    # The exit-code marker (printed right before `exit $proc.ExitCode`) exists because
    # the channel's own exit status cannot be trusted on Windows: Win32-OpenSSH flattens
    # a nested child process's real exit code to 1 whenever DefaultShell=cmd.exe wraps
    # the exec payload (verified live 2026-07-03, see planning docs) - and that's exactly
    # what this wrapper is from OpenSSH's point of view, a nested child reporting its own
    # code via `exit $proc.ExitCode`. _handle_command_completion recovers the real value
    # from this marker instead of chan.recv_exit_status().
    _PID_CAPTURE_SCRIPT_TEMPLATE = r"""
$ErrorActionPreference = 'Stop'
$__cmdText = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('__CMD_B64__'))
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = 'cmd.exe'
$psi.Arguments = '/c ' + $__cmdText
$psi.UseShellExecute = $false
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$psi.CreateNoWindow = $true
$proc = New-Object System.Diagnostics.Process
$proc.StartInfo = $psi
$proc.EnableRaisingEvents = $true
$stdoutAction = { if ($EventArgs.Data -ne $null) { [Console]::Out.WriteLine($EventArgs.Data) } }
$stderrAction = { if ($EventArgs.Data -ne $null) { [Console]::Error.WriteLine($EventArgs.Data) } }
Register-ObjectEvent -InputObject $proc -EventName OutputDataReceived -Action $stdoutAction | Out-Null
Register-ObjectEvent -InputObject $proc -EventName ErrorDataReceived -Action $stderrAction | Out-Null
[void]$proc.Start()
[Console]::Error.WriteLine('__PID_MARKER__' + $proc.Id)
[Console]::Error.Flush()
$proc.BeginOutputReadLine()
$proc.BeginErrorReadLine()
while (-not $proc.HasExited) {
    Start-Sleep -Milliseconds 50
}
$proc.WaitForExit()
Start-Sleep -Milliseconds 150
[Console]::Error.WriteLine('__EXITCODE_MARKER__' + $proc.ExitCode)
[Console]::Error.Flush()
exit $proc.ExitCode
"""

    def _wrap_for_pid_capture(self, cmd: str) -> str:
        """Spawn cmd's real child process via System.Diagnostics.Process (not
        Start-Process, which only supports file-based redirection) so output can
        still stream live while exposing a real Windows PID - needed for
        runtime_timeout/ssh_cmd_kill to signal the right process. Always invoked
        via 'powershell -EncodedCommand' (see ps_encode.py) so this works
        regardless of whether the SSH server's DefaultShell is cmd.exe or
        PowerShell, and regardless of anything in cmd itself.

        Streaming caveat: PowerShell only runs Process's OutputDataReceived/
        ErrorDataReceived -Action handlers while the engine is idle, which a
        blocking WaitForExit() call up front would prevent - so this polls
        HasExited in a sleep loop instead, matching the standard PowerShell
        idiom for combining Register-ObjectEvent with a synchronous process wait.
        """
        cmd_b64 = base64.b64encode(cmd.encode('utf-8')).decode('ascii')

        script = self._PID_CAPTURE_SCRIPT_TEMPLATE
        script = script.replace('__CMD_B64__', cmd_b64)
        script = script.replace('__PID_MARKER__', self.PID_MARKER)
        script = script.replace('__EXITCODE_MARKER__', self.EXIT_CODE_MARKER)
        return powershell_encoded_command(script)

    def _capture_pid(self, chan, handle):
        """Capture the real remote Windows PID via the marker _wrap_for_pid_capture
        prints to stderr right after starting the child process, instead of
        paramiko's local channel id. Falls back to the channel id (degraded -
        kill/status by PID won't target the right process, but the call still
        proceeds) if the marker doesn't arrive within PID_CAPTURE_TIMEOUT.

        Unlike Linux, the marker isn't necessarily the very first stderr line
        (PowerShell startup noise can precede it) - so this keeps scanning
        complete lines until the marker is found, forwarding every non-marker
        line through the same incremental path as everything else, only once
        it's genuinely newline-terminated (never a synthetic newline on a
        still-arriving fragment).
        """
        handle.pid = chan.get_id()  # fallback default; overwritten below if marker found
        stderr_buf = ''
        deadline = time.monotonic() + self.PID_CAPTURE_TIMEOUT
        marker_found = False

        try:
            while time.monotonic() < deadline and not marker_found:
                if chan.exit_status_ready() and not chan.recv_ready() and not chan.recv_stderr_ready():
                    break
                readable, _, _ = select.select([chan], [], [], 0.1)
                if not readable:
                    continue
                if chan.recv_ready():
                    data = chan.recv(4096)
                    if data:
                        self._feed_output_chunk(handle, data, is_stderr=False)
                if chan.recv_stderr_ready():
                    data_stderr = chan.recv_stderr(4096)
                    if data_stderr:
                        stderr_buf += data_stderr.decode('utf-8', errors='replace')
                        while '\n' in stderr_buf and not marker_found:
                            line, _, rest = stderr_buf.partition('\n')
                            if self.PID_MARKER in line:
                                pid_str = line[line.index(self.PID_MARKER) + len(self.PID_MARKER):].strip()
                                if pid_str.isdigit():
                                    handle.pid = int(pid_str)
                                    marker_found = True
                                stderr_buf = rest
                            else:
                                # Not the marker line (e.g. PowerShell startup noise) -
                                # forward it now that it's genuinely complete.
                                self._feed_output_chunk(handle, line + '\n', is_stderr=True)
                                stderr_buf = rest
        except Exception as e:
            self.logger.warning(f"Error during PID marker capture: {e}")

        if marker_found:
            self.logger.info(f"Captured real remote PID: {handle.pid}")
        else:
            self.logger.warning(
                f"PID marker not received within {self.PID_CAPTURE_TIMEOUT}s, falling back to "
                f"channel id {handle.pid} (kill/status by PID will not target the right process)"
            )

        # Whatever's left - a trailing partial line not yet newline-terminated,
        # or (if the marker never arrived at all) everything read - still needs
        # to reach the handle.
        if stderr_buf:
            self._feed_output_chunk(handle, stderr_buf, is_stderr=True)

    def _extract_and_strip_exit_code_marker(self, handle) -> Optional[int]:
        """Find the real exit code marker in the handle's captured stderr, remove
        that line so it never reaches the user, and return the parsed value - or
        None if the marker isn't present (e.g. the wrapper crashed before reaching
        it, in which case the caller should fall back to the channel exit status).
        """
        for line in list(handle._stderr_buf):
            stripped = line.strip()
            if self.EXIT_CODE_MARKER in stripped:
                code_str = stripped[stripped.index(self.EXIT_CODE_MARKER) + len(self.EXIT_CODE_MARKER):].strip()
                if code_str.lstrip('-').isdigit():
                    handle._stderr_buf.remove(line)
                    return int(code_str)
        return None

    def _handle_command_completion(self, chan, handle, sudo_pwd_attempted):
        """Handle successful command completion.

        Overridden from the base implementation: chan.recv_exit_status() cannot be
        trusted on Windows. Verified live 2026-07-03 (see planning docs) that
        Win32-OpenSSH flattens a nested child process's real exit code to 1
        whenever DefaultShell=cmd.exe wraps the exec payload - and that's exactly
        what our own PowerShell wrapper is from OpenSSH's point of view, a nested
        child reporting its own code via `exit $proc.ExitCode`. The wrapper prints
        the real exit code to stderr with a marker right before exiting; this
        recovers it from there instead, falling back to the (unreliable) channel
        value only if the marker never arrives at all.
        """
        channel_exit_code = chan.recv_exit_status()
        marker_exit_code = self._extract_and_strip_exit_code_marker(handle)

        if marker_exit_code is not None:
            handle.exit_code = marker_exit_code
            if marker_exit_code != channel_exit_code:
                self.logger.info(
                    f"Channel exit status ({channel_exit_code}) differs from the real "
                    f"exit code recovered via marker ({marker_exit_code}) - using the "
                    f"marker value (known Win32-OpenSSH nested-exit-code flattening bug)"
                )
        else:
            handle.exit_code = channel_exit_code
            self.logger.warning(
                f"Exit code marker not found in stderr - falling back to channel exit "
                f"status {channel_exit_code}, which is unreliable on Windows"
            )

        handle.end_ts = datetime.now(UTC)
        handle.running = False
        self.logger.info(f"Command finished with exit code {handle.exit_code}")

        if self._is_cwd_invalid(handle):
            # See the base class's identical branch for why this is removed from
            # history rather than left looking like a real execution.
            self.ssh_client.history_manager.remove_command(handle.id)
            raise CwdNotFound(handle.requested_cwd)

        if handle.requested_cwd is not None:
            handle.cwd = handle.requested_cwd

        if handle.exit_code != 0:
            if self._check_sudo_error(handle, sudo_pwd_attempted):
                pass

            stdout_all = handle.get_full_output()
            stderr_output = handle.get_full_stderr()
            raise CommandFailed(handle.exit_code, stdout_all, stderr_output)

        return handle

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
