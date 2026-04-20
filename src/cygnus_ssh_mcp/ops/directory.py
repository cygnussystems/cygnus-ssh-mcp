import os
import re
import shlex
import logging
from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any


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
        source_exists = 'exists' in source_check.tail(1)[0]

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
            destination_exists = check_handle.tail(1)[0].strip() == 'exists'

            # Debug log the actual check result
            self.logger.debug(f"Destination check result: '{check_handle.tail(1)[0].strip()}', exists={destination_exists}")

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
                                     sudo: bool = False) -> Dict[str, Any]:
        """
        Create a compressed archive (tar.gz or tar) from a directory.

        Args:
            source_path: Directory to archive
            archive_path: Where to write the archive
            format: "tar.gz" or "tar"
            sudo: Whether to use sudo for the operation

        Returns:
            Dict with status and archive path
        """
        self.logger.info(f"Creating {format} archive from {source_path} to {archive_path} (sudo={sudo})")

        # Validate format
        if format not in ["tar.gz", "tar"]:
            error_msg = f"Unsupported archive format: {format}. Use 'tar.gz' or 'tar'."
            self.logger.error(error_msg)
            return {
                'status': 'error',
                'message': error_msg
            }

        try:
            # Get directory name without trailing slash
            source_dir = source_path.rstrip('/')
            parent_dir = os.path.dirname(source_dir)
            base_name = os.path.basename(source_dir)

            # Create archive based on format
            if format == "tar.gz":
                # Create tar.gz archive (compressed)
                cmd = f"tar -czf {shlex.quote(archive_path)} -C {shlex.quote(parent_dir)} {shlex.quote(base_name)}"
                handle = self.ssh_client.run(cmd, io_timeout=300, runtime_timeout=1800, sudo=sudo)
            else:  # tar
                # Create tar archive (uncompressed)
                cmd = f"tar -cf {shlex.quote(archive_path)} -C {shlex.quote(parent_dir)} {shlex.quote(base_name)}"
                handle = self.ssh_client.run(cmd, io_timeout=300, runtime_timeout=1800, sudo=sudo)

            if handle.exit_code != 0:
                self.logger.error(f"Failed to create archive: {handle.tail(5)}")
                return {
                    'status': 'error',
                    'message': f"Failed to create archive, exit code: {handle.exit_code}"
                }

            # Verify the archive was created
            verify_cmd = f"[ -f {shlex.quote(archive_path)} ] && echo 'exists' || echo 'not_exists'"
            verify_handle = self.ssh_client.run(verify_cmd, io_timeout=30, sudo=sudo)

            if 'exists' not in verify_handle.tail(1)[0]:
                self.logger.error(f"Archive was not created at {archive_path}")
                return {
                    'status': 'error',
                    'message': f"Archive was not created at {archive_path}"
                }

            # Get archive size
            size_cmd = self._cmd_file_size(archive_path)
            size_handle = self.ssh_client.run(size_cmd, io_timeout=30, sudo=sudo)

            try:
                archive_size = int(size_handle.tail(1)[0].strip())
            except (ValueError, IndexError):
                archive_size = -1

            self.logger.info(f"Successfully created archive at {archive_path} ({archive_size} bytes)")
            return {
                'status': 'success',
                'success': True,  # Add this for compatibility with tests
                'archive_created': archive_path,
                'format': format,
                'size_bytes': archive_size
            }

        except Exception as e:
            self.logger.error(f"Error creating archive: {e}", exc_info=True)
            return {
                'status': 'error',
                'message': str(e)
            }

    def extract_archive_to_directory(self,
                                    archive_path: str,
                                    destination_path: str,
                                    overwrite: bool = False,
                                    sudo: bool = False) -> Dict[str, Any]:
        """
        Extract a tar or tar.gz archive to a directory.

        Args:
            archive_path: Path to archive file
            destination_path: Extract location
            overwrite: Whether to overwrite existing files
            sudo: Whether to use sudo for the operation

        Returns:
            Dict with status and list of extracted files
        """
        self.logger.info(f"Extracting archive {archive_path} to {destination_path} (overwrite={overwrite}, sudo={sudo})")

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
            self.ssh_client.run(mkdir_cmd, io_timeout=30, sudo=sudo)

            # List files in the archive before extraction
            if archive_type == 'tar.gz':
                list_cmd = f"tar -tzf {shlex.quote(archive_path)}"
            else:  # tar
                list_cmd = f"tar -tf {shlex.quote(archive_path)}"

            list_handle = self.ssh_client.run(list_cmd, io_timeout=120, runtime_timeout=300, sudo=sudo)

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

            extract_handle = self.ssh_client.run(extract_cmd, io_timeout=300, runtime_timeout=1800, sudo=sudo)

            # Check for non-zero exit code but handle the special case for tar --keep-old-files
            # which exits with code 1 if files already exist
            if extract_handle.exit_code != 0:
                if archive_type == 'tar.gz' and not overwrite and extract_handle.exit_code == 1:
                    # This is expected with --keep-old-files if files exist
                    self.logger.warning("Some files already exist and were not overwritten")
                else:
                    self.logger.error(f"Failed to extract archive: {extract_handle.tail(5)}")
                    return {
                        'status': 'error',
                        'message': f"Failed to extract archive, exit code: {extract_handle.exit_code}",
                        'extracted_files': []
                    }

            self.logger.info(f"Successfully extracted {len(files)} files to {destination_path}")
            return {
                'status': 'success',
                'success': True,  # Add this for compatibility with tests
                'extracted_files': files,
                'destination_path': destination_path
            }

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

        # Build grep command with appropriate options
        grep_opts = []
        if regex:
            grep_opts.append("-E")  # Extended regex
        if not case_sensitive:
            grep_opts.append("-i")  # Case insensitive

        # Add line number and filename options
        grep_opts.extend(["-n", "-H"])

        # Construct the full command
        # Using find to get all files and xargs to pass to grep
        # The grep command will return non-zero if no matches are found, which is not an error for us
        cmd = f"find {shlex.quote(path)} -type f -print0 | xargs -0 grep {' '.join(grep_opts)} {shlex.quote(pattern)} || [ $? -eq 1 ]"

        try:
            handle = self.ssh_client.run(cmd, io_timeout=300, runtime_timeout=1800, sudo=sudo)

            # Process the output
            results = []
            for line in handle.tail(handle.total_lines):
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
        dest_exists = 'exists' in check_handle.tail(1)[0]

        if dest_exists and overwrite:
            # Remove existing destination if overwrite is True
            self.logger.info(f"Removing existing destination for overwrite")
            rm_cmd = f"rm -rf {shlex.quote(destination_path)}"
            self.ssh_client.run(rm_cmd, io_timeout=60, runtime_timeout=300, sudo=sudo)

        # Create destination directory
        mkdir_cmd = f"mkdir -p {shlex.quote(destination_path)}"
        self.ssh_client.run(mkdir_cmd, io_timeout=30, sudo=sudo)

        # Build cp command with appropriate options
        cp_opts = ["-r"]  # Recursive copy

        if preserve_permissions:
            cp_opts.append("-p")  # Preserve mode, ownership, timestamps

        if preserve_symlinks:
            # Default behavior of cp is to follow symlinks, we need to handle them specially
            # First, copy everything except symlinks
            cp_cmd = f"cd {shlex.quote(source_path)} && find . -type f -o -type d | xargs -I{{}} cp -a {{}} {shlex.quote(destination_path)}/"
        else:
            # Use standard cp command
            cp_cmd = f"cp {' '.join(cp_opts)} {shlex.quote(source_path)}/* {shlex.quote(destination_path)}/"

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

            # If preserving symlinks, we need to recreate them
            if preserve_symlinks:
                # Find all symlinks in the source directory
                find_links_cmd = self._cmd_find_symlinks(source_path)
                links_handle = self.ssh_client.run(find_links_cmd, io_timeout=60, sudo=sudo)

                # Process each symlink
                for line in links_handle.tail(links_handle.total_lines):
                    if not line.strip():
                        continue

                    parts = line.strip().split('\t')
                    if len(parts) == 2:
                        link_path, target = parts
                        # Create relative path in destination - use posix paths
                        rel_path = os.path.relpath(link_path, source_path)
                        # Convert Windows backslashes to forward slashes for Linux
                        rel_path = rel_path.replace('\\', '/')
                        dest_link = f"{destination_path}/{rel_path}"

                        # Create the symlink in destination
                        ln_cmd = f"ln -sf {shlex.quote(target)} {shlex.quote(dest_link)}"
                        self.ssh_client.run(ln_cmd, io_timeout=30, sudo=sudo)

            # Count files copied by listing destination
            count_cmd = f"find {shlex.quote(destination_path)} -type f | wc -l"
            count_handle = self.ssh_client.run(count_cmd, io_timeout=60, sudo=sudo)
            try:
                files_copied = int(count_handle.tail(1)[0].strip())
            except (ValueError, IndexError):
                files_copied = -1

            # Get total size of destination
            size_cmd = self._cmd_dir_size(destination_path)
            size_handle = self.ssh_client.run(size_cmd, io_timeout=60, sudo=sudo)

            try:
                bytes_copied = int(size_handle.tail(1)[0].strip())
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
        """Return find command with -printf for path and type."""
        cmd_parts = ["find", shlex.quote(path)]

        if max_depth is not None:
            cmd_parts.append(f"-maxdepth {max_depth}")

        cmd_parts.append(f"-name {shlex.quote(name_pattern)}")

        if not include_dirs:
            cmd_parts.append("-type f")

        # GNU find -printf: %p=path, %y=type
        cmd_parts.append("-printf '%p\\t%y\\n'")

        return " ".join(cmd_parts)

    def _cmd_dir_size(self, path: str) -> str:
        """Return du command for directory size in bytes."""
        return f"du -sb {shlex.quote(path)} | cut -f1"

    def _cmd_list_with_metadata(self, path: str, max_depth: Optional[int]) -> str:
        """Return find command with -printf for full metadata."""
        cmd_parts = ["find", shlex.quote(path)]

        if max_depth is not None:
            cmd_parts.append(f"-maxdepth {max_depth}")

        # GNU find -printf: %p=path, %y=type, %s=size, %T@=mtime, %m=perms, %u=user, %g=group
        cmd_parts.append("-printf '%p\\t%y\\t%s\\t%T@\\t%m\\t%u\\t%g\\n'")

        return " ".join(cmd_parts)

    def _cmd_file_size(self, path: str) -> str:
        """Return stat command for file size."""
        return f"stat -c %s {shlex.quote(path)}"

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
            list_cmd = f'''powershell -Command "Get-ChildItem -Path '{ps_path}' -Recurse -Force -ErrorAction SilentlyContinue | ForEach-Object {{ $_.FullName }}"'''
            list_handle = self.ssh_client.run(list_cmd, io_timeout=120, runtime_timeout=300)
            items = [line.strip() for line in list_handle.tail(list_handle.total_lines) if line.strip()]
            items.append(path)  # Include the directory itself

            if dry_run:
                self.logger.info(f"Dry run - would delete {len(items)} items")
                return {'status': 'success', 'dry_run': True, 'deleted_items': items}

            # Perform deletion
            delete_cmd = f'''powershell -Command "Remove-Item -Path '{ps_path}' -Recurse -Force -ErrorAction Stop"'''
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
            find_cmd = f'''powershell -Command "Get-ChildItem -Path '{ps_path}' -Recurse -Filter '{ps_pattern}' -File -ErrorAction SilentlyContinue | ForEach-Object {{ $_.FullName }}"'''
            find_handle = self.ssh_client.run(find_cmd, io_timeout=120, runtime_timeout=300)
            files = [line.strip() for line in find_handle.tail(find_handle.total_lines) if line.strip()]

            if dry_run:
                self.logger.info(f"Dry run - would delete {len(files)} files")
                return {'status': 'success', 'dry_run': True, 'deleted_files': files}

            if not files:
                self.logger.info("No matching files found to delete")
                return {'status': 'success', 'deleted_files': []}

            # Delete matching files
            delete_cmd = f'''powershell -Command "Get-ChildItem -Path '{ps_path}' -Recurse -Filter '{ps_pattern}' -File -ErrorAction SilentlyContinue | Remove-Item -Force"'''
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
            check_src_cmd = f'''powershell -Command "if (Test-Path '{ps_source}') {{ 'exists' }} else {{ 'not_exists' }}"'''
            check_src = self.ssh_client.run(check_src_cmd, io_timeout=30)
            if 'not_exists' in check_src.tail(1)[0]:
                return {'success': False, 'message': f"Source does not exist: {source}"}

            # Check if destination exists
            check_dst_cmd = f'''powershell -Command "if (Test-Path '{ps_dest}') {{ 'exists' }} else {{ 'not_exists' }}"'''
            check_dst = self.ssh_client.run(check_dst_cmd, io_timeout=30)
            dest_exists = 'exists' in check_dst.tail(1)[0]

            if dest_exists and not overwrite:
                return {'success': False, 'message': f"Destination exists and overwrite not allowed: {destination}"}

            # Perform move
            force_flag = "-Force" if overwrite else ""
            move_cmd = f'''powershell -Command "Move-Item -Path '{ps_source}' -Destination '{ps_dest}' {force_flag} -ErrorAction Stop"'''
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
                                     sudo: bool = False) -> Dict[str, Any]:
        """Create archive using PowerShell Compress-Archive (zip format on Windows)."""
        self.logger.info(f"Creating archive from {source_path} to {archive_path}")

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
            cmd = f'''powershell -Command "Compress-Archive -Path '{ps_source}' -DestinationPath '{ps_archive}' -Force -ErrorAction Stop"'''
            self.ssh_client.run(cmd, io_timeout=300, runtime_timeout=1800)

            # Get archive size
            size_cmd = f'''powershell -Command "(Get-Item '{ps_archive}').Length"'''
            size_handle = self.ssh_client.run(size_cmd, io_timeout=30)
            try:
                archive_size = int(size_handle.tail(1)[0].strip())
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
                                    sudo: bool = False) -> Dict[str, Any]:
        """Extract archive using PowerShell Expand-Archive.

        Note: This strips the first component of the archive path to match
        Linux tar behavior with --strip-components=1.
        """
        self.logger.info(f"Extracting {archive_path} to {destination_path}")

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
            strip_script = (
                f"$tempDir = Join-Path $env:TEMP ('ssh_extract_' + [guid]::NewGuid().ToString('N')); "
                f"New-Item -ItemType Directory -Path $tempDir -Force | Out-Null; "
                f"Expand-Archive -Path '{ps_archive}' -DestinationPath $tempDir {force_flag} -ErrorAction Stop; "
                f"$items = Get-ChildItem -Path $tempDir; "
                f"if ($items.Count -eq 1 -and $items[0].PSIsContainer) {{ "
                f"$innerPath = $items[0].FullName; "
                f"New-Item -ItemType Directory -Path '{ps_dest}' -Force | Out-Null; "
                f"Get-ChildItem -Path $innerPath | Move-Item -Destination '{ps_dest}' -Force "
                f"}} else {{ "
                f"New-Item -ItemType Directory -Path '{ps_dest}' -Force | Out-Null; "
                f"Get-ChildItem -Path $tempDir | Move-Item -Destination '{ps_dest}' -Force "
                f"}}; "
                f"Remove-Item -Path $tempDir -Recurse -Force -ErrorAction SilentlyContinue"
            )
            cmd = f'powershell -Command "{strip_script}"'
            self.ssh_client.run(cmd, io_timeout=300, runtime_timeout=1800)

            # List extracted files
            list_cmd = f'''powershell -Command "Get-ChildItem -Path '{ps_dest}' -Recurse -File | ForEach-Object {{ $_.FullName }}"'''
            list_handle = self.ssh_client.run(list_cmd, io_timeout=60)
            files = [line.strip() for line in list_handle.tail(list_handle.total_lines) if line.strip()]

            return {
                'status': 'success',
                'success': True,
                'extracted_files': files,
                'destination_path': destination_path
            }

        except Exception as e:
            self.logger.error(f"Error extracting archive: {e}", exc_info=True)
            return {'status': 'error', 'message': str(e), 'extracted_files': []}

    def search_file_contents(self,
                            path: str,
                            pattern: str,
                            regex: bool = False,
                            case_sensitive: bool = True,
                            sudo: bool = False) -> List[Dict[str, Any]]:
        """Search file contents using PowerShell Select-String."""
        self.logger.info(f"Searching for '{pattern}' in files under {path}")

        ps_path = path.replace("'", "''")
        ps_pattern = pattern.replace("'", "''")

        case_flag = "" if case_sensitive else "-CaseSensitive:$false"
        match_flag = "" if regex else "-SimpleMatch"

        try:
            cmd = f'''powershell -Command "Get-ChildItem -Path '{ps_path}' -Recurse -File -ErrorAction SilentlyContinue | Select-String -Pattern '{ps_pattern}' {match_flag} {case_flag} -ErrorAction SilentlyContinue | ForEach-Object {{ \\"$($_.Path):$($_.LineNumber):$($_.Line)\\" }}"'''
            handle = self.ssh_client.run(cmd, io_timeout=300, runtime_timeout=1800)

            results = []
            # Regex to parse Windows paths: drive_letter:\path:line_num:content
            # Example: C:\Temp\test.txt:1:Hello World
            win_path_pattern = re.compile(r'^([A-Za-z]:[^:]+):(\d+):(.*)$')

            for line in handle.tail(handle.total_lines):
                if not line.strip():
                    continue

                match = win_path_pattern.match(line)
                if match:
                    file_path, line_num_str, content = match.groups()
                    try:
                        line_num = int(line_num_str)
                    except ValueError:
                        line_num = -1
                    results.append({'file': file_path, 'line': line_num, 'content': content.rstrip()})
                else:
                    # Fallback: try simple split (for UNC paths or other edge cases)
                    parts = line.split(':', 2)
                    if len(parts) >= 3:
                        file_path, line_num_str, content = parts
                        try:
                            line_num = int(line_num_str)
                        except ValueError:
                            line_num = -1
                        results.append({'file': file_path, 'line': line_num, 'content': content.rstrip()})

            self.logger.info(f"Found {len(results)} matches")
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
        """Copy directory recursively using PowerShell."""
        self.logger.info(f"Copying {source_path} to {destination_path}")

        ps_source = source_path.replace("'", "''")
        ps_dest = destination_path.replace("'", "''")

        try:
            # Remove destination if overwrite
            if overwrite:
                rm_cmd = f'''powershell -Command "if (Test-Path '{ps_dest}') {{ Remove-Item -Path '{ps_dest}' -Recurse -Force }}"'''
                self.ssh_client.run(rm_cmd, io_timeout=60, runtime_timeout=300)

            # Copy directory
            cmd = f'''powershell -Command "Copy-Item -Path '{ps_source}' -Destination '{ps_dest}' -Recurse -Force -ErrorAction Stop"'''
            self.ssh_client.run(cmd, io_timeout=300, runtime_timeout=1800)

            # Count files and size
            count_cmd = f'''powershell -Command "(Get-ChildItem -Path '{ps_dest}' -Recurse -File).Count"'''
            count_handle = self.ssh_client.run(count_cmd, io_timeout=60)
            try:
                files_copied = int(count_handle.tail(1)[0].strip())
            except (ValueError, IndexError):
                files_copied = -1

            size_cmd = f'''powershell -Command "(Get-ChildItem -Path '{ps_dest}' -Recurse -File | Measure-Object -Property Length -Sum).Sum"'''
            size_handle = self.ssh_client.run(size_cmd, io_timeout=60)
            try:
                bytes_copied = int(size_handle.tail(1)[0].strip())
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
            cmd = f'''powershell -Command "Get-ChildItem -Path '{ps_path}' -Recurse {depth_param} -Filter '{ps_pattern}' -ErrorAction SilentlyContinue | ForEach-Object {{ $t = if ($_.PSIsContainer) {{ 'd' }} else {{ 'f' }}; \\"$($_.FullName)`t$t\\" }}"'''
        else:
            # Files only
            cmd = f'''powershell -Command "Get-ChildItem -Path '{ps_path}' -Recurse {depth_param} -Filter '{ps_pattern}' -File -ErrorAction SilentlyContinue | ForEach-Object {{ \\"$($_.FullName)`tf\\" }}"'''

        return cmd

    def _cmd_dir_size(self, path: str) -> str:
        """Return PowerShell command for directory size in bytes."""
        ps_path = path.replace("'", "''")
        return f'''powershell -Command "(Get-ChildItem -Path '{ps_path}' -Recurse -File -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum"'''

    def _cmd_list_with_metadata(self, path: str, max_depth: Optional[int]) -> str:
        """Return PowerShell command to list files with metadata."""
        ps_path = path.replace("'", "''")
        depth_param = f"-Depth {max_depth}" if max_depth is not None else ""

        # Output format: path<tab>type<tab>size<tab>mtime<tab>perms<tab>user<tab>group
        # Windows doesn't have Unix perms or group, so we'll use placeholder values
        cmd = f'''powershell -Command "Get-ChildItem -Path '{ps_path}' -Recurse {depth_param} -ErrorAction SilentlyContinue | ForEach-Object {{ $t = if ($_.PSIsContainer) {{ 'd' }} else {{ 'f' }}; $s = if ($_.PSIsContainer) {{ 0 }} else {{ $_.Length }}; $m = [int][double]::Parse((Get-Date $_.LastWriteTimeUtc -UFormat %s)); $owner = try {{ $_.GetAccessControl().Owner }} catch {{ 'unknown' }}; \\"$($_.FullName)`t$t`t$s`t$m`t0`t$owner`tunknown\\" }}"'''

        return cmd

    def _cmd_file_size(self, path: str) -> str:
        """Return PowerShell command for file size."""
        ps_path = path.replace("'", "''")
        return f'''powershell -Command "(Get-Item -Path '{ps_path}' -ErrorAction SilentlyContinue).Length"'''

    def _cmd_find_symlinks(self, path: str) -> str:
        """Return PowerShell command to find symlinks (reparse points) with targets."""
        ps_path = path.replace("'", "''")
        # Windows symlinks are represented as ReparsePoints
        return f'''powershell -Command "Get-ChildItem -Path '{ps_path}' -Recurse -ErrorAction SilentlyContinue | Where-Object {{ $_.Attributes -match 'ReparsePoint' }} | ForEach-Object {{ $target = try {{ (Get-Item $_.FullName).Target }} catch {{ 'unknown' }}; \\"$($_.FullName)`t$target\\" }}"'''
