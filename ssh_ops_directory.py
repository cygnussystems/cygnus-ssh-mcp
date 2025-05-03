import os
import shlex
import logging
import json
import time
from typing import Optional, List, Dict, Any, Union, Tuple

class SshDirectoryOperations:
    """Handles advanced directory operations like searching, copying, and archiving."""
    
    def __init__(self, ssh_client):
        """
        Args:
            ssh_client: Reference to parent SSH client
        """
        self.ssh_client = ssh_client
        self.logger = logging.getLogger(f"{__name__}.SshDirectoryOperations")
    
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
        
        # Construct find command with appropriate options
        cmd_parts = ["find", shlex.quote(start_path)]
        
        # Add depth constraint if specified
        if max_depth is not None:
            cmd_parts.append(f"-maxdepth {max_depth}")
        
        # Add name pattern
        cmd_parts.append(f"-name {shlex.quote(name_pattern)}")
        
        # Add type filter if not including directories
        if not include_dirs:
            cmd_parts.append("-type f")
        
        # Add output formatting to include file type
        cmd_parts.append("-printf '%p\\t%y\\n'")  # path, type separated by tab
        
        # Execute the command
        cmd = " ".join(cmd_parts)
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
        
        # Use du with block size of 1 byte for accurate counting
        cmd = f"du -sb {shlex.quote(path)} | cut -f1"
        
        try:
            handle = self.ssh_client.run(cmd, io_timeout=120, runtime_timeout=300)
            
            if handle.exit_code != 0:
                self.logger.error(f"Failed to calculate directory size: {handle.tail(5)}")
                raise RuntimeError(f"Failed to calculate directory size, exit code: {handle.exit_code}")
            
            # Parse the output (should be a single number)
            size_str = handle.tail(1)[0].strip()
            size_bytes = int(size_str)
            
            self.logger.info(f"Directory {path} size: {size_bytes} bytes")
            return size_bytes
            
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
        
        # Check if destination exists
        check_cmd = f"[ -e {shlex.quote(destination)} ] && echo 'exists' || echo 'not_exists'"
        
        try:
            check_handle = self.ssh_client.run(check_cmd, io_timeout=30, sudo=sudo)
            destination_exists = 'exists' in check_handle.tail(1)[0]
            
            if destination_exists and not overwrite:
                self.logger.warning(f"Destination exists and overwrite=False: {destination}")
                return {
                    'status': 'error',
                    'message': f"Destination exists and overwrite not allowed: {destination}"
                }
            
            # Perform the move
            move_cmd = f"mv {'-f' if overwrite else ''} {shlex.quote(source)} {shlex.quote(destination)}"
            move_handle = self.ssh_client.run(move_cmd, io_timeout=120, runtime_timeout=300, sudo=sudo)
            
            if move_handle.exit_code != 0:
                self.logger.error(f"Failed to move/rename: {move_handle.tail(5)}")
                return {
                    'status': 'error',
                    'message': f"Failed to move/rename, exit code: {move_handle.exit_code}"
                }
            
            self.logger.info(f"Successfully moved {source} to {destination}")
            return {
                'status': 'success',
                'message': f"Successfully moved {source} to {destination}"
            }
            
        except Exception as e:
            self.logger.error(f"Error moving/renaming: {e}", exc_info=True)
            return {
                'status': 'error',
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
        
        # Construct find command with appropriate options
        cmd_parts = ["find", shlex.quote(path)]
        
        # Add depth constraint if specified
        if max_depth is not None:
            cmd_parts.append(f"-maxdepth {max_depth}")
        
        # Add output formatting to include metadata
        # Format: path, type, size, modified time, permissions, user, group
        cmd_parts.append("-printf '%p\\t%y\\t%s\\t%T@\\t%m\\t%u\\t%g\\n'")
        
        # Execute the command
        cmd = " ".join(cmd_parts)
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
        Create a compressed archive (tar.gz or zip) from a directory.
        
        Args:
            source_path: Directory to archive
            archive_path: Where to write the archive
            format: "tar.gz" or "zip"
            sudo: Whether to use sudo for the operation
            
        Returns:
            Dict with status and archive path
        """
        self.logger.info(f"Creating {format} archive from {source_path} to {archive_path} (sudo={sudo})")
        
        # Validate format
        if format not in ["tar.gz", "zip"]:
            error_msg = f"Unsupported archive format: {format}. Use 'tar.gz' or 'zip'."
            self.logger.error(error_msg)
            return {
                'status': 'error',
                'message': error_msg
            }
        
        try:
            # Create the archive based on format
            if format == "tar.gz":
                # Get directory name without trailing slash
                source_dir = source_path.rstrip('/')
                parent_dir = os.path.dirname(source_dir)
                base_name = os.path.basename(source_dir)
                
                # Create tar.gz archive
                cmd = f"tar -czf {shlex.quote(archive_path)} -C {shlex.quote(parent_dir)} {shlex.quote(base_name)}"
                handle = self.ssh_client.run(cmd, io_timeout=300, runtime_timeout=1800, sudo=sudo)
                
            else:  # zip
                # Create zip archive
                cmd = f"cd {shlex.quote(os.path.dirname(source_path.rstrip('/')))} && zip -r {shlex.quote(archive_path)} {shlex.quote(os.path.basename(source_path.rstrip('/')))}"
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
            size_cmd = f"stat -c %s {shlex.quote(archive_path)}"
            size_handle = self.ssh_client.run(size_cmd, io_timeout=30, sudo=sudo)
            
            try:
                archive_size = int(size_handle.tail(1)[0].strip())
            except (ValueError, IndexError):
                archive_size = -1
            
            self.logger.info(f"Successfully created archive at {archive_path} ({archive_size} bytes)")
            return {
                'status': 'success',
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
        Extract a zip or tar.gz archive to a directory.
        
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
        elif archive_path.endswith('.zip'):
            archive_type = 'zip'
        else:
            error_msg = f"Unsupported archive format for {archive_path}. Supported formats: .tar.gz, .tgz, .zip"
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
            else:  # zip
                list_cmd = f"unzip -l {shlex.quote(archive_path)} | tail -n+4 | head -n-2 | awk '{{print $4}}'"
            
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
                extract_cmd = f"tar -xzf {shlex.quote(archive_path)} -C {shlex.quote(destination_path)}"
                if not overwrite:
                    extract_cmd += " --keep-old-files"
            else:  # zip
                extract_cmd = f"unzip {'-o' if overwrite else ''} {shlex.quote(archive_path)} -d {shlex.quote(destination_path)}"
            
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
        
        if dest_exists and not overwrite:
            # Create a new destination path to avoid overwriting
            destination_path = f"{destination_path}_{int(time.time())}"
            self.logger.info(f"Destination exists and overwrite=False, using new path: {destination_path}")
        elif dest_exists and overwrite:
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
                find_links_cmd = f"find {shlex.quote(source_path)} -type l -printf '%p\\t%l\\n'"
                links_handle = self.ssh_client.run(find_links_cmd, io_timeout=60, sudo=sudo)
                
                # Process each symlink
                for line in links_handle.tail(links_handle.total_lines):
                    if not line.strip():
                        continue
                    
                    parts = line.strip().split('\t')
                    if len(parts) == 2:
                        link_path, target = parts
                        # Create relative path in destination
                        rel_path = os.path.relpath(link_path, source_path)
                        dest_link = os.path.join(destination_path, rel_path)
                        
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
            size_cmd = f"du -sb {shlex.quote(destination_path)} | cut -f1"
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
