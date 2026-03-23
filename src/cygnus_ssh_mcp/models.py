from __future__ import annotations
from collections import deque
from datetime import datetime, UTC
from typing import Optional, Deque, Any, List, Literal # Added List and Literal


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
        self.stderr = stderr_str # Assign the processed stderr string


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
        self._tail_keep = tail_keep
        
        self._buf = deque(maxlen=self._tail_keep)      # For stdout
        self._stderr_buf = deque(maxlen=self._tail_keep) # For stderr
        
        self.start_ts = datetime.now(UTC)
        self.end_ts = None
        self.exit_code = None
        self.running = True
        
        self.total_lines = 0        # For stdout
        self.truncated = False      # For stdout
        
        self.total_stderr_lines = 0 # For stderr
        self.stderr_truncated = False # For stderr
        
    def add_output(self, line): # Stdout
        self._buf.append(line)
        self.total_lines += 1
        if self._tail_keep is not None and self.total_lines > self._tail_keep:
            self.truncated = True

    def add_stderr_output(self, line): # Stderr
        self._stderr_buf.append(line)
        self.total_stderr_lines += 1
        if self._tail_keep is not None and self.total_stderr_lines > self._tail_keep:
            self.stderr_truncated = True
            
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

    def tail_stderr(self, n=50): # Stderr
        """Return the last n lines of stderr captured by run()."""
        if n <= 0:
            return []
        if n >= len(self._stderr_buf):
            return list(self._stderr_buf)
        return list(self._stderr_buf)[-n:]
        
    def set_tail_keep(self, n):
        self._tail_keep = n
        if n is not None:
            # Recreate stdout buffer
            old_buf = list(self._buf)
            self._buf = deque(maxlen=n)
            for line in old_buf[-min(n, len(old_buf)):]:
                self._buf.append(line)
            
            # Recreate stderr buffer
            old_stderr_buf = list(self._stderr_buf)
            self._stderr_buf = deque(maxlen=n)
            for line in old_stderr_buf[-min(n, len(old_stderr_buf)):]:
                self._stderr_buf.append(line)
            
    def info(self):
        """Return metadata about the command."""
        return {
            'id': self.id,
            'cmd': self.cmd,
            'pid': self.pid,
            'output_lines': len(self._buf),
            'stderr_lines': len(self._stderr_buf),
            'tail_keep': self._tail_keep,
            'start_ts': self.start_ts.isoformat() + 'Z',
            'end_ts': self.end_ts.isoformat() + 'Z' if self.end_ts else None,
            'exit_code': self.exit_code,
            'running': self.running,
            'total_lines': self.total_lines, # Stdout
            'truncated': self.truncated,   # Stdout
            'total_stderr_lines': self.total_stderr_lines,
            'stderr_truncated': self.stderr_truncated
        }

    def chunk(self, start, length=50): # Stdout
        """Return `length` lines starting at zero-based index `start` from run()."""
        if start < 0:
             raise ValueError(f"Start index {start} cannot be negative")

        buf_list = list(self._buf)
        buf_start_abs_index = max(0, self.total_lines - len(self._buf))
        
        if start < buf_start_abs_index:
            raise OutputPurged(self.id)
            
        relative_start_idx = start - buf_start_abs_index
        
        if relative_start_idx >= len(buf_list):
            return []
            
        return buf_list[relative_start_idx : relative_start_idx + length]
