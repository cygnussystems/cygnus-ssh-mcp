import os
import posixpath
import re
import shlex
import logging
from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any
from cygnus_ssh_mcp.ps_encode import powershell_encoded_command
from cygnus_ssh_mcp.models import SshError, report_progress


class SshDirectoryOperations(ABC):
    """Base class for directory operations. Platform-specific commands are abstract methods."""

    def __init__(self, ssh_client):
        """
        Args:
            ssh_client: Reference to parent SSH client
        """
        self.ssh_client = ssh_client
        self.logger = logging.getLogger(f"{__name__}.SshDirectoryOperations")

    # ==========================================================================
    # Abstract command methods - implemented by platform-specific subclasses
    # ==========================================================================

    @abstractmethod
    def _cmd_find_with_type(self, path: str, name_pattern: str, max_depth: Optional[int], include_dirs: bool) -> str:
        """Return command to find files with path and type output (tab-separated: path\\ttype)."""
        pass

    @abstractmethod
    def _cmd_dir_size(self, path: str) -> str:
        """Return command to get directory size in bytes."""
        pass

    @abstractmethod
    def _cmd_list_with_metadata(self, path: str, max_depth: Optional[int]) -> str:
        """Return command to list files with metadata (tab-separated: path\\ttype\\tsize\\tmtime\\tperms\\tuser\\tgroup)."""
        pass

    @abstractmethod
    def _cmd_file_size(self, path: str) -> str:
        """Return command to get file size in bytes."""
        pass

    @abstractmethod
    def _cmd_find_symlinks(self, path: str) -> str:
        """Return command to find symlinks with their targets (tab-separated: path\\ttarget)."""
        pass

    # ==========================================================================
    # Shared implementation methods
    # ==========================================================================

    def search_files_recursive(self,
                              start_path: str,
                              name_pattern: str,
                              max_depth: Optional[int] = None,
                              include_dirs: bool = False) -> List[Dict[str, str]]:
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
        self.logger.info(f"Searching for '{name_pattern}' in {start_path} (max_depth={max_depth}, include_dirs={include_dirs})")

        cmd = self._cmd_find_with_type(start_path, name_pattern, max_depth, include_dirs)
        self.logger.debug(f"Executing search command: {cmd}")

        try:
            handle = self.ssh_client.run(cmd, io_timeout=120, runtime_timeout=300)

            # Process the output
            results = []
            for line in handle.tail(handle.total_lines):
                if not line.strip():
                    continue

                parts = line.strip().split('\t')
                if len(parts) == 2:
                    path, type_code = parts
                    # Convert find's type codes to more descriptive types
                    type_map = {
                        'f': 'file',
                        'd': 'directory',
                        'l': 'symlink',
                        'p': 'pipe',
                        's': 'socket',
                        'b': 'block',
                        'c': 'character'
                    }
                    file_type = type_map.get(type_code, type_code)

                    results.append({
                        'path': path,
                        'type': file_type
                    })

            self.logger.info(f"Found {len(results)} matches for '{name_pattern}'")
            return results

        except Exception as e:
            self.logger.error(f"Error searching for files: {e}", exc_info=True)
            raise

    def calculate_directory_size(self, path: str) -> int:
        """
        Compute total size of a directory recursively in bytes.

        Args:
            path: Directory to measure

        Returns:
            Total size in bytes
        """
        self.logger.info(f"Calculating size of directory: {path}")

        cmd = self._cmd_dir_size(path)

        try:
            handle = self.ssh_client.run(cmd, io_timeout=120, runtime_timeout=300)

            if handle.exit_code != 0:
                self.logger.error(f"Failed to calculate directory size: {handle.tail(5)}")
                raise RuntimeError(f"Failed to calculate directory size, exit code: {handle.exit_code}")

            # Parse the output (should be a single number)
            # Handle empty output (e.g., empty directory on Windows returns nothing)
            output_lines = handle.tail(1)
            if not output_lines or not output_lines[0].strip():
                self.logger.info(f"Directory {path} is empty, size: 0 bytes")
                return 0

            size_str = output_lines[0].strip()
            size_bytes = int(size_str)

            self.logger.info(f"Directory {path} size: {size_bytes} bytes")
            return size_bytes

        except ValueError as ve:
            # Handle case where output isn't a valid number (e.g., empty string)
            self.logger.warning(f"Could not parse size output, assuming empty: {ve}")
            return 0
        except Exception as e:
            self.logger.error(f"Error calculating directory size: {e}", exc_info=True)
            raise

    def delete_directory_recursive(self,
                                  path: str,
                                  dry_run: bool = True,
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
        self.logger.info(f"Deleting directory: {path} (dry_run={dry_run}, sudo={sudo})")

        # Safety check - don't allow deleting root or home directory
        path = path.rstrip('/')
        if path == '' or path == '/' or path == '/home' or path == f'/home/{self.ssh_client.user}':
            error_msg = f"Refusing to delete critical directory: {path}"
            self.logger.error(error_msg)
            return {
                'status': 'error',
                'error': error_msg,
                'deleted_items': []
            }

        # First list what would be deleted (for both dry run and actual deletion)
        list_cmd = f"find {shlex.quote(path)} -depth -print"

        try:
            list_handle = self.ssh_client.run(list_cmd, io_timeout=120, runtime_timeout=300, sudo=sudo)

            if list_handle.exit_code != 0:
                self.logger.error(f"Failed to list directory contents: {list_handle.tail(5)}")
                return {
                    'status': 'error',
                    'error': f"Failed to list directory contents, exit code: {list_handle.exit_code}",
                    'deleted_items': []
                }

            # Get the list of items that would be deleted
            items = [line.strip() for line in list_handle.tail(list_handle.total_lines) if line.strip()]

            # If dry run, just return the list
            if dry_run:
                self.logger.info(f"Dry run - would delete {len(items)} items")
                return {
                    'status': 'success',
                    'dry_run': True,
                    'deleted_items': items
                }

            # Otherwise, perform the actual deletion
            delete_cmd = f"rm -rf {shlex.quote(path)}"
            delete_handle = self.ssh_client.run(delete_cmd, io_timeout=120, runtime_timeout=300, sudo=sudo)

            if delete_handle.exit_code != 0:
                self.logger.error(f"Failed to delete directory: {delete_handle.tail(5)}")
                return {
                    'status': 'error',
                    'error': f"Failed to delete directory, exit code: {delete_handle.exit_code}",
                    'deleted_items': []
                }

            self.logger.info(f"Successfully deleted {len(items)} items")
            return {
                'status': 'success',
                'deleted_items': items
            }

        except Exception as e:
            self.logger.error(f"Error deleting directory: {e}", exc_info=True)
            return {
                'status': 'error',
                'error': str(e),
                'deleted_items': []
            }

    def batch_delete_by_pattern(self,
                               path: str,
                               pattern: str,
                               dry_run: bool = True,
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
        self.logger.info(f"Batch deleting files matching '{pattern}' in {path} (dry_run={dry_run}, sudo={sudo})")

        # First find all matching files
        find_cmd = f"find {shlex.quote(path)} -type f -name {shlex.quote(pattern)} -print"

        try:
            find_handle = self.ssh_client.run(find_cmd, io_timeout=120, runtime_timeout=300, sudo=sudo)

            if find_handle.exit_code != 0:
                self.logger.error(f"Failed to find matching files: {find_handle.tail(5)}")
                return {
                    'status': 'error',
                    'error': f"Failed to find matching files, exit code: {find_handle.exit_code}",
                    'deleted_files': []
                }

            # Get the list of files that would be deleted
            files = [line.strip() for line in find_handle.tail(find_handle.total_lines) if line.strip()]

            # If dry run, just return the list
            if dry_run:
                self.logger.info(f"Dry run - would delete {len(files)} files")
                return {
                    'status': 'success',
                    'dry_run': True,
                    'deleted_files': files
                }

            # If no files found, return early
            if not files:
                self.logger.info("No matching files found to delete")
                return {
                    'status': 'success',
                    'deleted_files': []
                }

            # Otherwise, delete each file
            # Using xargs to handle large numbers of files efficiently
            delete_cmd = f"find {shlex.quote(path)} -type f -name {shlex.quote(pattern)} -print0 | xargs -0 rm -f"
            delete_handle = self.ssh_client.run(delete_cmd, io_timeout=120, runtime_timeout=300, sudo=sudo)

            if delete_handle.exit_code != 0:
                self.logger.error(f"Failed to delete files: {delete_handle.tail(5)}")
                return {
                    'status': 'error',
                    'error': f"Failed to delete files, exit code: {delete_handle.exit_code}",
                    'deleted_files': []
                }

            self.logger.info(f"Successfully deleted {len(files)} files")
            return {
                'status': 'success',
                'deleted_files': files
            }

        except Exception as e:
            self.logger.error(f"Error batch deleting files: {e}", exc_info=True)
            return {
                'status': 'error',
                'error': str(e),
                'deleted_files': []
            }

    def safe_move_or_rename(self,
                           source: str,
                           destination: str,
                           overwrite: bool = False,
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
        self.logger.info(f"Moving {source} to {destination} (overwrite={overwrite}, sudo={sudo})")

        # Check if source exists
        source_check_cmd = f"[ -e {shlex.quote(source)} ] && echo 'exists' || echo 'not_exists'"
        source_check = self.ssh_client.run(source_check_cmd, io_timeout=30, sudo=sudo)
        # Exact match, not 'in' - 'not_exists' contains 'exists' as a substring, so a
        # naive 'exists' in ... check is always True regardless of the real answer.
        source_exists = source_check.last_nonblank() == 'exists'

        if not source_exists:
            self.logger.error(f"Source does not exist: {source}")
            return {
                'success': False,
                'message': f"Source does not exist: {source}"
            }

        # Check if destination exists
        check_cmd = f"[ -e {shlex.quote(destination)} ] && echo 'exists' || echo 'not_exists'"

        try:
            check_handle = self.ssh_client.run(check_cmd, io_timeout=30, sudo=sudo)
            destination_exists = check_handle.last_nonblank() == 'exists'

            # Debug log the actual check result
            self.logger.debug(f"Destination check result: '{check_handle.last_nonblank()}', exists={destination_exists}")

            if destination_exists and not overwrite:
                self.logger.warning(f"Destination exists and overwrite=False: {destination}")
                return {
                    'success': False,
                    'message': f"Destination exists and overwrite not allowed: {destination}"
                }

            # Perform the move
            move_cmd = f"mv {'-f' if overwrite else ''} {shlex.quote(source)} {shlex.quote(destination)}"
            move_handle = self.ssh_client.run(move_cmd, io_timeout=120, runtime_timeout=300, sudo=sudo)

            if move_handle.exit_code != 0:
                self.logger.error(f"Failed to move/rename: {move_handle.tail(5)}")
                return {
                    'success': False,
                    'message': f"Failed to move/rename, exit code: {move_handle.exit_code}"
                }

            self.logger.info(f"Successfully moved {source} to {destination}")
            return {
                'success': True,
                'message': f"Successfully moved {source} to {destination}"
            }

        except Exception as e:
            self.logger.error(f"Error moving/renaming: {e}", exc_info=True)
            return {
                'success': False,
                'message': str(e)
            }

    def list_directory_recursive(self,
                                path: str,
                                max_depth: Optional[int] = None,
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
        self.logger.info(f"Listing directory recursively: {path} (max_depth={max_depth}, sudo={sudo})")

        cmd = self._cmd_list_with_metadata(path, max_depth)
        self.logger.debug(f"Executing list command: {cmd}")

        try:
            handle = self.ssh_client.run(cmd, io_timeout=120, runtime_timeout=300, sudo=sudo)

            if handle.exit_code != 0:
                self.logger.error(f"Failed to list directory: {handle.tail(5)}")
                raise RuntimeError(f"Failed to list directory, exit code: {handle.exit_code}")

            # Process the output
            results = []
            for line in handle.tail(handle.total_lines):
                if not line.strip():
                    continue

                parts = line.strip().split('\t')
                if len(parts) >= 7:
                    path, type_code, size, mtime, perms, user, group = parts[:7]

                    # Convert find's type codes to more descriptive types
                    type_map = {
                        'f': 'file',
                        'd': 'directory',
                        'l': 'symlink',
                        'p': 'pipe',
                        's': 'socket',
                        'b': 'block',
                        'c': 'character'
                    }
                    file_type = type_map.get(type_code, type_code)

                    # Convert size to int
                    try:
                        size_bytes = int(size)
                    except ValueError:
                        size_bytes = 0

                    # Convert mtime to float
                    try:
                        modified_time = float(mtime)
                    except ValueError:
                        modified_time = 0

                    results.append({
                        'path': path,
                        'type': file_type,
                        'size_bytes': size_bytes,
                        'modified_time': modified_time,
                        'permissions': perms,
                        'user': user,
                        'group': group
                    })

            self.logger.info(f"Listed {len(results)} items in {path}")
            return results

        except Exception as e:
            self.logger.error(f"Error listing directory: {e}", exc_info=True)
            raise

    def create_archive_from_directory(self,
                                     source_path: str,
                                     archive_path: str,
                                     format: str = "tar.gz",
                                     sudo: bool = False,
                                     parent_tool: Optional[str] = None) -> Dict[str, Any]:
        """
        Create a compressed archive (tar.gz or tar) from a directory.

        Args:
            source_path: Directory to archive
            archive_path: Where to write the archive
            format: "tar.gz" or "tar"
            sudo: Whether to use sudo for the operation
            parent_tool: When set, tags every command this issues as internal
                plumbing owned by that tool (e.g. 'ssh_dir_transfer') rather than
                a direct user-issued command - see ssh_cmd_history's
                include_internal filter. Left None for direct use (e.g.
                ssh_archive_create), where these commands ARE the user's request.

        Returns:
            Dict with status and archive path
        """
        self.logger.info(f"Creating {format} archive from {source_path} to {archive_path} (sudo={sudo})")
        history_tag = {'origin': 'tool_internal', 'parent_tool': parent_tool} if parent_tool else {}

        # Validate format
        if format not in ["tar.gz", "tar"]:
            error_msg = f"Unsupported archive format: {format}. Use 'tar.gz' or 'tar'."
            self.logger.error(error_msg)
            return {
                'status': 'error',
                'message': error_msg
            }

        tar_started = False
        try:
            # Get directory name without trailing slash
            source_dir = source_path.rstrip('/')
            # remote POSIX path (Windows has its own implementation) - never the local rules
            parent_dir = posixpath.dirname(source_dir)
            base_name = posixpath.basename(source_dir)

            tar_started = True
            # Create archive based on format
            if format == "tar.gz":
                # Create tar.gz archive (compressed)
                cmd = f"tar -czf {shlex.quote(archive_path)} -C {shlex.quote(parent_dir)} {shlex.quote(base_name)}"
                handle = self.ssh_client.run(cmd, io_timeout=300, runtime_timeout=1800, sudo=sudo, **history_tag)
            else:  # tar
                # Create tar archive (uncompressed)
                cmd = f"tar -cf {shlex.quote(archive_path)} -C {shlex.quote(parent_dir)} {shlex.quote(base_name)}"
                handle = self.ssh_client.run(cmd, io_timeout=300, runtime_timeout=1800, sudo=sudo, **history_tag)

            if handle.exit_code != 0:
                self.logger.error(f"Failed to create archive: {handle.tail(5)}")
                return {
                    'status': 'error',
                    'message': f"Failed to create archive, exit code: {handle.exit_code}"
                }

            # Verify the archive was created
            verify_cmd = f"[ -f {shlex.quote(archive_path)} ] && echo 'exists' || echo 'not_exists'"
            verify_handle = self.ssh_client.run(verify_cmd, io_timeout=30, sudo=sudo, **history_tag)

            if verify_handle.last_nonblank() != 'exists':
                self.logger.error(f"Archive was not created at {archive_path}")
                return {
                    'status': 'error',
                    'message': f"Archive was not created at {archive_path}"
                }

            # Get archive size - best effort only: the archive exists at this point, so a
            # failing size query (e.g. no `stat` at all on OpenWrt/BusyBox, 2026-09-28) must
            # never turn a created archive into an error the caller might "fix" by retrying.
            size_error = None
            try:
                size_cmd = self._cmd_file_size(archive_path)
                size_handle = self.ssh_client.run(size_cmd, io_timeout=30, sudo=sudo, **history_tag)
                archive_size = int(size_handle.last_nonblank())
            except Exception as e:
                archive_size = -1
                size_error = f"Archive created, but its size couldn't be read: {e}"
                self.logger.warning(size_error)

            self.logger.info(f"Successfully created archive at {archive_path} ({archive_size} bytes)")
            result = {
                'status': 'success',
                'success': True,  # Add this for compatibility with tests
                'archive_created': archive_path,
                'format': format,
                'size_bytes': archive_size
            }
            if size_error:
                result['size_error'] = size_error
            return result

        except Exception as e:
            self.logger.error(f"Error creating archive: {e}", exc_info=True)
            message = str(e)
            if tar_started:
                message += (f" - note: tar was started, so a complete or partial archive may exist "
                            f"at {archive_path}; check it (e.g. ssh_cmd_run 'ls -l {archive_path}') "
                            f"before retrying.")
            return {
                'status': 'error',
                'message': message
            }

    def extract_archive_to_directory(self,
                                    archive_path: str,
                                    destination_path: str,
                                    overwrite: bool = False,
                                    sudo: bool = False,
                                    parent_tool: Optional[str] = None) -> Dict[str, Any]:
        """
        Extract a tar or tar.gz archive to a directory.

        Args:
            archive_path: Path to archive file
            destination_path: Extract location
            overwrite: Whether to overwrite existing files
            sudo: Whether to use sudo for the operation
            parent_tool: When set, tags every command this issues as internal
                plumbing owned by that tool (e.g. 'ssh_dir_transfer') rather than
                a direct user-issued command - see ssh_cmd_history's
                include_internal filter. Left None for direct use (e.g.
                ssh_archive_extract), where these commands ARE the user's request.

        Returns:
            Dict with status and list of extracted files
        """
        self.logger.info(f"Extracting archive {archive_path} to {destination_path} (overwrite={overwrite}, sudo={sudo})")
        history_tag = {'origin': 'tool_internal', 'parent_tool': parent_tool} if parent_tool else {}

        # Determine archive type
        if archive_path.endswith('.tar.gz') or archive_path.endswith('.tgz'):
            archive_type = 'tar.gz'
        elif archive_path.endswith('.tar'):
            archive_type = 'tar'
        else:
            error_msg = f"Unsupported archive format for {archive_path}. Supported formats: .tar.gz, .tgz, .tar"
            self.logger.error(error_msg)
            return {
                'status': 'error',
                'message': error_msg,
                'extracted_files': []
            }

        try:
            # Create destination directory if it doesn't exist
            mkdir_cmd = f"mkdir -p {shlex.quote(destination_path)}"
            self.ssh_client.run(mkdir_cmd, io_timeout=30, sudo=sudo, **history_tag)

            # List files in the archive before extraction
            if archive_type == 'tar.gz':
                list_cmd = f"tar -tzf {shlex.quote(archive_path)}"
            else:  # tar
                list_cmd = f"tar -tf {shlex.quote(archive_path)}"

            list_handle = self.ssh_client.run(list_cmd, io_timeout=120, runtime_timeout=300, sudo=sudo, **history_tag)

            if list_handle.exit_code != 0:
                self.logger.error(f"Failed to list archive contents: {list_handle.tail(5)}")
                return {
                    'status': 'error',
                    'message': f"Failed to list archive contents, exit code: {list_handle.exit_code}",
                    'extracted_files': []
                }

            # Get the list of files in the archive
            files = [line.strip() for line in list_handle.tail(list_handle.total_lines) if line.strip()]

            # Extract the archive
            if archive_type == 'tar.gz':
                # Use --strip-components=1 to remove the top-level directory
                extract_cmd = f"tar -xzf {shlex.quote(archive_path)} -C {shlex.quote(destination_path)} --strip-components=1"
                if not overwrite:
                    extract_cmd += " --keep-old-files"
            else:  # tar
                # For tar, similar to tar.gz but without the z (compression) flag
                extract_cmd = f"tar -xf {shlex.quote(archive_path)} -C {shlex.quote(destination_path)} --strip-components=1"
                if not overwrite:
                    extract_cmd += " --keep-old-files"

            kept_existing = False
            extract_handle = self.ssh_client.run(extract_cmd, io_timeout=300, runtime_timeout=1800, sudo=sudo, **history_tag)

            # Check for non-zero exit code but handle the special case for tar --keep-old-files
            # which exits with code 1 if files already exist
            if extract_handle.exit_code != 0:
                if archive_type == 'tar.gz' and not overwrite and extract_handle.exit_code == 1:
                    # This is expected with --keep-old-files if files exist
                    self.logger.warning("Some files already exist and were not overwritten")
                    kept_existing = True
                else:
                    self.logger.error(f"Failed to extract archive: {extract_handle.tail(5)}")
                    return {
                        'status': 'error',
                        'message': f"Failed to extract archive, exit code: {extract_handle.exit_code}",
                        'extracted_files': []
                    }

            self.logger.info(f"Successfully extracted {len(files)} files to {destination_path}")
            result = {
                'status': 'success',
                'success': True,  # Add this for compatibility with tests
                'extracted_files': files,
                'destination_path': destination_path
            }
            if kept_existing:
                result['existing_files_kept'] = True
            return result

        except Exception as e:
            self.logger.error(f"Error extracting archive: {e}", exc_info=True)
            return {
                'status': 'error',
                'message': str(e),
                'extracted_files': []
            }

    def search_file_contents(self,
                            path: str,
                            pattern: str,
                            regex: bool = False,
                            case_sensitive: bool = True,
                            sudo: bool = False) -> List[Dict[str, Any]]:
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
        self.logger.info(f"Searching for '{pattern}' in files under {path} (regex={regex}, case_sensitive={case_sensitive}, sudo={sudo})")
        self.last_search_skipped = []

        # Build grep command with appropriate options
        grep_opts = []
        if regex:
            grep_opts.append("-E")  # Extended regex
        if not case_sensitive:
            grep_opts.append("-i")  # Case insensitive

        # Recursive, line numbers, always print the file name
        grep_opts.extend(["-r", "-n", "-H"])

        # grep's own exit code: 0 = matches, 1 = no match (a normal empty result),
        # >=2 = a real error (possibly alongside matches, e.g. some unreadable files).
        # It's echoed as a marker and the command always exits 0, so "no match" never
        # surfaces as a failure. (The old `find | xargs grep ... || [ $? -eq 1 ]` broke on
        # GNU xargs, which reports a child's exit 1 as 123.) -e keeps a pattern starting
        # with '-' from being read as an option.
        cmd = (f"grep {' '.join(grep_opts)} -e {shlex.quote(pattern)} {shlex.quote(path)}; "
               f"echo \"__GREP_RC__:$?\"; exit 0")

        try:
            handle = self.ssh_client.run(cmd, io_timeout=300, runtime_timeout=1800, sudo=sudo)

            # Process the output
            results = []
            grep_rc = None
            for line in handle.tail(handle.total_lines):
                if line.startswith("__GREP_RC__:"):
                    grep_rc = int(line.split(":", 1)[1].strip() or 2)
                    continue
                if not line.strip():
                    continue

                # Parse grep output format: filename:line_number:content
                parts = line.split(':', 2)
                if len(parts) >= 3:
                    file_path, line_num, content = parts

                    try:
                        line_num = int(line_num)
                    except ValueError:
                        line_num = -1

                    results.append({
                        'file': file_path,
                        'line': line_num,
                        'content': content.rstrip()
                    })

            if grep_rc is not None and grep_rc >= 2:
                stderr_text = handle.get_full_stderr().strip()
                # Only a problem with the starting directory itself (missing, unreadable)
                # is an error. Unreadable files/subdirectories inside it (e.g. systemd's
                # private dirs under /tmp) are skipped: the search result - including
                # "no matches" = [] - still stands.
                root = path.rstrip('/') or '/'
                root_failed = any(line.startswith((f"grep: {root}: ", f"grep: {root}/: "))
                                  for line in stderr_text.splitlines())
                if root_failed or not stderr_text:
                    raise SshError(
                        f"Content search under '{path}' failed (grep exit {grep_rc}): "
                        f"{stderr_text or 'no error details'}"
                    )
                self.logger.warning(f"Search skipped some unreadable entries under '{path}' "
                                    f"(grep exit {grep_rc}): {stderr_text[:300]}")
                for line in stderr_text.splitlines():
                    if line.startswith("grep: "):
                        where, _, why = line[len("grep: "):].rpartition(": ")
                        self.last_search_skipped.append({'path': where or line, 'reason': why or line})

            self.logger.info(f"Found {len(results)} matches for '{pattern}'")
            return results

        except Exception as e:
            self.logger.error(f"Error searching file contents: {e}", exc_info=True)
            raise

    def copy_directory_recursive(self,
                                source_path: str,
                                destination_path: str,
                                overwrite: bool = False,
                                preserve_symlinks: bool = True,
                                preserve_permissions: bool = True,
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
        self.logger.info(f"Copying directory {source_path} to {destination_path} (overwrite={overwrite}, "
                        f"preserve_symlinks={preserve_symlinks}, preserve_permissions={preserve_permissions}, sudo={sudo})")

        # Normalize paths
        source_path = source_path.rstrip('/')

        # Check if destination exists and handle overwrite
        check_dest_cmd = f"[ -d {shlex.quote(destination_path)} ] && echo 'exists' || echo 'not_exists'"
        check_handle = self.ssh_client.run(check_dest_cmd, io_timeout=30, sudo=sudo)
        # Exact match, not 'in' - 'not_exists' contains 'exists' as a substring, so a
        # naive 'exists' in ... check is always True regardless of the real answer.
        dest_exists = check_handle.last_nonblank() == 'exists'

        if dest_exists and overwrite:
            # Remove existing destination if overwrite is True
            self.logger.info(f"Removing existing destination for overwrite")
            rm_cmd = f"rm -rf {shlex.quote(destination_path)}"
            self.ssh_client.run(rm_cmd, io_timeout=60, runtime_timeout=300, sudo=sudo)

        # Create destination directory
        mkdir_cmd = f"mkdir -p {shlex.quote(destination_path)}"
        self.ssh_client.run(mkdir_cmd, io_timeout=30, sudo=sudo)

        # Copy the CONTENTS of source into destination with one cp: 'src/.' includes
        # hidden files, -P keeps symlinks as symlinks (-L follows them), -p preserves
        # mode/timestamps. Works the same with GNU, BSD (macOS) and BusyBox cp.
        # (This used to be 'cd src && find . -type f -o -type d | xargs -I{} cp -a {} dest/',
        # which copied every subdirectory AND every file inside it into the destination
        # root, so nested files were duplicated, flattened, at the top level - found
        # 2026-09-27 while fixing issues/_archive_/2026-09-26-linux-dir-size-counts-directory-bytes.md.
        # The other branch used 'src/*', which skipped hidden files.)
        cp_opts = ["-R", "-P" if preserve_symlinks else "-L"]
        if preserve_permissions:
            cp_opts.append("-p")
        cp_cmd = (f"cp {' '.join(cp_opts)} {shlex.quote(source_path + '/.')} "
                  f"{shlex.quote(destination_path + '/')}")

        try:
            # Execute the copy command
            handle = self.ssh_client.run(cp_cmd, io_timeout=300, runtime_timeout=1800, sudo=sudo)

            if handle.exit_code != 0:
                self.logger.error(f"Failed to copy directory: {handle.tail(5)}")
                return {
                    'status': 'error',
                    'message': f"Failed to copy directory, exit code: {handle.exit_code}",
                    'files_copied': 0,
                    'bytes_copied': 0,
                    'destination_path': destination_path
                }

            # Count files copied by listing destination
            count_cmd = f"find {shlex.quote(destination_path)} -type f | wc -l"
            count_handle = self.ssh_client.run(count_cmd, io_timeout=60, sudo=sudo)
            try:
                files_copied = int(count_handle.last_nonblank())
            except (ValueError, IndexError):
                files_copied = -1

            # Get total size of destination
            size_cmd = self._cmd_dir_size(destination_path)
            size_handle = self.ssh_client.run(size_cmd, io_timeout=60, sudo=sudo)

            try:
                bytes_copied = int(size_handle.last_nonblank())
            except (ValueError, IndexError):
                bytes_copied = -1

            self.logger.info(f"Successfully copied {files_copied} files ({bytes_copied} bytes) to {destination_path}")
            return {
                'status': 'success',
                'files_copied': files_copied,
                'bytes_copied': bytes_copied,
                'destination_path': destination_path
            }

        except Exception as e:
            self.logger.error(f"Error copying directory: {e}", exc_info=True)
            return {
                'status': 'error',
                'message': str(e),
                'files_copied': 0,
                'bytes_copied': 0,
                'destination_path': destination_path
            }


class SshDirectoryOperations_Linux(SshDirectoryOperations):
    """Linux implementation of directory operations using GNU coreutils."""

    def _cmd_find_with_type(self, path: str, name_pattern: str, max_depth: Optional[int], include_dirs: bool) -> str:
        """Return find command printing path<TAB>type: GNU find -printf, or where that's
        missing (BusyBox: Alpine, OpenWrt) a POSIX sh loop over 'find -exec ... {} +' -
        ssh_dir_search_glob used to refuse outright on those hosts (2026-09-28)."""
        cmd_parts = ["find", shlex.quote(path)]

        if max_depth is not None:
            cmd_parts.append(f"-maxdepth {max_depth}")

        cmd_parts.append(f"-name {shlex.quote(name_pattern)}")

        if not include_dirs:
            cmd_parts.append("-type f")

        if self.ssh_client.capabilities.get('find_printf', True):
            # GNU find -printf: %p=path, %y=type
            cmd_parts.append("-printf '%p\\t%y\\n'")
        else:
            cmd_parts.append(
                "-exec sh -c 'for f; do if [ -L \"$f\" ]; then t=l; elif [ -d \"$f\" ]; then t=d; "
                "elif [ -f \"$f\" ]; then t=f; elif [ -p \"$f\" ]; then t=p; elif [ -S \"$f\" ]; then t=s; "
                "else t=f; fi; printf \"%s\\t%s\\n\" \"$f\" \"$t\"; done' _ {} +")

        return " ".join(cmd_parts)

    def _cmd_dir_size(self, path: str) -> str:
        """Sum of the sizes of all regular files under path, in bytes - the documented
        contract, and what the macOS version computes. (It used 'du -sb', which also
        counts each directory's own size: 17 directories added 69,632 bytes to a
        2,001-file tree - issues/_archive_/2026-09-26-linux-dir-size-counts-directory-bytes.md.)"""
        if self.ssh_client.capabilities.get('find_printf', True):
            return (f"find {shlex.quote(path)} -type f -printf '%s\\n' | "
                    f"awk '{{s+=$1}} END {{printf \"%.0f\\n\", s}}'")
        # BusyBox (Alpine, OpenWrt) has no find -printf: sum the size column of
        # 'ls -ln' (POSIX; field 5 = size, before the name, so odd names are fine).
        # Without this, ssh_dir_copy silently reported bytes_copied: 0 there, and
        # ssh_dir_calc_size refused to run (found 2026-09-28).
        return (f"find {shlex.quote(path)} -type f -exec ls -ln {{}} + | "
                f"awk '{{s+=$5}} END {{printf \"%.0f\\n\", s}}'")

    def _cmd_list_with_metadata(self, path: str, max_depth: Optional[int]) -> str:
        """Return find command with -printf for full metadata."""
        cmd_parts = ["find", shlex.quote(path)]

        if max_depth is not None:
            cmd_parts.append(f"-maxdepth {max_depth}")

        # GNU find -printf: %p=path, %y=type, %s=size, %T@=mtime, %m=perms, %u=user, %g=group
        cmd_parts.append("-printf '%p\\t%y\\t%s\\t%T@\\t%m\\t%u\\t%g\\n'")

        return " ".join(cmd_parts)

    def _cmd_file_size(self, path: str) -> str:
        """Return stat command for file size (POSIX `wc -c` where GNU `stat -c` isn't
        confirmed - OpenWrt's BusyBox has no `stat` at all)."""
        if self.ssh_client.capabilities.get('stat_c', True):
            return f"stat -c %s {shlex.quote(path)}"
        return f"wc -c < {shlex.quote(path)}"

    def _cmd_find_symlinks(self, path: str) -> str:
        """Return find command for symlinks with targets."""
        # GNU find -printf: %p=path, %l=link target
        return f"find {shlex.quote(path)} -type l -printf '%p\\t%l\\n'"


class SshDirectoryOperations_Mac(SshDirectoryOperations):
    """macOS implementation of directory operations using BSD coreutils."""

    def _cmd_find_with_type(self, path: str, name_pattern: str, max_depth: Optional[int], include_dirs: bool) -> str:
        """Return find command with stat for path and type (BSD find has no -printf)."""
        cmd_parts = ["find", shlex.quote(path)]

        if max_depth is not None:
            cmd_parts.append(f"-maxdepth {max_depth}")

        cmd_parts.append(f"-name {shlex.quote(name_pattern)}")

        if not include_dirs:
            cmd_parts.append("-type f")

        # BSD find doesn't have -printf, use -exec stat instead
        # Output format: path<tab>type (f=file, d=directory, l=symlink)
        # Note: macOS stat doesn't interpret \t, so we use printf to get a real tab character
        cmd_parts.append("-exec sh -c 'TAB=$(printf \"\\t\"); for f; do t=$(stat -f %HT \"$f\" 2>/dev/null | cut -c1 | tr \"DRLS\" \"dflL\"); echo \"$f${TAB}${t:-f}\"; done' _ {} +")

        return " ".join(cmd_parts)

    def _cmd_dir_size(self, path: str) -> str:
        """Return command for directory size in bytes (BSD du has no -b flag)."""
        # Use find + stat to sum file sizes accurately
        return f"find {shlex.quote(path)} -type f -exec stat -f %z {{}} + 2>/dev/null | awk '{{s+=$1}} END {{print s+0}}'"

    def _cmd_list_with_metadata(self, path: str, max_depth: Optional[int]) -> str:
        """Return find command with stat for full metadata (BSD find has no -printf)."""
        cmd_parts = ["find", shlex.quote(path)]

        if max_depth is not None:
            cmd_parts.append(f"-maxdepth {max_depth}")

        # BSD stat: %N=name, %HT=type, %z=size, %m=mtime, %Lp=perms(octal), %Su=user, %Sg=group
        # Output format: path<tab>type<tab>size<tab>mtime<tab>perms<tab>user<tab>group
        # Note: macOS stat doesn't interpret \t, so we use printf to get a real tab character
        cmd_parts.append("-exec sh -c 'TAB=$(printf \"\\t\"); for f; do stat -f \"%N${TAB}%HT${TAB}%z${TAB}%m${TAB}%Lp${TAB}%Su${TAB}%Sg\" \"$f\" 2>/dev/null | sed \"s/Directory/d/;s/Regular File/f/;s/Symbolic Link/l/\"; done' _ {} +")

        return " ".join(cmd_parts)

    def _cmd_file_size(self, path: str) -> str:
        """Return stat command for file size (BSD stat)."""
        return f"stat -f %z {shlex.quote(path)}"

    def _cmd_find_symlinks(self, path: str) -> str:
        """Return find command for symlinks with targets (BSD find has no -printf)."""
        # Use find + readlink to get symlink targets
        return f"find {shlex.quote(path)} -type l -exec sh -c 'for f; do echo \"$f\\t$(readlink \"$f\")\"; done' _ {{}} +"


class SshDirectoryOperations_Win(SshDirectoryOperations):
    """Windows implementation of directory operations using PowerShell."""

    def delete_directory_recursive(self,
                                  path: str,
                                  dry_run: bool = True,
                                  sudo: bool = False) -> Dict[str, Any]:
        """Delete a directory and all contents using PowerShell."""
        self.logger.info(f"Deleting directory: {path} (dry_run={dry_run}, sudo={sudo})")

        # Safety check - don't allow deleting critical directories
        path = path.rstrip('\\').rstrip('/')
        critical_paths = ['C:', 'C:\\', 'C:\\Windows', 'C:\\Users', f'C:\\Users\\{self.ssh_client.user}']
        if path.upper() in [p.upper() for p in critical_paths]:
            error_msg = f"Refusing to delete critical directory: {path}"
            self.logger.error(error_msg)
            return {'status': 'error', 'error': error_msg, 'deleted_items': []}

        ps_path = path.replace("'", "''")

        try:
            # List what would be deleted
            list_cmd = powershell_encoded_command(
                f"Get-ChildItem -Path '{ps_path}' -Recurse -Force -ErrorAction SilentlyContinue | ForEach-Object {{ $_.FullName }}"
            )
            list_handle = self.ssh_client.run(list_cmd, io_timeout=120, runtime_timeout=300)
            items = [line.strip() for line in list_handle.tail(list_handle.total_lines) if line.strip()]
            items.append(path)  # Include the directory itself

            if dry_run:
                self.logger.info(f"Dry run - would delete {len(items)} items")
                return {'status': 'success', 'dry_run': True, 'deleted_items': items}

            # Perform deletion
            delete_cmd = powershell_encoded_command(f"Remove-Item -Path '{ps_path}' -Recurse -Force -ErrorAction Stop")
            self.ssh_client.run(delete_cmd, io_timeout=120, runtime_timeout=300)

            self.logger.info(f"Successfully deleted {len(items)} items")
            return {'status': 'success', 'deleted_items': items}

        except Exception as e:
            self.logger.error(f"Error deleting directory: {e}", exc_info=True)
            return {'status': 'error', 'error': str(e), 'deleted_items': []}

    def batch_delete_by_pattern(self,
                               path: str,
                               pattern: str,
                               dry_run: bool = True,
                               sudo: bool = False) -> Dict[str, Any]:
        """Delete files matching a pattern using PowerShell."""
        self.logger.info(f"Batch deleting files matching '{pattern}' in {path} (dry_run={dry_run})")

        ps_path = path.replace("'", "''")
        ps_pattern = pattern.replace("'", "''")

        try:
            # Find matching files
            find_cmd = powershell_encoded_command(
                f"Get-ChildItem -Path '{ps_path}' -Recurse -Filter '{ps_pattern}' -File -ErrorAction SilentlyContinue | ForEach-Object {{ $_.FullName }}"
            )
            find_handle = self.ssh_client.run(find_cmd, io_timeout=120, runtime_timeout=300)
            files = [line.strip() for line in find_handle.tail(find_handle.total_lines) if line.strip()]

            if dry_run:
                self.logger.info(f"Dry run - would delete {len(files)} files")
                return {'status': 'success', 'dry_run': True, 'deleted_files': files}

            if not files:
                self.logger.info("No matching files found to delete")
                return {'status': 'success', 'deleted_files': []}

            # Delete matching files
            delete_cmd = powershell_encoded_command(
                f"Get-ChildItem -Path '{ps_path}' -Recurse -Filter '{ps_pattern}' -File -ErrorAction SilentlyContinue | Remove-Item -Force"
            )
            self.ssh_client.run(delete_cmd, io_timeout=120, runtime_timeout=300)

            self.logger.info(f"Successfully deleted {len(files)} files")
            return {'status': 'success', 'deleted_files': files}

        except Exception as e:
            self.logger.error(f"Error batch deleting files: {e}", exc_info=True)
            return {'status': 'error', 'error': str(e), 'deleted_files': []}

    def safe_move_or_rename(self,
                           source: str,
                           destination: str,
                           overwrite: bool = False,
                           sudo: bool = False) -> Dict[str, Any]:
        """Move or rename using PowerShell."""
        self.logger.info(f"Moving {source} to {destination} (overwrite={overwrite})")

        ps_source = source.replace("'", "''")
        ps_dest = destination.replace("'", "''")

        try:
            # Check if source exists
            check_src_cmd = powershell_encoded_command(f"if (Test-Path '{ps_source}') {{ 'exists' }} else {{ 'not_exists' }}")
            check_src = self.ssh_client.run(check_src_cmd, io_timeout=30)
            if 'not_exists' in check_src.last_nonblank():
                return {'success': False, 'message': f"Source does not exist: {source}"}

            # Check if destination exists
            check_dst_cmd = powershell_encoded_command(f"if (Test-Path '{ps_dest}') {{ 'exists' }} else {{ 'not_exists' }}")
            check_dst = self.ssh_client.run(check_dst_cmd, io_timeout=30)
            # Exact match, not 'in' - 'not_exists' contains 'exists' as a substring, so a
            # naive 'exists' in ... check is always True regardless of the real answer
            # (this is exactly what made every Windows move report a false "destination
            # already exists", verified live 2026-07-03).
            dest_exists = check_dst.last_nonblank() == 'exists'

            if dest_exists and not overwrite:
                return {'success': False, 'message': f"Destination exists and overwrite not allowed: {destination}"}

            # Perform move
            force_flag = "-Force" if overwrite else ""
            move_cmd = powershell_encoded_command(f"Move-Item -Path '{ps_source}' -Destination '{ps_dest}' {force_flag} -ErrorAction Stop")
            self.ssh_client.run(move_cmd, io_timeout=120, runtime_timeout=300)

            self.logger.info(f"Successfully moved {source} to {destination}")
            return {'success': True, 'message': f"Successfully moved {source} to {destination}"}

        except Exception as e:
            self.logger.error(f"Error moving/renaming: {e}", exc_info=True)
            return {'success': False, 'message': str(e)}

    def create_archive_from_directory(self,
                                     source_path: str,
                                     archive_path: str,
                                     format: str = "tar.gz",
                                     sudo: bool = False,
                                     parent_tool: Optional[str] = None) -> Dict[str, Any]:
        """Create archive using PowerShell Compress-Archive (zip format on Windows).

        parent_tool: when set, tags every command this issues as internal
        plumbing owned by that tool (e.g. 'ssh_dir_transfer') - see
        ssh_cmd_history's include_internal filter.
        """
        self.logger.info(f"Creating archive from {source_path} to {archive_path}")
        history_tag = {'origin': 'tool_internal', 'parent_tool': parent_tool} if parent_tool else {}

        # Windows native is zip; tar.gz would need external tools
        if format not in ["zip", "tar.gz", "tar"]:
            return {'status': 'error', 'message': f"Unsupported format: {format}"}

        ps_source = source_path.replace("'", "''")
        ps_archive = archive_path.replace("'", "''")

        # For tar formats, change extension to .zip and warn
        if format in ["tar.gz", "tar"]:
            self.logger.warning(f"Windows using zip format instead of {format}")
            if ps_archive.endswith('.tar.gz'):
                ps_archive = ps_archive[:-7] + '.zip'
            elif ps_archive.endswith('.tar'):
                ps_archive = ps_archive[:-4] + '.zip'

        try:
            # Archive the directory itself (not contents) so structure matches Linux tar behavior
            # Compress-Archive with a directory path includes the directory name in the archive
            cmd = powershell_encoded_command(f"Compress-Archive -Path '{ps_source}' -DestinationPath '{ps_archive}' -Force -ErrorAction Stop")
            self.ssh_client.run(cmd, io_timeout=300, runtime_timeout=1800, **history_tag)

            # Get archive size
            size_cmd = powershell_encoded_command(f"(Get-Item '{ps_archive}').Length")
            size_handle = self.ssh_client.run(size_cmd, io_timeout=30, **history_tag)
            try:
                archive_size = int(size_handle.last_nonblank())
            except (ValueError, IndexError):
                archive_size = -1

            return {
                'status': 'success',
                'success': True,
                'archive_created': ps_archive.replace("''", "'"),
                'format': 'zip',
                'size_bytes': archive_size
            }

        except Exception as e:
            self.logger.error(f"Error creating archive: {e}", exc_info=True)
            return {'status': 'error', 'message': str(e)}

    def extract_archive_to_directory(self,
                                    archive_path: str,
                                    destination_path: str,
                                    overwrite: bool = False,
                                    sudo: bool = False,
                                    parent_tool: Optional[str] = None) -> Dict[str, Any]:
        """Extract archive using PowerShell Expand-Archive.

        Note: This strips the first component of the archive path to match
        Linux tar behavior with --strip-components=1.

        parent_tool: when set, tags the command this issues as internal
        plumbing owned by that tool (e.g. 'ssh_dir_transfer') - see
        ssh_cmd_history's include_internal filter.
        """
        self.logger.info(f"Extracting {archive_path} to {destination_path}")
        history_tag = {'origin': 'tool_internal', 'parent_tool': parent_tool} if parent_tool else {}

        ps_archive = archive_path.replace("'", "''")
        ps_dest = destination_path.replace("'", "''")

        # Only zip is natively supported
        if not archive_path.endswith('.zip'):
            return {
                'status': 'error',
                'message': f"Only .zip format supported on Windows. Got: {archive_path}",
                'extracted_files': []
            }

        try:
            # Extract to a temp location first, then move contents up to strip first component
            # This mimics Linux tar's --strip-components=1 behavior
            force_flag = "-Force" if overwrite else ""

            # PowerShell script (single-line to work through SSH/CMD)
            # 1. Extract to temp dir
            # 2. Get the single top-level folder (the "component" to strip)
            # 3. Move its contents to the actual destination
            # 4. Clean up temp dir
            # NOTE: Must be single-line because multi-line strings don't work through SSH->CMD->PowerShell
            # File list is captured from the temp extraction dir (i.e. actual archive
            # members) rather than the destination dir, which may already contain
            # unrelated pre-existing files (e.g. the archive itself, if the source
            # directory was archived in place).
            strip_script = (
                f"$tempDir = Join-Path $env:TEMP ('ssh_extract_' + [guid]::NewGuid().ToString('N')); "
                f"New-Item -ItemType Directory -Path $tempDir -Force | Out-Null; "
                f"Expand-Archive -Path '{ps_archive}' -DestinationPath $tempDir {force_flag} -ErrorAction Stop; "
                f"$items = Get-ChildItem -Path $tempDir; "
                f"if ($items.Count -eq 1 -and $items[0].PSIsContainer) {{ "
                f"$innerPath = $items[0].FullName; "
                f"$extracted = Get-ChildItem -Path $innerPath -Recurse -File | ForEach-Object {{ $_.FullName.Substring($innerPath.Length + 1) }}; "
                f"New-Item -ItemType Directory -Path '{ps_dest}' -Force | Out-Null; "
                f"Get-ChildItem -Path $innerPath | Move-Item -Destination '{ps_dest}' -Force "
                f"}} else {{ "
                f"$extracted = Get-ChildItem -Path $tempDir -Recurse -File | ForEach-Object {{ $_.FullName.Substring($tempDir.Length + 1) }}; "
                f"New-Item -ItemType Directory -Path '{ps_dest}' -Force | Out-Null; "
                f"Get-ChildItem -Path $tempDir | Move-Item -Destination '{ps_dest}' -Force "
                f"}}; "
                f"Remove-Item -Path $tempDir -Recurse -Force -ErrorAction SilentlyContinue; "
                f"$extracted | ForEach-Object {{ Write-Output $_ }}"
            )
            cmd = powershell_encoded_command(strip_script)
            extract_handle = self.ssh_client.run(cmd, io_timeout=300, runtime_timeout=1800, **history_tag)
            files = [line.strip() for line in extract_handle.tail(extract_handle.total_lines) if line.strip()]

            return {
                'status': 'success',
                'success': True,
                'extracted_files': files,
                'destination_path': destination_path
            }

        except Exception as e:
            self.logger.error(f"Error extracting archive: {e}", exc_info=True)
            return {'status': 'error', 'message': str(e), 'extracted_files': []}

    # Files bigger than this are skipped (and reported) by the Windows content search
    SEARCH_MAX_FILE_BYTES = 10 * 1024 * 1024

    def search_file_contents(self,
                            path: str,
                            pattern: str,
                            regex: bool = False,
                            case_sensitive: bool = True,
                            sudo: bool = False) -> List[Dict[str, Any]]:
        """Search file contents entirely over ONE SFTP session: walk the tree with
        SFTP directory listings, read each file's raw bytes, match locally in Python.

        - Filenames come from SFTP, which returns them as proper UTF-8. (They used to
          come from PowerShell's stdout, in Windows' OEM console code page: 'cafe' with
          an accent came back garbled, reading the garbled path failed, and the file
          was silently skipped - a false "no match", verified 2026-09-27.)
        - One SFTP session for the whole search: opening a new session per file cost
          ~0.4s each (300 small files took ~2 minutes); reads on one session take
          milliseconds.
        - Content is read as bytes and decoded client-side (UTF-8, BOM tolerated), so
          non-ASCII content is never corrupted either.
        - Nothing is skipped silently: unreadable directories/files and files over
          SEARCH_MAX_FILE_BYTES are recorded in self.last_search_skipped, which
          ssh_dir_search_files_content reports as an 'incomplete' result.

        Regex flavor is Python's `re`.
        """
        import stat as stat_module
        self.logger.info(f"Searching for '{pattern}' in files under {path}")
        self.last_search_skipped = []
        root = path.replace('/', '\\').rstrip('\\') or path
        if root.endswith(':'):
            root += '\\'  # a drive root like C:\ - 'C:' alone means the current dir on C:
        compiled_pattern = None
        if regex:
            compiled_pattern = re.compile(pattern, 0 if case_sensitive else re.IGNORECASE)
        needle = pattern if case_sensitive else pattern.lower()

        sftp = self.ssh_client.open_sftp()
        try:
            try:
                root_attr = sftp.stat(root)
            except Exception as e:
                raise SshError(f"Content search failed: can't access '{path}': {e}")
            if not stat_module.S_ISDIR(root_attr.st_mode):
                raise SshError(f"Content search failed: '{path}' is not a directory")

            results = []
            files_searched = 0
            pending = [root]
            while pending:
                directory = pending.pop()
                try:
                    entries = sftp.listdir_attr(directory)
                except Exception as e:
                    self.last_search_skipped.append({'path': directory, 'reason': f"can't list directory: {e}"})
                    continue
                for entry in sorted(entries, key=lambda item: item.filename):
                    full_path = f"{directory}\\{entry.filename}"
                    mode = entry.st_mode or 0
                    if stat_module.S_ISDIR(mode):
                        pending.append(full_path)
                        continue
                    if not stat_module.S_ISREG(mode):
                        continue
                    if entry.st_size and entry.st_size > self.SEARCH_MAX_FILE_BYTES:
                        self.last_search_skipped.append({
                            'path': full_path,
                            'reason': f"larger than {self.SEARCH_MAX_FILE_BYTES // (1024 * 1024)} MB"})
                        continue
                    try:
                        with sftp.open(full_path, 'rb') as handle:
                            raw = handle.read()
                    except Exception as e:
                        self.last_search_skipped.append({'path': full_path, 'reason': f"can't read: {e}"})
                        continue
                    files_searched += 1
                    report_progress(items={'files_searched': files_searched})
                    content = raw.decode('utf-8-sig', errors='replace')
                    for line_num, line in enumerate(content.splitlines(), start=1):
                        if regex:
                            is_match = compiled_pattern.search(line) is not None
                        elif case_sensitive:
                            is_match = needle in line
                        else:
                            is_match = needle in line.lower()
                        if is_match:
                            results.append({'file': full_path, 'line': line_num, 'content': line})
            self.logger.info(f"Found {len(results)} matches, skipped {len(self.last_search_skipped)} entries")
            return results
        except Exception as e:
            self.logger.error(f"Error searching file contents: {e}", exc_info=True)
            raise
        finally:
            sftp.close()

    def copy_directory_recursive(self,
                                source_path: str,
                                destination_path: str,
                                overwrite: bool = False,
                                preserve_symlinks: bool = True,
                                preserve_permissions: bool = True,
                                sudo: bool = False) -> Dict[str, Any]:
        """Copy directory recursively using PowerShell."""
        self.logger.info(f"Copying {source_path} to {destination_path}")

        ps_source = source_path.replace("'", "''")
        ps_dest = destination_path.replace("'", "''")

        try:
            # Remove destination if overwrite
            if overwrite:
                rm_cmd = powershell_encoded_command(f"if (Test-Path '{ps_dest}') {{ Remove-Item -Path '{ps_dest}' -Recurse -Force }}")
                self.ssh_client.run(rm_cmd, io_timeout=60, runtime_timeout=300)

            # Copy directory
            cmd = powershell_encoded_command(f"Copy-Item -Path '{ps_source}' -Destination '{ps_dest}' -Recurse -Force -ErrorAction Stop")
            self.ssh_client.run(cmd, io_timeout=300, runtime_timeout=1800)

            # Count files and size
            count_cmd = powershell_encoded_command(f"(Get-ChildItem -Path '{ps_dest}' -Recurse -File).Count")
            count_handle = self.ssh_client.run(count_cmd, io_timeout=60)
            try:
                files_copied = int(count_handle.last_nonblank())
            except (ValueError, IndexError):
                files_copied = -1

            size_cmd = powershell_encoded_command(f"(Get-ChildItem -Path '{ps_dest}' -Recurse -File | Measure-Object -Property Length -Sum).Sum")
            size_handle = self.ssh_client.run(size_cmd, io_timeout=60)
            try:
                bytes_copied = int(size_handle.last_nonblank())
            except (ValueError, IndexError):
                bytes_copied = -1

            return {
                'status': 'success',
                'files_copied': files_copied,
                'bytes_copied': bytes_copied,
                'destination_path': destination_path
            }

        except Exception as e:
            self.logger.error(f"Error copying directory: {e}", exc_info=True)
            return {
                'status': 'error',
                'message': str(e),
                'files_copied': 0,
                'bytes_copied': 0,
                'destination_path': destination_path
            }

    def _cmd_find_with_type(self, path: str, name_pattern: str, max_depth: Optional[int], include_dirs: bool) -> str:
        """Return PowerShell command to find files with path and type."""
        # Build PowerShell Get-ChildItem command
        depth_param = f"-Depth {max_depth}" if max_depth is not None else ""

        # Escape path for PowerShell
        ps_path = path.replace("'", "''")
        ps_pattern = name_pattern.replace("'", "''")

        if include_dirs:
            # Include both files and directories
            script = (
                f"Get-ChildItem -Path '{ps_path}' -Recurse {depth_param} -Filter '{ps_pattern}' -ErrorAction SilentlyContinue | "
                "ForEach-Object { $t = if ($_.PSIsContainer) { 'd' } else { 'f' }; \"$($_.FullName)`t$t\" }"
            )
        else:
            # Files only
            script = (
                f"Get-ChildItem -Path '{ps_path}' -Recurse {depth_param} -Filter '{ps_pattern}' -File -ErrorAction SilentlyContinue | "
                'ForEach-Object { "$($_.FullName)`tf" }'
            )

        return powershell_encoded_command(script)

    def _cmd_dir_size(self, path: str) -> str:
        """Return PowerShell command for directory size in bytes."""
        ps_path = path.replace("'", "''")
        return powershell_encoded_command(f"(Get-ChildItem -Path '{ps_path}' -Recurse -File -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum")

    def _cmd_list_with_metadata(self, path: str, max_depth: Optional[int]) -> str:
        """Return PowerShell command to list files with metadata."""
        ps_path = path.replace("'", "''")
        depth_param = f"-Depth {max_depth}" if max_depth is not None else ""

        # Output format: path<tab>type<tab>size<tab>mtime<tab>perms<tab>user<tab>group
        # Windows doesn't have Unix perms or group, so we'll use placeholder values
        script = (
            f"Get-ChildItem -Path '{ps_path}' -Recurse {depth_param} -ErrorAction SilentlyContinue | ForEach-Object {{ "
            "$t = if ($_.PSIsContainer) { 'd' } else { 'f' }; "
            "$s = if ($_.PSIsContainer) { 0 } else { $_.Length }; "
            "$m = [int][double]::Parse((Get-Date $_.LastWriteTimeUtc -UFormat %s)); "
            "$owner = try { $_.GetAccessControl().Owner } catch { 'unknown' }; "
            '"$($_.FullName)`t$t`t$s`t$m`t0`t$owner`tunknown" }'
        )

        return powershell_encoded_command(script)

    def _cmd_file_size(self, path: str) -> str:
        """Return PowerShell command for file size."""
        ps_path = path.replace("'", "''")
        return powershell_encoded_command(f"(Get-Item -Path '{ps_path}' -ErrorAction SilentlyContinue).Length")

    def _cmd_find_symlinks(self, path: str) -> str:
        """Return PowerShell command to find symlinks (reparse points) with targets."""
        ps_path = path.replace("'", "''")
        # Windows symlinks are represented as ReparsePoints
        script = (
            f"Get-ChildItem -Path '{ps_path}' -Recurse -ErrorAction SilentlyContinue | "
            "Where-Object { $_.Attributes -match 'ReparsePoint' } | ForEach-Object { "
            "$target = try { (Get-Item $_.FullName).Target } catch { 'unknown' }; "
            '"$($_.FullName)`t$target" }'
        )
        return powershell_encoded_command(script)
