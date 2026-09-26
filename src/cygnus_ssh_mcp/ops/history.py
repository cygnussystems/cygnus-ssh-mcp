from collections import deque
from datetime import datetime
from typing import Optional, Self, List, Dict, Any
from datetime import UTC
from cygnus_ssh_mcp.models import (
    CommandHandle, CommandTimeout, CommandRuntimeTimeout,
    CommandFailed, SudoRequired, SshError, OutputLimits
)

class CommandHistoryManager:
    """Manages command history with flexible output retention."""
    
    def __init__(self, history_limit=30, recent_full_output=None, default_tail=None):
        """
        Args:
            history_limit: Total number of commands to keep
            recent_full_output: Unused (kept for compatibility) - retention is size-based
                now, see models.OutputLimits
            default_tail: Optional line limit per stream per command (None = size limit only)
        """
        self._history = {}
        self._history_order = deque()
        self.history_limit = history_limit
        self.default_tail = default_tail
        self.tail_keep = default_tail  # Add this attribute for compatibility with tests
        self._next_id = 1

    def add_command(self, cmd: str, pid: Optional[int] = None, sudo: bool = False,
                     origin: str = 'user', parent_tool: Optional[str] = None) -> CommandHandle:
        """Add a new command to history and return its handle."""
        handle_id = self._next_id
        self._next_id += 1

        handle = CommandHandle(handle_id, cmd, tail_keep=self.default_tail, pid=pid, sudo=sudo,
                                origin=origin, parent_tool=parent_tool)

        # Trim history if needed
        if len(self._history) >= self.history_limit:
            oldest_id = self._history_order.popleft()
            self._history.pop(oldest_id, None)

        self._history[handle.id] = handle
        self._history_order.append(handle.id)
        self._enforce_memory_cap()
        return handle

    def _enforce_memory_cap(self):
        """Keep total retained output under OutputLimits.total by releasing the output of
        the oldest FINISHED commands first (running commands are never touched). Their
        metadata stays in history; their output reads as dropped, with a clear message."""
        total = sum(h.memory_chars() for h in self._history.values())
        if total <= OutputLimits.total:
            return
        for old_id in list(self._history_order):
            handle = self._history.get(old_id)
            if handle is None or handle.running or not handle.memory_chars():
                continue
            total -= handle.memory_chars()
            handle.release_output()
            if total <= OutputLimits.total:
                break

    def get_handle(self, handle_id: int) -> CommandHandle:
        """Get a command handle by ID."""
        if handle_id not in self._history:
            raise KeyError(f"No command handle found with ID {handle_id}")
        return self._history[handle_id]

    def remove_command(self, handle_id: int) -> None:
        """Remove a handle from history entirely - for cases where the handle was
        added optimistically (before a command's real PID/exit code is known) but
        turned out not to represent a real, user-visible execution at all (e.g. a
        cwd-validation failure, where the wrapper process that ran is an
        implementation detail, not the user's command). No-op if already absent.
        """
        self._history.pop(handle_id, None)
        try:
            self._history_order.remove(handle_id)
        except ValueError:
            pass

    def get_history(self) -> List[Dict[str, Any]]:
        """Get metadata for all commands in history order."""
        return [self._history[handle_id].info()
               for handle_id in self._history_order
               if handle_id in self._history]

    def update_handle(self, handle: CommandHandle) -> None:
        """Update a command handle in history."""
        if handle.id not in self._history:
            raise KeyError(f"Handle ID {handle.id} not found in history")
        self._history[handle.id] = handle

    def get_output(self, handle_id: int, lines: Optional[int] = None) -> List[str]:
        """Get output for a command, optionally limiting to specific number of lines."""
        handle = self.get_handle(handle_id)
        if lines is None:
            return handle.get_full_output()
        return handle.tail(lines)

    def clear(self) -> int:
        """Clear all command history. Returns number of entries cleared."""
        count = len(self._history)
        self._history.clear()
        self._history_order.clear()
        return count

