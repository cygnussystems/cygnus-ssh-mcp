import os
import tempfile
import shlex
import logging
import time
from typing import Optional, Callable
from ssh_models import SshError

class SshFileOperations_Win:
    """Handles file operations on Windows systems."""
    
    def __init__(self, ssh_client):
        """
        Args:
            ssh_client: Reference to parent SSH client
        """
        self.ssh_client = ssh_client
        self.logger = logging.getLogger(f"{__name__}.SshFileOperations_Win")

class SshFileOperations_Linux:
    """Handles file transfers, directory operations, and file editing."""
    
    def __init__(self, ssh_client):
        """
        Args:
            ssh_client: Reference to parent SSH client
        """
        self.ssh_client = ssh_client
        self.logger = logging.getLogger(f"{__name__}.SshFileOperations")
        
    def get(self, remote_path: str, local_path: str) -> None:
        """Download a file from remote to local."""
        sftp = None
        try:
            self.logger.info(f"Downloading {remote_path} to {local_path}")
            sftp = self.ssh_client._client.open_sftp()
            sftp.get(remote_path, local_path)
            self.logger.info("Download complete.")
        except Exception as e:
            self.logger.error(f"SFTP get failed for {remote_path}: {e}", exc_info=True)
            raise SshError(f"SFTP get failed: {e}") from e
        finally:
            if sftp: 
                sftp.close()

    def put(self, local_path: str, remote_path: str) -> None:
        """Upload a file from local to remote."""
        sftp = None
        try:
            self.logger.info(f"Uploading {local_path} to {remote_path}")
            sftp = self.ssh_client._client.open_sftp()
            sftp.put(local_path, remote_path)
            self.logger.info("Upload complete.")
        except Exception as e:
            self.logger.error(f"SFTP put failed for {local_path} to {remote_path}: {e}", exc_info=True)
            raise SshError(f"SFTP put failed: {e}") from e
        finally:
            if sftp: 
                sftp.close()

    def mkdir(self, path: str, sudo: bool = False, mode: int = 0o755) -> None:
        """Create a remote directory with optional sudo."""
        self.logger.info(f"Creating directory {path} (sudo={sudo}, mode={mode:o})")
        if sudo:
            self.ssh_client.run(f"mkdir -p -m {mode:o} {shlex.quote(path)}", sudo=True)
        else:
            with self.ssh_client._client.open_sftp() as sftp:
                sftp.mkdir(path, mode)

    def rmdir(self, path: str, sudo: bool = False, recursive: bool = False) -> None:
        """Remove a remote directory with optional sudo."""
        self.logger.info(f"Removing directory {path} (sudo={sudo}, recursive={recursive})")
        if recursive:
            cmd = f"rm -rf {shlex.quote(path)}"
        else:
            cmd = f"rmdir {shlex.quote(path)}"
        self.ssh_client.run(cmd, sudo=sudo)

    def listdir(self, path: str) -> list:
        """List contents of a remote directory."""
        self.logger.info(f"Listing directory {path}")
        with self.ssh_client._client.open_sftp() as sftp:
            return sftp.listdir(path)

    def stat(self, path: str) -> dict:
        """Get file/directory status info."""
        self.logger.debug(f"Getting stats for {path}")
        with self.ssh_client._client.open_sftp() as sftp:
            return sftp.stat(path)

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
        self.logger.info(f"Searching for pattern in {remote_file} (regex={regex}, sudo={sudo})")
        
        # Escape pattern for grep if not using regex
        grep_pattern = pattern if regex else pattern.replace("'", "'\\''")
        grep_option = "-E" if regex else "-F"
        
        # Build grep command with line numbers
        cmd = f"grep {grep_option} -n '{grep_pattern}' {shlex.quote(remote_file)}"
        
        try:
            # Execute grep command
            handle = self.ssh_client.run(cmd, sudo=sudo)
            output = handle.get_full_output()
            
            # Parse grep output (format: "line_number:content")
            matches = []
            for line in output.splitlines():
                if ":" in line:
                    line_num_str, content = line.split(":", 1)
                    try:
                        line_num = int(line_num_str)
                        matches.append({"line_number": line_num, "content": content})
                    except ValueError:
                        self.logger.warning(f"Could not parse line number from grep output: {line}")
            
            return {
                "total_matches": len(matches),
                "matches": matches
            }
        except Exception as e:
            if "No such file or directory" in str(e):
                return {"total_matches": 0, "matches": [], "error": "File not found"}
            elif "Permission denied" in str(e) and not sudo:
                return {"total_matches": 0, "matches": [], "error": "Permission denied, try with sudo=True"}
            else:
                self.logger.error(f"Error searching file {remote_file}: {e}")
                return {"total_matches": 0, "matches": [], "error": str(e)}

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
        self.logger.info(f"Getting context around line in {remote_file} (context={context}, sudo={sudo})")
        
        # First find the exact line number
        find_result = self.find_lines_with_pattern(remote_file, match_line, regex=False, sudo=sudo)
        
        if find_result["total_matches"] == 0:
            return {"match_found": False, "error": "Match line not found in file"}
        
        if find_result["total_matches"] > 1:
            return {"match_found": False, "error": "Multiple matches found for the line", "matches": find_result["matches"]}
        
        # Get the line number of the match
        match_line_number = find_result["matches"][0]["line_number"]
        
        # Calculate the range of lines to extract
        start_line = max(1, match_line_number - context)
        end_line = match_line_number + context
        
        # Use sed to extract the range of lines
        cmd = f"sed -n '{start_line},{end_line}p' {shlex.quote(remote_file)}"
        
        try:
            handle = self.ssh_client.run(cmd, sudo=sudo)
            output = handle.get_full_output()
            
            # Build the context block with line numbers
            context_block = []
            current_line = start_line
            for line in output.splitlines():
                context_block.append({"line_number": current_line, "content": line})
                current_line += 1
            
            return {
                "match_found": True,
                "match_line_number": match_line_number,
                "context_block": context_block
            }
        except Exception as e:
            self.logger.error(f"Error getting context around line in {remote_file}: {e}")
            return {"match_found": False, "error": str(e)}

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
        self.logger.info(f"Replacing line by content in {remote_file} (sudo={sudo}, force={force})")
        
        # Ensure new_lines is a list
        if isinstance(new_lines, str):
            new_lines = [new_lines]
        
        # First check if the file exists
        try:
            with self.ssh_client._client.open_sftp() as sftp:
                sftp.stat(remote_file)
        except FileNotFoundError:
            return {"success": False, "error": f"File not found: {remote_file}"}
        except Exception as e:
            if not sudo or not force:
                return {"success": False, "error": f"Error accessing file: {str(e)}"}
        
        # Check for duplicate lines before attempting modification
        try:
            with self.ssh_client._client.open_sftp() as sftp:
                with sftp.file(remote_file, 'r') as f:
                    content = f.read().decode('utf-8', errors='replace')
                    lines = content.splitlines()
                    match_count = sum(1 for line in lines if line.rstrip('\r\n') == match_line)
                    
                    if match_count == 0:
                        return {"success": False, "error": f"Match line not found in file: {match_line}"}
                    if match_count > 1:
                        return {"success": False, "error": f"Match line is not unique in file (found {match_count} occurrences): {match_line}"}
        except Exception as e:
            if not sudo:
                return {"success": False, "error": f"Error checking for duplicate lines: {str(e)}"}
        
        # Define the modification function
        def modify_func(text):
            lines = text.splitlines(keepends=True)
            match_count = sum(1 for line in lines if line.rstrip('\r\n') == match_line)
            
            # Check for uniqueness - this should never be reached due to the pre-check above
            if match_count == 0:
                raise ValueError(f"Match line not found in file: {match_line}")
            if match_count > 1:
                raise ValueError(f"Match line is not unique in file (found {match_count} occurrences): {match_line}")
            
            modified = False
            result = []
            
            for line in lines:
                stripped_line = line.rstrip('\r\n')
                if stripped_line == match_line and not modified:
                    # Found the match, replace with new lines
                    # Always use Unix line endings (\n) for consistency
                    for new_line in new_lines:
                        result.append(new_line + '\n')
                    modified = True
                else:
                    result.append(line)
            
            return "".join(result) if modified else text
        
        # Use existing helpers for file modification
        if sudo:
            remote_temp_path = f"/tmp/replace_line_{os.path.basename(remote_file)}_{int(time.time())}"
            try:
                result = self._replace_content_sudo(remote_file, remote_temp_path, modify_func, force=force)
                if isinstance(result, dict) and not result.get("success", False):
                    return result
                return {"success": True, "lines_written": len(new_lines)}
            except Exception as e:
                self.logger.error(f"Failed to replace line in {remote_file}: {e}")
                return {"success": False, "error": str(e)}
        else:
            if force:
                self.logger.warning("force=True has no effect when sudo=False")
            try:
                result = self._replace_content_sftp(remote_file, modify_func)
                if result.get("success", False):
                    result["lines_written"] = len(new_lines)
                return result
            except Exception as e:
                self.logger.error(f"Failed to replace line in {remote_file}: {e}")
                return {"success": False, "error": str(e)}

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
        self.logger.info(f"Inserting lines after match in {remote_file} (sudo={sudo}, force={force})")
        
        # Ensure lines_to_insert is a list
        if isinstance(lines_to_insert, str):
            lines_to_insert = [lines_to_insert]
        
        # First check if the file exists
        try:
            with self.ssh_client._client.open_sftp() as sftp:
                sftp.stat(remote_file)
        except FileNotFoundError:
            return {"success": False, "error": f"File not found: {remote_file}"}
        except Exception as e:
            if not sudo or not force:
                return {"success": False, "error": f"Error accessing file: {str(e)}"}
        
        # Check for duplicate lines before attempting modification
        try:
            with self.ssh_client._client.open_sftp() as sftp:
                with sftp.file(remote_file, 'r') as f:
                    content = f.read().decode('utf-8', errors='replace')
                    lines = content.splitlines()
                    match_count = sum(1 for line in lines if line.rstrip('\r\n') == match_line)
                    
                    if match_count == 0:
                        return {"success": False, "error": f"Match line not found in file: {match_line}"}
                    if match_count > 1:
                        return {"success": False, "error": f"Match line is not unique in file (found {match_count} occurrences): {match_line}"}
        except Exception as e:
            if not sudo:
                return {"success": False, "error": f"Error checking for duplicate lines: {str(e)}"}
        
        # Define the modification function
        def modify_func(text):
            lines = text.splitlines(keepends=True)
            match_count = sum(1 for line in lines if line.rstrip('\r\n') == match_line)
            
            # Check for uniqueness - this should never be reached due to the pre-check above
            if match_count == 0:
                raise ValueError(f"Match line not found in file: {match_line}")
            if match_count > 1:
                raise ValueError(f"Match line is not unique in file (found {match_count} occurrences): {match_line}")
            
            modified = False
            result = []
            
            for line in lines:
                result.append(line)  # Always keep the original line
                
                # Check if this is the match line
                stripped_line = line.rstrip('\r\n')
                if stripped_line == match_line and not modified:
                    # Insert new lines after the match
                    # Always use Unix line endings (\n) for consistency
                    for new_line in lines_to_insert:
                        result.append(new_line + '\n')
                    modified = True
            
            return "".join(result) if modified else text
        
        # Use existing helpers for file modification
        if sudo:
            remote_temp_path = f"/tmp/insert_after_{os.path.basename(remote_file)}_{int(time.time())}"
            try:
                result = self._replace_content_sudo(remote_file, remote_temp_path, modify_func, force=force)
                if isinstance(result, dict) and not result.get("success", False):
                    return result
                return {"success": True, "lines_inserted": len(lines_to_insert)}
            except Exception as e:
                self.logger.error(f"Failed to insert lines in {remote_file}: {e}")
                return {"success": False, "error": str(e)}
        else:
            if force:
                self.logger.warning("force=True has no effect when sudo=False")
            try:
                result = self._replace_content_sftp(remote_file, modify_func)
                if result.get("success", False):
                    result["lines_inserted"] = len(lines_to_insert)
                return result
            except Exception as e:
                self.logger.error(f"Failed to insert lines in {remote_file}: {e}")
                return {"success": False, "error": str(e)}

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
        self.logger.info(f"Deleting line by content in {remote_file} (sudo={sudo}, force={force})")
        
        # First check if the file exists
        try:
            with self.ssh_client._client.open_sftp() as sftp:
                sftp.stat(remote_file)
        except FileNotFoundError:
            return {"success": False, "error": f"File not found: {remote_file}"}
        except Exception as e:
            if not sudo or not force:
                return {"success": False, "error": f"Error accessing file: {str(e)}"}
        
        # Check for duplicate lines before attempting modification
        try:
            with self.ssh_client._client.open_sftp() as sftp:
                with sftp.file(remote_file, 'r') as f:
                    content = f.read().decode('utf-8', errors='replace')
                    lines = content.splitlines()
                    match_count = sum(1 for line in lines if line.rstrip('\r\n') == match_line)
                    
                    if match_count == 0:
                        return {"success": False, "error": f"Match line not found in file: {match_line}"}
                    if match_count > 1:
                        return {"success": False, "error": f"Match line is not unique in file (found {match_count} occurrences): {match_line}"}
        except Exception as e:
            if not sudo:
                return {"success": False, "error": f"Error checking for duplicate lines: {str(e)}"}
        
        # Define the modification function
        def modify_func(text):
            # Normalize line endings to Unix style
            text = text.replace('\r\n', '\n')
            lines = text.splitlines(keepends=True)
            match_count = sum(1 for line in lines if line.rstrip('\n') == match_line)
            
            # Check for uniqueness - this should never be reached due to the pre-check above
            if match_count == 0:
                raise ValueError(f"Match line not found in file: {match_line}")
            if match_count > 1:
                raise ValueError(f"Match line is not unique in file (found {match_count} occurrences): {match_line}")
            
            modified = False
            result = []
            
            for line in lines:
                stripped_line = line.rstrip('\n')
                if stripped_line == match_line and not modified:
                    # Skip this line (delete it)
                    modified = True
                else:
                    result.append(line)
            
            return "".join(result) if modified else text
        
        # Use existing helpers for file modification
        if sudo:
            remote_temp_path = f"/tmp/delete_line_{os.path.basename(remote_file)}_{int(time.time())}"
            try:
                result = self._replace_content_sudo(remote_file, remote_temp_path, modify_func, force=force)
                if isinstance(result, dict) and not result.get("success", False):
                    return result
                return {"success": True}
            except Exception as e:
                self.logger.error(f"Failed to delete line in {remote_file}: {e}")
                return {"success": False, "error": str(e)}
        else:
            if force:
                self.logger.warning("force=True has no effect when sudo=False")
            try:
                return self._replace_content_sftp(remote_file, modify_func)
            except Exception as e:
                self.logger.error(f"Failed to delete line in {remote_file}: {e}")
                return {"success": False, "error": str(e)}

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
        # Generate timestamped destination if requested
        actual_destination = destination_path
        if append_timestamp:
            timestamp = time.strftime("%Y%m%dT%H%M%S")
            base, ext = os.path.splitext(destination_path)
            actual_destination = f"{base}.{timestamp}{ext}"
        
        self.logger.info(f"Copying file from {source_path} to {actual_destination} (sudo={sudo})")
        
        if sudo:
            # Use cp command with sudo
            cmd = f"cp {shlex.quote(source_path)} {shlex.quote(actual_destination)}"
            try:
                self.ssh_client.run(cmd, sudo=True)
                return {
                    "success": True,
                    "copied_to": actual_destination
                }
            except Exception as e:
                self.logger.error(f"Failed to copy file with sudo: {e}")
                return {"success": False, "error": str(e)}
        else:
            # Use SFTP for non-sudo copy
            try:
                # First get the source file
                local_temp_fd, local_temp_path = tempfile.mkstemp()
                os.close(local_temp_fd)
                
                try:
                    self.get(source_path, local_temp_path)
                    self.put(local_temp_path, actual_destination)
                    return {
                        "success": True,
                        "copied_to": actual_destination
                    }
                finally:
                    # Clean up temp file
                    if os.path.exists(local_temp_path):
                        os.unlink(local_temp_path)
            except Exception as e:
                self.logger.error(f"Failed to copy file via SFTP: {e}")
                return {"success": False, "error": str(e)}

    def _replace_content_sftp(self, remote_file: str, modify_func: Callable[[str], str]) -> dict:
        """
        Internal helper for SFTP-based file modification.
        
        Args:
            remote_file: Path to remote file
            modify_func: Function that takes file content and returns modified content
            
        Returns:
            Dictionary with success status and error message if applicable
        """
        local_temp_fd, local_temp_path = tempfile.mkstemp(text=True)
        os.close(local_temp_fd) # Close handle, we just need the name
        self.logger.debug(f"Created local temp file: {local_temp_path}")

        try:
            # 1. Download
            self.get(remote_file, local_temp_path)

            # 2. Read, Modify, Write locally
            with open(local_temp_path, 'r', encoding='utf-8', errors='replace') as f:
                # Normalize line endings to Unix style (LF)
                original_text = f.read().replace('\r\n', '\n')
                
            try:
                modified_text = modify_func(original_text)
            except ValueError as e:
                self.logger.error(f"Modification failed: {str(e)}")
                return {"success": False, "error": str(e)}

            # Only upload if content changed
            if modified_text != original_text:
                self.logger.info(f"Content modified for {remote_file}. Uploading changes.")
                with open(local_temp_path, 'w', encoding='utf-8', newline='\n') as f:
                    f.write(modified_text)
                # 3. Upload back
                self.put(local_temp_path, remote_file)
                return {"success": True}
            else:
                self.logger.info(f"Content for {remote_file} not modified, skipping upload.")
                return {"success": True, "message": "No changes needed"}

        except Exception as e:
            self.logger.error(f"File operation failed: {str(e)}")
            return {"success": False, "error": str(e)}
        finally:
            # 4. Cleanup local temp file
            if os.path.exists(local_temp_path):
                self.logger.debug(f"Cleaning up local temp file: {local_temp_path}")
                os.unlink(local_temp_path)

    def _replace_content_sudo(self, remote_file: str, remote_temp_path: str, 
                            modify_func: Callable[[str], str], force: bool = False) -> dict:
        """
        Internal helper for sudo-based file modification.
        
        Args:
            remote_file: Path to remote file
            remote_temp_path: Temporary path on remote system
            modify_func: Function that takes file content and returns modified content
            force: Whether to proceed if original file cannot be read
            
        Returns:
            Dictionary with success status and error message if applicable
        """
        local_temp_fd, local_temp_path = tempfile.mkstemp(text=True)
        os.close(local_temp_fd)
        self.logger.debug(f"Created local temp file: {local_temp_path}")
        original_text = None # Initialize

        try:
            # 1. Download original file (best effort, might fail if no read permission)
            try:
                self.get(remote_file, local_temp_path)
                with open(local_temp_path, 'r', encoding='utf-8', errors='replace') as f:
                    # Normalize line endings to Unix style (LF)
                    original_text = f.read().replace('\r\n', '\n')
                self.logger.debug(f"Successfully downloaded original file {remote_file}")
            except Exception as e:
                self.logger.warning(f"Could not download original {remote_file}: {e}. Checking force flag.")
                if not force:
                    raise SshError(f"Cannot read original file {remote_file} and force=False. Aborting replacement.") from e
                else:
                    self.logger.warning("force=True specified. Proceeding with modification assuming empty or irrelevant original content.")
                    original_text = "" # Proceed with empty content if forced

            # 2. Modify content
            try:
                modified_text = modify_func(original_text)
            except ValueError as e:
                self.logger.error(f"Modification failed: {str(e)}")
                return {"success": False, "error": str(e)}

            # 3. Check if content actually changed (important!)
            if modified_text == original_text:
                self.logger.info(f"Content for {remote_file} not modified, skipping sudo replacement.")
                return {"success": True, "message": "No changes needed"} # Exit early, no need to upload or move

            # 4. Write modified content to local temp
            self.logger.info(f"Content modified for {remote_file}. Proceeding with sudo replacement.")
            with open(local_temp_path, 'w', encoding='utf-8', newline='\n') as f:
                f.write(modified_text)

            # 5. Upload modified content to REMOTE temp path
            self.put(local_temp_path, remote_temp_path)

            # 6. Use `sudo mv` to replace the original file atomically
            #    Also copy permissions and ownership from original if possible
            perms = owner = group = None
            if original_text is not None: # Only try stat if we could potentially read the original
                stat_cmd = f"stat -c '%a %u %g' {shlex.quote(remote_file)}"
                try:
                    # Use run() for stat, ensure sudo is False for stat command itself
                    stat_handle = self.ssh_client.run(stat_cmd, io_timeout=10, sudo=False)
                    stat_output = stat_handle.tail(1)[0].strip()
                    parts = stat_output.split()
                    if len(parts) == 3:
                        perms, owner, group = parts
                        self.logger.debug(f"Got permissions for {remote_file}: {perms} {owner}:{group}")
                    else:
                        self.logger.warning(f"Unexpected output from stat command: '{stat_output}'. Cannot restore permissions.")
                except Exception as stat_err:
                    self.logger.warning(f"Could not get permissions/owner for {remote_file}: {stat_err}. Using defaults.")

            # Build the move and permission commands
            mv_cmd = f"mv {shlex.quote(remote_temp_path)} {shlex.quote(remote_file)}"
            chown_cmd = f"chown {owner}:{group} {shlex.quote(remote_file)}" if owner and group else None
            chmod_cmd = f"chmod {perms} {shlex.quote(remote_file)}" if perms else None

            # Execute commands with sudo (original sudo flag for the replace operation)
            self.logger.info(f"Executing sudo mv: {mv_cmd}")
            self.ssh_client.run(mv_cmd, sudo=True) # run() handles potential sudo errors
            if chown_cmd:
                try:
                    self.logger.info(f"Executing sudo chown: {chown_cmd}")
                    self.ssh_client.run(chown_cmd, sudo=True)
                except Exception as chown_err:
                    self.logger.warning(f"Failed to sudo chown {remote_file}: {chown_err}")
            if chmod_cmd:
                try:
                    self.logger.info(f"Executing sudo chmod: {chmod_cmd}")
                    self.ssh_client.run(chmod_cmd, sudo=True)
                except Exception as chmod_err:
                    self.logger.warning(f"Failed to sudo chmod {remote_file}: {chmod_err}")

            self.logger.info(f"Successfully replaced {remote_file} using sudo.")
            return {"success": True}

        except Exception as e:
            self.logger.error(f"Sudo file operation failed: {str(e)}")
            return {"success": False, "error": str(e)}
        finally:
            # 7. Cleanup local and remote temp files
            if os.path.exists(local_temp_path):
                self.logger.debug(f"Cleaning up local temp file: {local_temp_path}")
                os.unlink(local_temp_path)
            # Try removing remote temp file, ignore errors, use run()
            try:
                self.logger.debug(f"Cleaning up remote temp file: {remote_temp_path}")
                # Use run with short timeout, ignore BusyError. Don't use sudo for /tmp cleanup.
                self.ssh_client.run(f"rm -f {shlex.quote(remote_temp_path)}", io_timeout=10, runtime_timeout=15, sudo=False)
            except Exception as cleanup_err:
                self.logger.warning(f"Failed to cleanup remote temp file {remote_temp_path}: {cleanup_err}")
