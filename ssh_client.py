from __future__ import annotations
import paramiko
import socket
import time
import tempfile
import os
import shlex
from datetime import datetime, UTC
import logging
import threading
import select
from typing import Optional, Callable, Dict, Deque, Any, Union, List, Literal
from ssh_ops_history import CommandHistoryManager
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
    def __init__(self, host, user, port=22, keyfile=None, key_passphrase=None,
                 password=None, sudo_password=None,
                 connect_timeout=10, history_limit=50, tail_keep=100):
        # Only import Linux-specific operations
        from ssh_ops_file import SshFileOperations_Linux
        from ssh_ops_task import SshTaskOperations_Linux
        from ssh_ops_run import SshRunOperations_Linux
        from ssh_ops_directory import SshDirectoryOperations_Linux
        from ssh_ops_os import SshOsOperations_Linux
        
        # Initialize platform detection
        self.os_type = None  # 'windows', 'linux', 'macos'
        self.os_subtype = None  # 'windows10', 'debian', 'centos', etc.
        
        # Initialize connection status tracking
        self._connection_status = {
            'os_type': None,
            'os_version': None,
            'user': None,
            'cwd': None,
            'has_sudo': sudo_password is not None,  # Assume sudo if password provided
            'last_updated': None
        }
        self._status_lock = threading.Lock()  # For thread safety
        self.host = host
        self.user = user
        self.port = port
        self.keyfile = keyfile
        self.key_passphrase = key_passphrase
        self.password = password
        self.sudo_password = sudo_password
        self.connect_timeout = connect_timeout
        self._busy_lock = threading.Lock()
        self.history_limit = history_limit
        self.tail_keep = tail_keep
        self.history_manager = CommandHistoryManager(history_limit, tail_keep)
        self._logger = logging.getLogger(f"{__name__}.SshClient")

        # Initialize operations after connection
        self.run_ops = None
        self.task_ops = None
        self.file_ops = None
        self.dir_ops = None
        self.os_ops = None
        
        # Setup Paramiko client
        self._client = paramiko.SSHClient()
        self._client.load_system_host_keys()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        # Connect and detect OS
        self._connect()
        self._detect_os()
        
        # Only create Linux operations
        if self.os_type != 'linux':
            raise SshError(f"Unsupported OS detected: {self.os_type}. Only Linux is supported.")
            
        self._create_operations()

    def _detect_os(self):
        """Detect the remote OS type and subtype."""
        # First verify we have an active connection
        if not self._client or not self._client.get_transport() or not self._client.get_transport().is_active():
            raise SshError("Cannot detect OS - no active SSH connection")
            
        try:
            # Use direct Paramiko command execution for OS detection
            stdin, stdout, stderr = self._client.exec_command('uname -s', timeout=5)
            result = stdout.read().decode('utf-8', errors='replace').strip()
            
            if 'Linux' in result:
                self.os_type = 'linux'
                self._detect_linux_distro()
            elif 'Darwin' in result:
                self.os_type = 'macos'
            else:
                # If uname fails, try systeminfo for Windows
                try:
                    stdin, stdout, stderr = self._client.exec_command('systeminfo', timeout=5)
                    stdout.read()  # Just check if command succeeds
                    self.os_type = 'windows'
                except Exception:
                    # Instead of defaulting to Linux, raise an error
                    raise SshError("Failed to detect OS type - neither Linux/macOS nor Windows commands succeeded")
        except Exception as e:
            # Close the connection and raise an error instead of defaulting to Linux
            self.close()
            raise SshError(f"Failed to detect OS: {e}")
            
        self._logger.info(f"Detected remote OS: {self.os_type} ({self.os_subtype})")
        
        # Update connection status with OS info
        with self._status_lock:
            self._connection_status.update({
                'os_type': self.os_type,
                'os_version': self.os_subtype
            })

    def _detect_linux_distro(self):
        """Detect Linux distribution subtype."""
        try:
            result = self.run('cat /etc/os-release')
            if 'debian' in result.lower():
                self.os_subtype = 'debian'
            elif 'centos' in result.lower():
                self.os_subtype = 'centos'
            else:
                self.os_subtype = 'unknown_linux'
        except Exception:
            self.os_subtype = 'unknown_linux'

    def _create_operations(self):
        """Create platform-specific operation classes."""
        from ssh_ops_file import SshFileOperations_Linux, SshFileOperations_Win
        from ssh_ops_task import SshTaskOperations_Linux, SshTaskOperations_Win
        from ssh_ops_run import SshRunOperations_Linux, SshRunOperations_Win
        from ssh_ops_directory import SshDirectoryOperations_Linux, SshDirectoryOperations_Win
        from ssh_ops_os import SshOsOperations_Linux, SshOsOperations_Win

        # Always use Linux operations since we're testing against Linux containers
        self.run_ops = SshRunOperations_Linux(self, self.tail_keep)
        self.task_ops = SshTaskOperations_Linux(self)
        self.file_ops = SshFileOperations_Linux(self)
        self.dir_ops = SshDirectoryOperations_Linux(self)
        self.os_ops = SshOsOperations_Linux(self)

    def _connect(self):
        """Establish SSH connection and update connection status."""
        self._logger.info(f"Connecting to {self.user}@{self.host}:{self.port}...")
        
        # Update connection status with initial info
        with self._status_lock:
            self._connection_status.update({
                'user': self.user,
                'host': self.host,
                'last_updated': time.time()
            })
        kwargs = dict(
            hostname=self.host,
            port=self.port,
            username=self.user,
            timeout=self.connect_timeout
        )
        if self.keyfile:
            kwargs['key_filename'] = self.keyfile
            self._logger.info(f"Using keyfile: {self.keyfile}")
            if self.key_passphrase:
                kwargs['passphrase'] = self.key_passphrase
                self._logger.info("Using passphrase for encrypted key")
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

    def is_connected(self) -> bool:
        """
        Check if the SSH client is connected.
        
        Returns:
            bool: True if connected, False otherwise.
        """
        return (self._client is not None and 
                self._client.get_transport() is not None and 
                self._client.get_transport().is_active())

    def close(self):
        """Close the SSH connection and clear status."""
        if self._client:
            self._logger.info("Closing SSH connection.")
            self._client.close()
            
        # Clear connection status
        with self._status_lock:
            self._connection_status = {
                'os_type': None,
                'os_version': None,
                'user': None,
                'cwd': None,
                'has_sudo': False,
                'last_updated': None
            }

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def _add_to_history(self, handle):
        """Adds a handle to history (delegates to history_manager)."""
        self.history_manager.add_command(handle.cmd, handle.pid)

    def update_connection_status(self, force=False):
        """Update cached connection status if stale (>5 minutes) or forced."""
        with self._status_lock:
            now = time.time()
            last_update = self._connection_status.get('last_updated', 0)
            
            if not force and (now - last_update) < 300:  # 5 minute cache
                return
                
            try:
                # Get basic info with single command
                cmd = """
                echo "USER:$(whoami)"
                echo "CWD:$(pwd)"
                """
                handle = self.run(cmd, io_timeout=5)
                output = "".join(handle.tail(handle.total_lines))
                
                # Parse output
                for line in output.splitlines():
                    if 'USER:' in line:
                        self._connection_status['user'] = line.split(':', 1)[1].strip()
                    elif 'CWD:' in line:
                        self._connection_status['cwd'] = line.split(':', 1)[1].strip()
                
                # Update timestamp
                self._connection_status['last_updated'] = now
                
            except Exception as e:
                self._logger.warning(f"Failed to update connection status: {e}")

    def get_connection_status(self) -> dict:
        """Return current connection status with timestamp."""
        self.update_connection_status()  # Refresh if needed
        with self._status_lock:
            return {
                **self._connection_status,
                'timestamp': datetime.now(UTC).isoformat(),
                'host': self.host
            }

    def verify_sudo_access(self) -> bool:
        """Optionally verify sudo access when needed."""
        try:
            handle = self.run('sudo -n true 2>/dev/null && echo true || echo false', io_timeout=5)
            return 'true' in handle.tail(1)[0]
        except Exception as e:
            self._logger.warning(f"Failed to verify sudo access: {e}")
            return False



    def run(self, cmd: str, io_timeout: float = 60.0, runtime_timeout: Optional[float] = None,
           sudo: bool = False) -> CommandHandle:
        """
        Execute a command synchronously, streaming output into a CommandHandle.
        This method BLOCKS until the command finishes, fails, or times out.
        Supports I/O inactivity timeout (io_timeout) and total runtime timeout (runtime_timeout).
        Returns the CommandHandle upon completion or raises CommandFailed, CommandTimeout, CommandRuntimeTimeout, SudoRequired.
        """
        return self.run_ops.execute_command(cmd, io_timeout, runtime_timeout, sudo)


    def launch(self, cmd: str, sudo: bool = False, stdout_log: Optional[str] = None,
              stderr_log: Optional[str] = None, log_output: bool = True, add_to_history: bool = True) -> CommandHandle:
        """
        Launch a command in the background and return a CommandHandle with the PID.
        This method returns almost immediately, it does NOT block waiting for the command.
        Output is NOT captured in the handle's buffer; it's redirected to files or /dev/null.
        If log_output=True (default) and stdout_log/stderr_log are None, redirects
        output to /tmp/task-<pid>.log.
        If add_to_history=False, the command won't appear in command history.
        WARNING: Does not work for interactive commands requiring input.
        """
        return self.task_ops.launch_task(cmd, stdout_log, stderr_log, log_output, sudo, add_to_history)

    def task_status(self, pid: int) -> Literal['running', 'exited', 'invalid', 'error']:
        """
        Check the status of a process with the given PID on the remote host using a direct channel.
        Returns:
            'running': Process exists.
            'exited': Process does not exist (assumed completed or killed).
            'error': Failed to check status.
        """
        return self.task_ops.get_task_status(pid)


    def task_kill(self, pid: int, signal: int = 15, sudo: bool = False,
                 force_kill_signal: int = 9, wait_seconds: float = 1.0) -> Literal['killed', 'already_exited', 'failed_to_kill', 'invalid_pid', 'error']:
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



    def output(self, handle_id: int, mode: Literal['tail', 'chunk'] = 'tail',
              n: int = 50, start: Optional[int] = None, lines: Optional[int] = None) -> List[str]:
        """Retrieve output from a previous CommandHandle created by run()."""
        try:
            handle = self.history_manager.get_handle(handle_id)
        except KeyError: # history_manager.get_handle raises KeyError if handle_id not found.
            raise TaskNotFound(handle_id)
        # CommandHandle.tail() or .chunk() can raise OutputPurged if output is no longer available.

        if mode == 'tail':
            num_lines_to_tail = n  # Default to n
            if lines is not None:  # If lines is provided, it takes precedence for tail mode
                num_lines_to_tail = lines
            return handle.tail(num_lines_to_tail)
        elif mode == 'chunk':
            if start is None:
                raise ValueError("`start` is required for chunk mode")
            try:
                start_idx = int(start)
            except ValueError:
                raise ValueError("`start` must be an integer for chunk mode.")
            # 'n' is used as length for chunk mode. 'lines' is not typically used here.
            return handle.chunk(start_idx, n)
        else:
            raise ValueError(f"Unknown mode for output: {mode}")

    def get(self, remote_path: str, local_path: str) -> None:
        """Download a file from remote to local."""
        return self.file_ops.get(remote_path, local_path)

    def put(self, local_path: str, remote_path: str) -> None:
        """Upload a file from local to remote."""
        return self.file_ops.put(local_path, remote_path)

    def mkdir(self, path: str, sudo: bool = False, mode: int = 0o755) -> None:
        """Create a remote directory with optional sudo."""
        return self.file_ops.mkdir(path, sudo, mode)

    def rmdir(self, path: str, sudo: bool = False, recursive: bool = False) -> None:
        """Remove a remote directory with optional sudo."""
        return self.file_ops.rmdir(path, sudo, recursive)

    def listdir(self, path: str) -> List[str]:
        """List contents of a remote directory."""
        return self.file_ops.listdir(path)

    def stat(self, path: str) -> Dict:
        """Get file/directory status info."""
        return self.file_ops.stat(path)

    def find_lines_with_pattern(self, remote_file: str, pattern: str, 
                               regex: bool = False, sudo: bool = False) -> dict:
        """
        Search for a pattern in a remote file and return matching lines.
        
        Args:
            remote_file: Path to remote file
            pattern: Text or regex pattern to search for
            regex: Whether to treat pattern as a regular expression
            sudo: Whether to use sudo for the operation
            
        Returns:
            Dictionary with total matches and list of matches (line number and content)
        """
        return self.file_ops.find_lines_with_pattern(remote_file, pattern, regex, sudo)
    
    def get_context_around_line(self, remote_file: str, match_line: str, 
                               context: int = 3, sudo: bool = False) -> dict:
        """
        Get lines before and after a line that matches exactly.
        
        Args:
            remote_file: Path to remote file
            match_line: Exact line content to match
            context: Number of lines before and after to include
            sudo: Whether to use sudo for the operation
            
        Returns:
            Dictionary with match line number and context block
        """
        return self.file_ops.get_context_around_line(remote_file, match_line, context, sudo)
    
    def replace_line_by_content(self, remote_file: str, match_line: str, new_lines: list,
                               sudo: bool = False, force: bool = False) -> dict:
        """
        Replace a unique line (by exact content) with new lines.
        
        Args:
            remote_file: Path to remote file
            match_line: Exact line content to match and replace
            new_lines: List of new lines to insert in place of the match
            sudo: Whether to use sudo for the operation
            force: Whether to proceed if original file cannot be read (sudo only)
            
        Returns:
            Dictionary with operation status
        """
        return self.file_ops.replace_line_by_content(remote_file, match_line, new_lines, sudo, force)
    
    def insert_lines_after_match(self, remote_file: str, match_line: str, lines_to_insert: list,
                                sudo: bool = False, force: bool = False) -> dict:
        """
        Insert lines after a unique line match.
        
        Args:
            remote_file: Path to remote file
            match_line: Exact line content to match
            lines_to_insert: List of lines to insert after the match
            sudo: Whether to use sudo for the operation
            force: Whether to proceed if original file cannot be read (sudo only)
            
        Returns:
            Dictionary with operation status
        """
        return self.file_ops.insert_lines_after_match(remote_file, match_line, lines_to_insert, sudo, force)
    
    def delete_line_by_content(self, remote_file: str, match_line: str,
                              sudo: bool = False, force: bool = False) -> dict:
        """
        Delete a line matching a unique content string.
        
        Args:
            remote_file: Path to remote file
            match_line: Exact line content to match and delete
            sudo: Whether to use sudo for the operation
            force: Whether to proceed if original file cannot be read (sudo only)
            
        Returns:
            Dictionary with operation status
        """
        return self.file_ops.delete_line_by_content(remote_file, match_line, sudo, force)
    
    def copy_file(self, source_path: str, destination_path: str, 
                 append_timestamp: bool = False, sudo: bool = False) -> dict:
        """
        Copy a file with optional timestamp appended to the destination.
        
        Args:
            source_path: Source file path
            destination_path: Destination file path
            append_timestamp: Whether to append a timestamp to the destination
            sudo: Whether to use sudo for the operation
            
        Returns:
            Dictionary with operation status
        """
        return self.file_ops.copy_file(source_path, destination_path, append_timestamp, sudo)



    def reboot(self, wait: bool = True, timeout: int = 300) -> None:
        """Reboot the remote host and optionally wait until it comes back."""
        return self.os_ops.reboot(wait, timeout)


    def full_status(self) -> Dict[str, Any]:
        """Return a snapshot of system state using a combined command."""
        return self.os_ops.status()


    def history(self) -> List[Dict[str, Any]]:
        """Return metadata for recent CommandHandles."""
        return self.history_manager.get_history()

    # Directory operations wrappers
    def search_files_recursive(self, start_path: str, name_pattern: str,
                             max_depth: Optional[int] = None, include_dirs: bool = False) -> List[Dict[str, str]]:
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
    
    def calculate_directory_size(self, path: str) -> int:
        """
        Compute total size of a directory recursively in bytes.
        
        Args:
            path: Directory to measure
            
        Returns:
            Total size in bytes
        """
        return self.dir_ops.calculate_directory_size(path)
    
    def delete_directory_recursive(self, path: str, dry_run: bool = True,
                                 sudo: bool = False) -> Dict[str, Any]:
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
    
    def batch_delete_by_pattern(self, path: str, pattern: str, dry_run: bool = True,
                              sudo: bool = False) -> Dict[str, Any]:
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
    
    def safe_move_or_rename(self, source: str, destination: str, overwrite: bool = False,
                          sudo: bool = False) -> Dict[str, Any]:
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
    
    def list_directory_recursive(self, path: str, max_depth: Optional[int] = None,
                               sudo: bool = False) -> List[Dict[str, Any]]:
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
    
    def create_archive_from_directory(self, source_path: str, archive_path: str,
                                    format: str = "tar.gz", sudo: bool = False) -> Dict[str, Any]:
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
    
    def extract_archive_to_directory(self, archive_path: str, destination_path: str,
                                   overwrite: bool = False, sudo: bool = False) -> Dict[str, Any]:
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
    
    def search_file_contents(self, path: str, pattern: str, regex: bool = False,
                           case_sensitive: bool = True, sudo: bool = False) -> List[Dict[str, Any]]:
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
    
    def copy_directory_recursive(self, source_path: str, destination_path: str, overwrite: bool = False, 
                               preserve_symlinks: bool = True, preserve_permissions: bool = True, 
                               sudo: bool = False) -> Dict[str, Any]:
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
