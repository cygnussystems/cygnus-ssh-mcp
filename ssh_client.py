from __future__ import annotations
import paramiko
import socket
import time
import tempfile
import os
import shlex
from datetime import datetime
import logging
import threading
import select
from typing import Optional, Callable, Dict, Deque, Any, Union, List
from ssh_history import CommandHistoryManager
from ssh_models import (
    SshError, CommandTimeout, CommandRuntimeTimeout, CommandFailed,
    SudoRequired, BusyError, OutputPurged, TaskNotFound, CommandHandle
)

# Configure basic logging for the library
log = logging.getLogger(__name__)
# Example basic config (users of the library should configure logging themselves)
# logging.basicConfig(level=logging.INFO)



class SshClient:
    """
    SSH manager for running commands, transferring files, and tracking history.
    Includes support for launching background tasks and monitoring them.
    Uses logging for output. Implements wall-clock timeouts for run().
    """
    def __init__(self, host, user, port=22, keyfile=None, password=None, sudo_password=None,
                 connect_timeout=10, history_limit=50, tail_keep=100):
        from ssh_ops_file import SshFileOperations  # Import here to avoid circular import
        from ssh_ops_task import SshTaskOperations  # Import here to avoid circular import
        from ssh_ops_run import SshRunOperations  # Import here to avoid circular import
        from ssh_ops_directory import SshDirectoryOperations  # Import here to avoid circular import
        from ssh_ops_os import SshOsOperations  # Import here to avoid circular import
        self.host = host
        self.user = user
        self.port = port
        self.keyfile = keyfile
        self.password = password
        self.sudo_password = sudo_password
        self.connect_timeout = connect_timeout
        self._busy_lock = threading.Lock()
        self.history_manager = CommandHistoryManager(history_limit, tail_keep)
        self._logger = logging.getLogger(f"{__name__}.SshClient")

        # Initialize operations classes
        self.run_ops = SshRunOperations(self, tail_keep)
        self.task_ops = SshTaskOperations(self)
        self.file_ops = SshFileOperations(self)
        self.dir_ops = SshDirectoryOperations(self)
        self.os_ops = SshOsOperations(self)

        # Setup Paramiko client
        self._client = paramiko.SSHClient()
        self._client.load_system_host_keys()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self._connect()

    def _connect(self):
        """Establish SSH connection."""
        self._logger.info(f"Connecting to {self.user}@{self.host}:{self.port}...")
        kwargs = dict(
            hostname=self.host,
            port=self.port,
            username=self.user,
            timeout=self.connect_timeout
        )
        if self.keyfile:
            kwargs['key_filename'] = self.keyfile
            self._logger.info(f"Using keyfile: {self.keyfile}")
        if self.password:
            kwargs['password'] = self.password
            # Avoid logging password itself
            self._logger.info("Using password authentication.")

        try:
            self._client.connect(**kwargs)
            self._logger.info("Connection successful.")
        except Exception as e:
            self._logger.error(f"Connection failed: {e}", exc_info=True)
            raise SshError(f"Connection failed: {e}") from e

    def close(self):
        """Close the SSH connection."""
        if self._client:
            self._logger.info("Closing SSH connection.")
            self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def _add_to_history(self, handle):
        """Adds a handle to history (delegates to history_manager)."""
        self.history_manager.add_command(handle.cmd, handle.pid)



    def run(self, cmd, io_timeout=60.0, runtime_timeout=None, sudo=False):
        """
        Execute a command synchronously, streaming output into a CommandHandle.
        This method BLOCKS until the command finishes, fails, or times out.
        Supports I/O inactivity timeout (io_timeout) and total runtime timeout (runtime_timeout).
        Returns the CommandHandle upon completion or raises CommandFailed, CommandTimeout, CommandRuntimeTimeout, SudoRequired.
        """
        return self.run_ops.execute_command(cmd, io_timeout, runtime_timeout, sudo)


    def launch(self, cmd, sudo=False, stdout_log=None, stderr_log=None, log_output=True):
        """
        Launch a command in the background and return a CommandHandle with the PID.
        This method returns almost immediately, it does NOT block waiting for the command.
        Output is NOT captured in the handle's buffer; it's redirected to files or /dev/null.
        If log_output=True (default) and stdout_log/stderr_log are None, redirects
        output to /tmp/task-<pid>.log.
        WARNING: Does not work for interactive commands requiring input.
        """
        return self.task_ops.launch_task(cmd, stdout_log, stderr_log, log_output, sudo)

    def task_status(self, pid):
        """
        Check the status of a process with the given PID on the remote host using a direct channel.
        Returns:
            'running': Process exists.
            'exited': Process does not exist (assumed completed or killed).
            'error': Failed to check status.
        """
        return self.task_ops.get_task_status(pid)


    def task_kill(self, pid, signal=15, sudo=False, force_kill_signal=9, wait_seconds=1.0):
        """
        Send a signal to a process with the given PID on the remote host.
        Uses self.run() internally, so it respects the busy lock and handles sudo.
        Tries the specified signal, waits, checks status, then tries force_kill_signal (default SIGKILL) if needed.
        Returns:
            'killed': Process was successfully terminated (by signal or force_kill_signal).
            'already_exited': Process was already gone before signaling.
            'failed_to_kill': Signaling attempts failed or process remained running.
            'error': An error occurred during the kill attempt.
        """
        return self.task_ops.kill_task(pid, signal, sudo, force_kill_signal, wait_seconds)



    def output(self, handle_id, mode='tail', n=50, start=None):
        """Retrieve output from a previous CommandHandle created by run()."""
        try:
            handle = self.history_manager.get_handle(handle_id)
        except KeyError:
            raise TaskNotFound(handle_id)

        # Output retrieval works for run() handles.
        if mode == 'tail':
            return handle.tail(n)
        elif mode == 'chunk':
            if start is None:
                raise ValueError("`start` is required for chunk mode")
            # Ensure start is int
            try:
                start_idx = int(start)
            except ValueError:
                raise ValueError("`start` must be an integer.")
            return handle.chunk(start_idx, n)
        else:
            raise ValueError(f"Unknown mode: {mode}")

    def get(self, remote_path, local_path):
        """Download a file from remote to local."""
        return self.file_ops.get(remote_path, local_path)

    def put(self, local_path, remote_path):
        """Upload a file from local to remote."""
        return self.file_ops.put(local_path, remote_path)

    def mkdir(self, path, sudo=False, mode=0o755):
        """Create a remote directory with optional sudo."""
        return self.file_ops.mkdir(path, sudo, mode)

    def rmdir(self, path, sudo=False, recursive=False):
        """Remove a remote directory with optional sudo."""
        return self.file_ops.rmdir(path, sudo, recursive)

    def listdir(self, path):
        """List contents of a remote directory."""
        return self.file_ops.listdir(path)

    def stat(self, path):
        """Get file/directory status info."""
        return self.file_ops.stat(path)

    def replace_line(self, remote_file, old_line, new_line, count=1, sudo=False, force=False):
        """
        Replace occurrences of a line in a remote text file.
        Uses temporary local file. Requires write permissions on remote dir/file.
        If sudo=True, attempts to use sudo for the final 'mv' command.
        If force=True, proceeds even if original file cannot be read (sudo only).
        """
        return self.file_ops.replace_line(remote_file, old_line, new_line, count, sudo, force)


    def replace_block(self, remote_file, old_block, new_block, sudo=False, force=False):
        """
        Replace a block of text in a remote text file.
        Uses temporary local file. Requires write permissions on remote dir/file.
        If sudo=True, attempts to use sudo for the final 'mv' command.
        If force=True, proceeds even if original file cannot be read (sudo only).
        """
        return self.file_ops.replace_block(remote_file, old_block, new_block, sudo, force)



    def reboot(self, wait=True, timeout=300):
        """Reboot the remote host and optionally wait until it comes back."""
        return self.os_ops.reboot(wait, timeout)


    def status(self):
        """Return a snapshot of system state using a combined command."""
        return self.os_ops.status()


    def history(self):
        """Return metadata for recent CommandHandles."""
        return self.history_manager.get_history()

    # Directory operations wrappers
    def search_files_recursive(self, start_path, name_pattern, max_depth=None, include_dirs=False):
        """
        Recursively search for files or directories matching a name pattern.
        
        Args:
            start_path: Base directory to search from
            name_pattern: Filename glob pattern (e.g. *.log)
            max_depth: How deep to search (None for unlimited)
            include_dirs: Whether to include matching directories
            
        Returns:
            List of dicts with 'path' and 'type' keys
        """
        return self.dir_ops.search_files_recursive(start_path, name_pattern, max_depth, include_dirs)
    
    def calculate_directory_size(self, path):
        """
        Compute total size of a directory recursively in bytes.
        
        Args:
            path: Directory to measure
            
        Returns:
            Total size in bytes
        """
        return self.dir_ops.calculate_directory_size(path)
    
    def delete_directory_recursive(self, path, dry_run=True, sudo=False):
        """
        Safely delete a directory and all of its contents, with dry-run support.
        
        Args:
            path: Target directory
            dry_run: If true, only preview deletions
            sudo: Whether to use sudo for the operation
            
        Returns:
            Dict with status and list of deleted items
        """
        return self.dir_ops.delete_directory_recursive(path, dry_run, sudo)
    
    def batch_delete_by_pattern(self, path, pattern, dry_run=True, sudo=False):
        """
        Delete all files matching a pattern recursively under a directory.
        
        Args:
            path: Directory to search
            pattern: Glob pattern (e.g. *.tmp)
            dry_run: Whether to only simulate deletion
            sudo: Whether to use sudo for the operation
            
        Returns:
            Dict with status and list of deleted files
        """
        return self.dir_ops.batch_delete_by_pattern(path, pattern, dry_run, sudo)
    
    def safe_move_or_rename(self, source, destination, overwrite=False, sudo=False):
        """
        Move or rename a file or directory, with overwrite control.
        
        Args:
            source: File or directory to move
            destination: New path
            overwrite: Whether to overwrite existing targets
            sudo: Whether to use sudo for the operation
            
        Returns:
            Dict with status and message
        """
        return self.dir_ops.safe_move_or_rename(source, destination, overwrite, sudo)
    
    def list_directory_recursive(self, path, max_depth=None, sudo=False):
        """
        List all contents of a directory tree with rich metadata.
        
        Args:
            path: Starting path
            max_depth: Recursion depth limit
            sudo: Whether to use sudo for the operation
            
        Returns:
            List of dicts with path, type, size_bytes, modified_time, permissions
        """
        return self.dir_ops.list_directory_recursive(path, max_depth, sudo)
    
    def create_archive_from_directory(self, source_path, archive_path, format="tar.gz", sudo=False):
        """
        Create a compressed archive (tar.gz or zip) from a directory.
        
        Args:
            source_path: Directory to archive
            archive_path: Where to write the archive
            format: "tar.gz" or "zip"
            sudo: Whether to use sudo for the operation
            
        Returns:
            Dict with status and archive path
        """
        return self.dir_ops.create_archive_from_directory(source_path, archive_path, format, sudo)
    
    def extract_archive_to_directory(self, archive_path, destination_path, overwrite=False, sudo=False):
        """
        Extract a zip or tar.gz archive to a directory.
        
        Args:
            archive_path: Path to archive file
            destination_path: Extract location
            overwrite: Whether to overwrite existing files
            sudo: Whether to use sudo for the operation
            
        Returns:
            Dict with status and list of extracted files
        """
        return self.dir_ops.extract_archive_to_directory(archive_path, destination_path, overwrite, sudo)
    
    def search_file_contents(self, path, pattern, regex=False, case_sensitive=True, sudo=False):
        """
        Search for a string or regex inside files under a directory.
        
        Args:
            path: Root directory
            pattern: Text or regex to search
            regex: Whether the pattern is a regex
            case_sensitive: Case sensitivity toggle
            sudo: Whether to use sudo for the operation
            
        Returns:
            List of dicts with file, line, content
        """
        return self.dir_ops.search_file_contents(path, pattern, regex, case_sensitive, sudo)
    
    def copy_directory_recursive(self, source_path, destination_path, overwrite=False, 
                               preserve_symlinks=True, preserve_permissions=True, sudo=False):
        """
        Recursively copy one directory to another with robust handling.
        
        Args:
            source_path: Path to copy from
            destination_path: Path to copy to
            overwrite: If true, overwrite existing content
            preserve_symlinks: Copy symlinks as-is vs resolving
            preserve_permissions: Retain original permissions
            sudo: Whether to use sudo for the operation
            
        Returns:
            Dict with status, files_copied, bytes_copied, destination_path
        """
        return self.dir_ops.copy_directory_recursive(
            source_path, destination_path, overwrite, preserve_symlinks, preserve_permissions, sudo
        )

    # _build_cmd helper removed as logic is inlined or handled directly
