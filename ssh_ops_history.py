from collections import deque
from datetime import datetime
from typing import Optional, Self
from datetime import UTC
from ssh_models import (
    CommandHandle, CommandTimeout, CommandRuntimeTimeout,
    CommandFailed, SudoRequired, SshError
)
from datetime import datetime
from typing import Dict, Deque, Optional
from ssh_models import CommandHandle

class CommandHistoryManager:
    """Manages command history with flexible output retention."""
    
    def __init__(self, history_limit=30, recent_full_output=10, default_tail=50):
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
        
        # Create handle with unlimited buffer for recent commands
        is_recent = len(self._history) < self.recent_full_output
        tail_keep = None if is_recent else self.default_tail
        handle = CommandHandle(handle_id, cmd, tail_keep=tail_keep, pid=pid)
        
        # Trim history if needed
        if len(self._history) >= self.history_limit:
            oldest_id = self._history_order.popleft()
            if oldest_id in self._history:
                del self._history[oldest_id]
        
        self._history[handle.id] = handle
        self._history_order.append(handle.id)
        
        # Trim older commands if they've fallen out of recent
        if len(self._history) > self.recent_full_output:
            for handle_id, handle in self._history.items():
                if handle.tail_keep is None and \
                   handle_id not in list(self._history_order)[-self.recent_full_output:]:
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

# class CommandHistoryManager:
#     """Manages command history and output storage."""
#
#     def __init__(self, history_limit: int = 50, tail_keep: int = 100):
#         """
#         Args:
#             history_limit: Maximum number of commands to keep in history
#             tail_keep: Number of output lines to keep per command
#         """
#         self._history: Dict[int, CommandHandle] = {}
#         self._history_order: Deque[int] = deque()
#         self._history_limit = history_limit
#         self._tail_keep = tail_keep
#         self._next_id = 1
#
#     def add_command(self, cmd: str, pid: Optional[int] = None) -> CommandHandle:
#         """Add a new command to history and return its handle."""
#         handle_id = self._next_id
#         self._next_id += 1
#
#         handle = CommandHandle(handle_id, cmd, tail_keep=self._tail_keep, pid=pid)
#
#         # Trim history if needed
#         if len(self._history) >= self._history_limit:
#             oldest_id = self._history_order.popleft()
#             if oldest_id in self._history:
#                 del self._history[oldest_id]
#
#         self._history[handle.id] = handle
#         self._history_order.append(handle.id)
#         return handle
#
#     def get_handle(self, handle_id: int) -> CommandHandle:
#         """Get a command handle by ID."""
#         if handle_id not in self._history:
#             raise KeyError(f"No command handle found with ID {handle_id}")
#         return self._history[handle_id]
#
#     def get_history(self) -> list:
#         """Get metadata for all commands in history order."""
#         return [self._history[handle_id].info()
#                for handle_id in self._history_order
#                if handle_id in self._history]
#
#     def update_handle(self, handle: CommandHandle) -> None:
#         """Update a command handle in history."""
#         if handle.id not in self._history:
#             raise KeyError(f"Handle ID {handle.id} not found in history")
#         self._history[handle.id] = handle
