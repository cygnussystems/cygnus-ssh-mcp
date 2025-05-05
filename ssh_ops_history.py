from collections import deque
from datetime import datetime
from typing import Optional, Self, List, Dict, Any
from datetime import UTC
from ssh_models import (
    CommandHandle, CommandTimeout, CommandRuntimeTimeout,
    CommandFailed, SudoRequired, SshError
)

class CommandHistoryManager:
    """Manages command history with flexible output retention."""
    
    def __init__(self, history_limit=30, recent_full_output=10, default_tail=100):
        """
        Args:
            history_limit: Total number of commands to keep
            recent_full_output: Number of recent commands to keep full output
            default_tail: Number of lines to keep for older commands
        """
        self._history = {}
        self._history_order = deque()
        self.history_limit = history_limit
        self.recent_full_output = recent_full_output
        self.default_tail = default_tail
        self._next_id = 1

    def add_command(self, cmd: str, pid: Optional[int] = None) -> CommandHandle:
        """Add a new command to history and return its handle."""
        handle_id = self._next_id
        self._next_id += 1
        
        # Create handle with appropriate buffer size
        tail_keep = None if self.recent_full_output > 0 else self.default_tail
        handle = CommandHandle(handle_id, cmd, tail_keep=tail_keep, pid=pid)
        
        # Trim history if needed
        if len(self._history) >= self.history_limit:
            oldest_id = self._history_order.popleft()
            if oldest_id in self._history:
                # Truncate output before removing
                old_handle = self._history[oldest_id]
                if old_handle._tail_keep is not None:
                    old_handle.set_tail_keep(self.default_tail)
                del self._history[oldest_id]
        
        self._history[handle.id] = handle
        self._history_order.append(handle.id)
        
        # Ensure proper output retention
        if self.recent_full_output > 0:
            for handle_id, handle in self._history.items():
                is_currently_recent = handle_id in list(self._history_order)[-self.recent_full_output:]
                if is_currently_recent and handle._tail_keep is not None:
                    # This command is now recent, give it unlimited output
                    handle.set_tail_keep(None)
                elif not is_currently_recent and handle._tail_keep is None:
                    # This command is no longer recent, truncate its output
                    handle.set_tail_keep(self.default_tail)
        
        return handle

    def get_handle(self, handle_id: int) -> CommandHandle:
        """Get a command handle by ID."""
        if handle_id not in self._history:
            raise KeyError(f"No command handle found with ID {handle_id}")
        return self._history[handle_id]

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

