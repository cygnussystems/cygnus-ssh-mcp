from __future__ import annotations
from collections import deque
import threading
from datetime import datetime, UTC
from typing import Optional, Deque, Any, List, Literal # Added List and Literal


class SshError(Exception):
    """Base exception for SSH manager errors."""


class CommandTimeout(SshError):
    """Raised for a soft timeout during command execution - either `io_timeout`
    (silence: no output for N seconds) or `wait_timeout` (N seconds of total elapsed
    wait, regardless of output activity) - see `reason`.

    The remote command is NOT killed when this is raised - monitoring is handed off
    to a background thread instead, so ssh_cmd_check_status/ssh_cmd_output eventually
    see the real output/exit code once the command actually finishes. handle carries
    the id/pid needed to check back later. handle may be None for non-command
    timeouts (e.g. waiting for a host to come back online after reboot).
    """
    def __init__(self, seconds, handle=None, reason='io_timeout'):
        ref = f" (PID: {handle.pid}, ID: {handle.id})" if handle else ""
        label = "I/O timed out after" if reason == 'io_timeout' else "wait_timeout reached after"
        super().__init__(f"Command {label} {seconds} seconds{ref}")
        self.handle = handle
        self.seconds = seconds
        self.reason = reason


class CwdNotFound(SshError):
    """Raised when an explicit cwd passed to ssh_cmd_run does not exist on the remote host.

    Fails closed: the command is NEVER executed in this case (the wrapper aborts before
    it would run), so there is no ambiguity about where anything ran.
    """
    def __init__(self, cwd):
        super().__init__(f"Working directory does not exist on remote host: {cwd}")
        self.cwd = cwd


class CommandRuntimeTimeout(SshError):
    """Raised when a command exceeds its total allowed runtime_timeout."""
    def __init__(self, handle, seconds):
        super().__init__(f"Command exceeded runtime timeout of {seconds}s (PID: {handle.pid}, ID: {handle.id})")
        self.handle = handle
        self.seconds = seconds


class CommandFailed(SshError):
    def __init__(self, exit_code, stdout, stderr):
        # Ensure stderr is string for consistent error message
        stderr_str = stderr if isinstance(stderr, str) else stderr.decode('utf-8', errors='replace')
        super().__init__(f"Command failed with exit code {exit_code}. Stderr: {stderr_str[:200]}") # Limit stderr in message
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr_str # Assign the processed stderr string
        self.handle = None  # set by the run ops when the failing command has a handle


class SudoRequired(SshError):
    def __init__(self, cmd):
        super().__init__(f"Password-less sudo required, or sudo password not provided for: {cmd}")
        self.cmd = cmd


class BusyError(SshError):
    def __init__(self):
        super().__init__("Another synchronous command (run) is currently executing")


class OutputPurged(SshError):
    def __init__(self, handle_id, first_available_line=None, stream='stdout'):
        if first_available_line is not None and first_available_line > 1:
            msg = (f"Lines 1-{first_available_line - 1} of this command's {stream} were dropped "
                   f"(over the server's output size limit) and can't be recovered. The first "
                   f"available line is {first_available_line}. For very large output, redirect "
                   f"it to a file (or use ssh_task_launch) and read that instead.")
        else:
            msg = f"Output for handle {handle_id} is no longer available"
        super().__init__(msg)
        self.handle_id = handle_id
        self.first_available_line = first_available_line


class TaskNotFound(SshError):
    # Can be raised by output(), task_status(), task_kill()
    def __init__(self, identifier):
        super().__init__(
            f"No command handle or task found with identifier: {identifier}. "
            f"Command handles only live for the current connection - reconnecting "
            f"(ssh_conn_connect) or a server restart clears them. Background tasks from "
            f"ssh_task_launch can still be checked by PID with ssh_task_status."
        )
        self.identifier = identifier


class OperationProgress:
    """Live progress of one long-running operation, shown by ssh_cmd_check_status while
    it runs: the current stage, bytes done/total for transfers, item counts (e.g. files
    searched), and when progress last changed - so "slow but moving" can be told apart
    from "stuck". Updated from the operation's worker thread, read by status polls."""

    def __init__(self):
        self._lock = threading.Lock()
        self.stage = None
        self.bytes_done = None
        self.bytes_total = None
        self.items = {}
        self.last_update = None

    def update(self, stage=None, bytes_done=None, bytes_total=None, items=None):
        with self._lock:
            if stage is not None and stage != self.stage:
                self.stage = stage
                self.bytes_done = self.bytes_total = None  # bytes belong to a stage
            if bytes_done is not None:
                self.bytes_done = bytes_done
            if bytes_total is not None:
                self.bytes_total = bytes_total
            if items:
                self.items.update(items)
            self.last_update = datetime.now(UTC)

    def snapshot(self):
        with self._lock:
            if self.last_update is None:
                return None
            info = {'stage': self.stage}
            if self.bytes_done is not None:
                info['bytes_done'] = self.bytes_done
                if self.bytes_total:
                    info['bytes_total'] = self.bytes_total
                    info['percent'] = round(100.0 * self.bytes_done / self.bytes_total, 1)
            if self.items:
                info.update(self.items)
            info['last_update'] = self.last_update.isoformat()
            info['seconds_since_update'] = round((datetime.now(UTC) - self.last_update).total_seconds(), 1)
            return info


_progress_local = threading.local()


def set_current_progress(progress):
    """Called by the server's operation wrapper in the operation's worker thread."""
    _progress_local.progress = progress


def report_progress(stage=None, bytes_done=None, bytes_total=None, items=None):
    """Report progress for the operation running in this thread, if any. Never raises -
    progress reporting must not be able to break the work it describes."""
    try:
        progress = getattr(_progress_local, 'progress', None)
        if progress is not None:
            progress.update(stage=stage, bytes_done=bytes_done, bytes_total=bytes_total, items=items)
    except Exception:
        pass


def sftp_progress_callback(done, total):
    """paramiko SFTP get/put callback: bytes so far of the current stage."""
    report_progress(bytes_done=done, bytes_total=total)


class OutputLimits:
    """Output retention limits, in characters (~bytes for ASCII output). Set once at
    startup from --max-output / --inline-output / --output-memory (or MCP_SSH_* env).

    - per_stream: how much of each command's stdout (and, separately, stderr) is kept in
      memory. Past this, the OLDEST lines are dropped - and counted, never silently.
    - inline: how much of the kept output ssh_cmd_run returns in its response (the most
      recent part); the rest can be paged with ssh_cmd_output.
    - total: memory ceiling across the whole command history. When a new command starts
      and history holds more than this, the oldest finished commands' output is released.
    """
    per_stream = 2 * 1024 * 1024
    inline = 32 * 1024
    total = 50 * 1024 * 1024


class CommandHandle:
    """Tracks the state and output of a single SSH command execution.
    For launched commands, tracks the PID.
    """
    def __init__(self, handle_id, cmd, tail_keep=None, pid=None, sudo=False, origin='user', parent_tool=None):
        self.id = handle_id
        self.cmd = cmd
        self.pid = pid
        self.sudo = sudo  # Whether this command was run with use_sudo=True - lets
                           # runtime_timeout's kill attempt (_kill_on_runtime_timeout)
                           # know it needs to elevate, since it has no other way to
                           # find out (unlike ssh_cmd_kill/ssh_task_kill, where the
                           # caller passes use_sudo explicitly on the kill call itself).
        self.origin = origin  # 'user' (default), 'tool_internal', 'connection_probe', or
                               # 'sudo_probe' - lets ssh_cmd_history distinguish commands
                               # the user actually asked for from plumbing issued internally
                               # by other tools (e.g. ssh_file_write's mv/chown/chmod dance).
        self.parent_tool = parent_tool  # Name of the MCP tool that triggered this command,
                                         # when origin != 'user' (e.g. 'ssh_file_write').
        self._tail_keep = tail_keep  # Optional line limit (None = only the size limit)
        self._max_chars = OutputLimits.per_stream  # Size limit per stream (see OutputLimits)

        self._buf = deque()         # For stdout
        self._stderr_buf = deque()  # For stderr
        self._buf_chars = 0
        self._stderr_buf_chars = 0
        self.dropped_lines = 0         # stdout lines dropped from the start (not retained)
        self.dropped_stderr_lines = 0  # same for stderr

        self._pending_stdout = ''  # Buffers an in-progress, not-yet-newline-terminated
        self._pending_stderr = ''  # line fragment across recv() chunks (see ops/run.py's
                                    # _feed_output_chunk/_flush_pending_output) so a chunk
                                    # boundary never synthesizes a fake newline mid-line.
        
        self.start_ts = datetime.now(UTC)
        self.end_ts = None
        self.exit_code = None
        self.running = True
        self.cwd = None  # Set to the confirmed directory this specific call ran in, only when
                         # an explicit cwd was passed to ssh_cmd_run (Linux/macOS). Per-call
                         # only - not remembered or carried forward to future commands.
        self.requested_cwd = None  # The raw cwd argument passed for this call, if any (for error messages)
        self.kill_confirmed = False  # True once a kill signal to the remote PID is confirmed
                                      # sent successfully (runtime_timeout's own kill, or a
                                      # later ssh_cmd_kill/ssh_cmd_check_status discovering the
                                      # process already gone) - exit_code is still unknown, but
                                      # there is nothing left to wait for.
        self._background_monitored = False  # True once io_timeout/wait_timeout hands off
                                             # ongoing monitoring of this command to a background
                                             # thread (see ops/run.py's _handoff_to_background) -
                                             # while True, this handle's channel/completion state
                                             # belongs to that thread; nothing else may close the
                                             # channel or set running/end_ts/exit_code.
        
        self.total_lines = 0        # For stdout
        self.truncated = False      # For stdout
        
        self.total_stderr_lines = 0 # For stderr
        self.stderr_truncated = False # For stderr
        
    def _clip_line(self, line):
        """A single line bigger than the whole per-stream budget keeps only its end."""
        if len(line) <= self._max_chars:
            return line
        keep = max(self._max_chars - 100, 0)
        return f"[... first {len(line) - keep} characters of this line dropped ...]" + line[-keep:]

    def _enforce_limits(self, stderr):
        buf = self._stderr_buf if stderr else self._buf
        chars = self._stderr_buf_chars if stderr else self._buf_chars
        dropped = 0
        while buf and ((self._tail_keep is not None and len(buf) > self._tail_keep)
                       or (chars > self._max_chars and len(buf) > 1)):
            chars -= len(buf.popleft())
            dropped += 1
        if stderr:
            self._stderr_buf_chars = chars
            if dropped:
                self.dropped_stderr_lines += dropped
                self.stderr_truncated = True
        else:
            self._buf_chars = chars
            if dropped:
                self.dropped_lines += dropped
                self.truncated = True

    def add_output(self, line): # Stdout
        line = self._clip_line(line)
        self._buf.append(line)
        self._buf_chars += len(line)
        self.total_lines += 1
        self._enforce_limits(stderr=False)

    def add_stderr_output(self, line): # Stderr
        line = self._clip_line(line)
        self._stderr_buf.append(line)
        self._stderr_buf_chars += len(line)
        self.total_stderr_lines += 1
        self._enforce_limits(stderr=True)

    def remove_stderr_line(self, line):
        """Remove one internal marker line from stderr, keeping the counts consistent."""
        self._stderr_buf.remove(line)
        self._stderr_buf_chars -= len(line)
        self.total_stderr_lines -= 1

    def retained_lines(self, stream='stdout'):
        """All lines still held for a stream (the most recent ones, if any were dropped)."""
        return list(self._stderr_buf if stream == 'stderr' else self._buf)

    def memory_chars(self):
        """Characters currently held for this command (both streams)."""
        return self._buf_chars + self._stderr_buf_chars

    def release_output(self):
        """Drop all retained output (memory ceiling reached) - counted as dropped."""
        self.dropped_lines += len(self._buf)
        self.dropped_stderr_lines += len(self._stderr_buf)
        if self._buf:
            self.truncated = True
        if self._stderr_buf:
            self.stderr_truncated = True
        self._buf.clear()
        self._stderr_buf.clear()
        self._buf_chars = self._stderr_buf_chars = 0
            
    def get_full_output(self): # Stdout
        return ''.join(self._buf)

    def get_full_stderr(self): # Stderr
        return ''.join(self._stderr_buf)
        
    def tail(self, n=50): # Stdout
        """Return the last n lines of output captured by run()."""
        if n <= 0:
            return []
        if n >= len(self._buf):
            return list(self._buf)
        return list(self._buf)[-n:]

    def last_nonblank(self):
        """Return the last non-blank line of output, stripped, or '' if none.

        Remote shells (PowerShell in particular, e.g. via -EncodedCommand) often
        emit a trailing blank line after the real content. A plain tail(1)[0]
        would then return that blank line instead of the actual result, silently
        breaking single-line result parsing (existence checks, counts, sizes).
        """
        for line in reversed(self._buf):
            stripped = line.strip()
            if stripped:
                return stripped
        return ''

    def tail_stderr(self, n=50): # Stderr
        """Return the last n lines of stderr captured by run()."""
        if n <= 0:
            return []
        if n >= len(self._stderr_buf):
            return list(self._stderr_buf)
        return list(self._stderr_buf)[-n:]
        
    def set_tail_keep(self, n):
        """Optional line limit on top of the size limit (None = size limit only)."""
        self._tail_keep = n
        self._enforce_limits(stderr=False)
        self._enforce_limits(stderr=True)

    def info(self):
        """Return metadata about the command."""
        return {
            'id': self.id,
            'cmd': self.cmd,
            'pid': self.pid,
            'output_lines': len(self._buf),
            'stderr_lines': len(self._stderr_buf),
            'tail_keep': self._tail_keep,
            # Timestamps are tz-aware, so isoformat() already carries the +00:00 offset
            'start_ts': self.start_ts.isoformat(),
            'end_ts': self.end_ts.isoformat() if self.end_ts else None,
            'exit_code': self.exit_code,
            'running': self.running,
            'total_lines': self.total_lines, # Stdout
            'truncated': self.truncated,   # Stdout: True if any early lines were dropped
            'dropped_lines': self.dropped_lines,
            'total_stderr_lines': self.total_stderr_lines,
            'stderr_truncated': self.stderr_truncated,
            'dropped_stderr_lines': self.dropped_stderr_lines,
            'cwd': self.cwd,
            'kill_confirmed': self.kill_confirmed,
            'sudo': self.sudo,
            'origin': self.origin,
            'parent_tool': self.parent_tool
        }

    def chunk(self, start, length=50, stream='stdout'):
        """Return `length` lines starting at zero-based index `start` (counted over ALL
        lines the command produced, including dropped ones)."""
        if start < 0:
            raise ValueError(f"Start index {start} cannot be negative")

        buf = self._stderr_buf if stream == 'stderr' else self._buf
        total = self.total_stderr_lines if stream == 'stderr' else self.total_lines
        buf_list = list(buf)
        buf_start_abs_index = max(0, total - len(buf))

        if start < buf_start_abs_index:
            raise OutputPurged(self.id, first_available_line=buf_start_abs_index + 1,
                               stream=stream)

        relative_start_idx = start - buf_start_abs_index

        if relative_start_idx >= len(buf_list):
            return []

        return buf_list[relative_start_idx : relative_start_idx + length]
