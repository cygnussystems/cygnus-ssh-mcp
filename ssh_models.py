from __future__ import annotations
from collections import deque
from datetime import datetime, UTC
from typing import Optional, Deque, Any

class SshError(Exception):
    """Base exception for SSH manager errors."""


class CommandTimeout(SshError):
    """Raised for I/O timeouts during command execution."""
    def __init__(self, seconds):
        super().__init__(f"Command I/O timed out after {seconds} seconds of inactivity")
        self.seconds = seconds


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
        self.stderr = stderr


class SudoRequired(SshError):
    def __init__(self, cmd):
        super().__init__(f"Password-less sudo required, or sudo password not provided for: {cmd}")
        self.cmd = cmd


class BusyError(SshError):
    def __init__(self):
        super().__init__("Another synchronous command (run) is currently executing")


class OutputPurged(SshError):
    def __init__(self, handle_id):
        super().__init__(f"Output for handle {handle_id} has been purged")
        self.handle_id = handle_id


class TaskNotFound(SshError):
    # Can be raised by output(), task_status(), task_kill()
    def __init__(self, identifier):
        super().__init__(f"No command handle or task found with identifier: {identifier}")
        self.identifier = identifier


class CommandHandle:
    """Tracks the state and output of a single SSH command execution.
    For launched commands, tracks the PID.
    """
    def __init__(self, handle_id, cmd, tail_keep=None, pid=None):
        self.id = handle_id
        self.cmd = cmd
        self.pid = pid
        self._buf = deque(maxlen=tail_keep)  # Use deque instead of list
        self._tail_keep = tail_keep
        self.start_ts = datetime.now(UTC)
        self.end_ts = None
        self.exit_code = None
        self.running = True
        self.total_lines = 0
        self.truncated = False
        
    def add_output(self, line):
        self._buf.append(line)
        self.total_lines += 1
        if self._tail_keep is not None and self.total_lines > self._tail_keep:
            self.truncated = True
            
    def get_full_output(self):
        return ''.join(self._buf)
        
    def tail(self, n=50):
        """Return the last n lines of output captured by run()."""
        if n <= 0:
            return []
        # If n is greater than buffer size, return all available lines
        if n >= len(self._buf):
            return list(self._buf)
        # Otherwise return the last n lines
        return list(self._buf)[-n:]
        
    def set_tail_keep(self, n):
        self._tail_keep = n
        if n is not None:
            # Create a new buffer with the new size
            old_buf = list(self._buf)
            self._buf = deque(maxlen=n)
            # Copy the last n lines from the old buffer
            for line in old_buf[-min(n, len(old_buf)):]:
                self._buf.append(line)
            
    def info(self):
        """Return metadata about the command."""
        return {
            'id': self.id,
            'cmd': self.cmd,
            'pid': self.pid,
            'output_lines': len(self._buf),
            'tail_keep': self._tail_keep,
            'start_ts': self.start_ts.isoformat() + 'Z',
            'end_ts': self.end_ts.isoformat() + 'Z' if self.end_ts else None,
            'exit_code': self.exit_code,
            'running': self.running,
            'total_lines': self.total_lines,
            'truncated': self.truncated
        }

    def chunk(self, start, length=50):
        """Return `length` lines starting at zero-based index `start` from run()."""
        # Output chunking works for run() commands.
        if start < 0: # Allow start=0 even if total_lines is 0
             raise ValueError(f"Start index {start} cannot be negative")

        # Convert deque to list for easier slicing
        buf_list = list(self._buf)
        
        # For a buffer with maxlen=10 that has 10 items (Lines 96-105),
        # if we request start=95, we should raise OutputPurged
        # if we request start=96, we should return the first item in the buffer
        
        # Calculate the absolute index of the first element currently in the buffer
        # For a command that generated 105 lines with a buffer of 10 lines,
        # the first line in the buffer would be at index 95 (105-10)
        buf_start_abs_index = max(0, self.total_lines - len(self._buf))
        
        # Debug output to help diagnose issues
        # print(f"Buffer start abs index: {buf_start_abs_index}, requested start: {start}")
        # print(f"Total lines: {self.total_lines}, buffer length: {len(self._buf)}")
        
        if start < buf_start_abs_index:
            # Requested start index is before the first line currently stored
            raise OutputPurged(self.id)
            
        # Calculate the index relative to the start of the current buffer
        relative_start_idx = start - buf_start_abs_index
        
        # Check if the relative index is within the buffer bounds
        if relative_start_idx >= len(buf_list):
            # Requested start index is beyond the end of the buffer
            return []
            
        return buf_list[relative_start_idx : relative_start_idx + length]
