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
        Replace a unique line (by exact content, ignoring leading/trailing whitespace) with new lines.
        """
        self.logger.info(f"Replacing line by content in {remote_file} (sudo={sudo}, force={force})")
        
        normalized_match_line = match_line.strip()

        if isinstance(new_lines, str):
            new_lines = [new_lines]
        
        try:
            with self.ssh_client._client.open_sftp() as sftp:
                sftp.stat(remote_file)
        except FileNotFoundError:
            return {"success": False, "error": f"File not found: {remote_file}"}
        except Exception as e:
            if not sudo or not force:
                return {"success": False, "error": f"Error accessing file: {str(e)}"}
        
        content = None
        try:
            with self.ssh_client._client.open_sftp() as sftp:
                with sftp.file(remote_file, 'r') as f:
                    content = f.read().decode('utf-8', errors='replace')
            
            file_lines = content.splitlines()

            match_count = 0
            matched_indices = []
            for idx, line_content in enumerate(file_lines):
                stripped_line_content = line_content.strip()
                if stripped_line_content == normalized_match_line:
                    match_count += 1
                    matched_indices.append(idx)
            
            if match_count == 0:
                self.logger.error(f"Match line not found in file (normalized): '{normalized_match_line}' (original: '{match_line}')")
                return {"success": False, "error": f"Match line not found in file: {match_line}"}
            if match_count > 1:
                self.logger.error(f"Match line is not unique in file (found {match_count} occurrences at indices {matched_indices} for normalized: '{normalized_match_line}') (original: '{match_line}')")
                return {"success": False, "error": f"Match line is not unique in file (found {match_count} occurrences): {match_line}"}
        except Exception as e:
            if not sudo:
                self.logger.error(f"Error checking for duplicate lines: {str(e)}")
                return {"success": False, "error": f"Error checking for duplicate lines: {str(e)}"}
            elif not force:
                return {"success": False, "error": f"Cannot check for duplicates: {str(e)}"}
        
        def modify_func(text):
            # Ensure text uses LF line endings before processing
            text_normalized = text.replace('\r\n', '\n')
            lines = text_normalized.splitlines(keepends=True)
            modified = False
            result = []
            
            for line in lines:
                if line.strip() == normalized_match_line and not modified:
                    for new_line_content in new_lines:
                        result.append(new_line_content + '\n') # Ensure new lines use LF
                    modified = True
                else:
                    result.append(line) # Original line (with LF if normalized)
            
            return "".join(result) if modified else text_normalized # Return normalized text if no change
        
        if sudo:
            remote_temp_path = f"/tmp/replace_line_{os.path.basename(remote_file)}_{int(time.time())}"
            try:
                op_result = self._replace_content_sudo(remote_file, remote_temp_path, modify_func, force=force)
                if isinstance(op_result, dict) and not op_result.get("success", False):
                    return op_result
                return {"success": True, "lines_written": len(new_lines)}
            except Exception as e:
                self.logger.error(f"Failed to replace line in {remote_file}: {e}")
                return {"success": False, "error": str(e)}
        else:
            if force: self.logger.warning("force=True has no effect when sudo=False")
            try:
                if content is not None: 
                    modified_content = modify_func(content) 
                    if modified_content != content.replace('\r\n', '\n'): # Compare with normalized original
                        local_temp_fd, local_temp_path = tempfile.mkstemp(text=True)
                        try:
                            with os.fdopen(local_temp_fd, 'w', encoding='utf-8', newline='\n') as f: # Write with LF
                                f.write(modified_content)
                            self.logger.info(f"Content modified for {remote_file}. Uploading changes.")
                            self.put(local_temp_path, remote_file)
                            return {"success": True, "lines_written": len(new_lines)}
                        finally:
                            if os.path.exists(local_temp_path): os.unlink(local_temp_path)
                    else:
                        return {"success": True, "message": "No changes needed"}
                else: 
                    op_result = self._replace_content_sftp(remote_file, modify_func)
                    if op_result.get("success", False): op_result["lines_written"] = len(new_lines)
                    return op_result
            except Exception as e:
                self.logger.error(f"Failed to replace line in {remote_file}: {e}")
                return {"success": False, "error": str(e)}

    def insert_lines_after_match(self, remote_file: str, match_line: str, lines_to_insert: list,
                                sudo: bool = False, force: bool = False) -> dict:
        """
        Insert lines after a unique line match (ignoring leading/trailing whitespace).
        """
        self.logger.info(f"Inserting lines after match in {remote_file} (sudo={sudo}, force={force})")

        normalized_match_line = match_line.strip()

        if isinstance(lines_to_insert, str):
            lines_to_insert = [lines_to_insert]
        
        try:
            with self.ssh_client._client.open_sftp() as sftp:
                sftp.stat(remote_file)
        except FileNotFoundError:
            return {"success": False, "error": f"File not found: {remote_file}"}
        except Exception as e:
            if not sudo or not force:
                return {"success": False, "error": f"Error accessing file: {str(e)}"}

        content = None
        try:
            with self.ssh_client._client.open_sftp() as sftp:
                with sftp.file(remote_file, 'r') as f:
                    content = f.read().decode('utf-8', errors='replace')
            
            file_lines = content.splitlines()
            
            match_count = 0
            matched_indices = []
            for idx, line_content in enumerate(file_lines):
                stripped_line_content = line_content.strip()
                if stripped_line_content == normalized_match_line:
                    match_count += 1
                    matched_indices.append(idx)

            if match_count == 0:
                self.logger.error(f"Match line not found in file (normalized): '{normalized_match_line}' (original: '{match_line}')")
                return {"success": False, "error": f"Match line not found in file: {match_line}"}
            if match_count > 1:
                self.logger.error(f"Match line is not unique in file (found {match_count} occurrences at indices {matched_indices} for normalized: '{normalized_match_line}') (original: '{match_line}')")
                return {"success": False, "error": f"Match line is not unique in file (found {match_count} occurrences): {match_line}"}
        except Exception as e:
            if not sudo:
                self.logger.error(f"Error checking for duplicate lines: {str(e)}")
                return {"success": False, "error": f"Error checking for duplicate lines: {str(e)}"}
            elif not force:
                return {"success": False, "error": f"Cannot check for duplicates: {str(e)}"}

        def modify_func(text):
            text_normalized = text.replace('\r\n', '\n')
            lines = text_normalized.splitlines(keepends=True)
            modified = False
            result = []
            
            for line in lines:
                result.append(line)
                if line.strip() == normalized_match_line and not modified:
                    for new_line_content in lines_to_insert:
                        result.append(new_line_content + '\n') # Ensure new lines use LF
                    modified = True
            
            return "".join(result) if modified else text_normalized

        if sudo:
            remote_temp_path = f"/tmp/insert_after_{os.path.basename(remote_file)}_{int(time.time())}"
            try:
                op_result = self._replace_content_sudo(remote_file, remote_temp_path, modify_func, force=force)
                if isinstance(op_result, dict) and not op_result.get("success", False):
                    return op_result
                return {"success": True, "lines_inserted": len(lines_to_insert)}
            except Exception as e:
                self.logger.error(f"Failed to insert lines in {remote_file}: {e}")
                return {"success": False, "error": str(e)}
        else:
            if force: self.logger.warning("force=True has no effect when sudo=False")
            try:
                if content is not None: 
                    modified_content = modify_func(content) 
                    if modified_content != content.replace('\r\n', '\n'): 
                        local_temp_fd, local_temp_path = tempfile.mkstemp(text=True)
                        try:
                            with os.fdopen(local_temp_fd, 'w', encoding='utf-8', newline='\n') as f: # Write with LF
                                f.write(modified_content)
                            self.logger.info(f"Content modified for {remote_file}. Uploading changes.")
                            self.put(local_temp_path, remote_file)
                            return {"success": True, "lines_inserted": len(lines_to_insert)}
                        finally:
                            if os.path.exists(local_temp_path): os.unlink(local_temp_path)
                    else:
                        return {"success": True, "message": "No changes needed"}
                else: 
                    op_result = self._replace_content_sftp(remote_file, modify_func)
                    if op_result.get("success", False): op_result["lines_inserted"] = len(lines_to_insert)
                    return op_result
            except Exception as e:
                self.logger.error(f"Failed to insert lines in {remote_file}: {e}")
                return {"success": False, "error": str(e)}

    def delete_line_by_content(self, remote_file: str, match_line: str,
                              sudo: bool = False, force: bool = False) -> dict:
        """
        Delete a line matching a unique content string (ignoring leading/trailing whitespace).
        """
        self.logger.info(f"Deleting line by content in {remote_file} (sudo={sudo}, force={force})")

        normalized_match_line = match_line.strip()
        
        try:
            with self.ssh_client._client.open_sftp() as sftp:
                sftp.stat(remote_file)
        except FileNotFoundError:
            return {"success": False, "error": f"File not found: {remote_file}"}
        except Exception as e:
            if not sudo or not force:
                return {"success": False, "error": f"Error accessing file: {str(e)}"}

        content = None
        try:
            with self.ssh_client._client.open_sftp() as sftp:
                with sftp.file(remote_file, 'r') as f:
                    content = f.read().decode('utf-8', errors='replace')

            file_lines = content.splitlines()
            
            match_count = 0
            matched_indices = []
            for idx, line_content in enumerate(file_lines):
                stripped_line_content = line_content.strip()
                if stripped_line_content == normalized_match_line:
                    match_count += 1
                    matched_indices.append(idx)
            
            if match_count == 0:
                self.logger.error(f"Match line not found in file (normalized): '{normalized_match_line}' (original: '{match_line}')")
                return {"success": False, "error": f"Match line not found in file: {match_line}"}
            if match_count > 1:
                self.logger.error(f"Match line is not unique in file (found {match_count} occurrences at indices {matched_indices} for normalized: '{normalized_match_line}') (original: '{match_line}')")
                return {"success": False, "error": f"Match line is not unique in file (found {match_count} occurrences): {match_line}"}
        except Exception as e:
            if not sudo:
                self.logger.error(f"Error checking for duplicate lines: {str(e)}")
                return {"success": False, "error": f"Error checking for duplicate lines: {str(e)}"}
            elif not force:
                return {"success": False, "error": f"Cannot check for duplicates: {str(e)}"}
        
        def modify_func(text):
            text_normalized = text.replace('\r\n', '\n')
            lines = text_normalized.splitlines(keepends=True)
            modified = False
            result = []
            
            for line in lines:
                if line.strip() == normalized_match_line and not modified:
                    modified = True # Skip appending this line
                else:
                    result.append(line)
            
            return "".join(result) if modified else text_normalized
        
        if sudo:
            remote_temp_path = f"/tmp/delete_line_{os.path.basename(remote_file)}_{int(time.time())}"
            try:
                op_result = self._replace_content_sudo(remote_file, remote_temp_path, modify_func, force=force)
                if isinstance(op_result, dict) and not op_result.get("success", False):
                    return op_result
                return {"success": True}
            except Exception as e:
                self.logger.error(f"Failed to delete line in {remote_file}: {e}")
                return {"success": False, "error": str(e)}
        else:
            if force: self.logger.warning("force=True has no effect when sudo=False")
            try:
                if content is not None: 
                    modified_content = modify_func(content) 
                    if modified_content != content.replace('\r\n', '\n'): 
                        local_temp_fd, local_temp_path = tempfile.mkstemp(text=True)
                        try:
                            with os.fdopen(local_temp_fd, 'w', encoding='utf-8', newline='\n') as f: # Write with LF
                                f.write(modified_content)
                            self.logger.info(f"Content modified for {remote_file}. Uploading changes.")
                            self.put(local_temp_path, remote_file)
                            return {"success": True}
                        finally:
                            if os.path.exists(local_temp_path): os.unlink(local_temp_path)
                    else:
                        return {"success": True, "message": "No changes needed"}
                else: 
                    op_result = self._replace_content_sftp(remote_file, modify_func)
                    return op_result
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
        
        # First check if source file exists
        try:
            with self.ssh_client._client.open_sftp() as sftp:
                try:
                    sftp.stat(source_path)
                except FileNotFoundError:
                    self.logger.error(f"Source file not found: {source_path}")
                    return {"success": False, "error": f"Source file not found: {source_path}"}
        except Exception as e:
            if not sudo:
                self.logger.error(f"Error checking source file: {e}")
                return {"success": False, "error": f"Error checking source file: {str(e)}"}
        
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
                except FileNotFoundError as e: 
                    self.logger.error(f"Source file not found: {source_path}")
                    return {"success": False, "error": f"Source file not found: {source_path}"}
                except Exception as e:
                    self.logger.error(f"Failed to copy file: {e}")
                    return {"success": False, "error": str(e)}
                finally:
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
        os.close(local_temp_fd) 
        self.logger.debug(f"Created local temp file: {local_temp_path}")

        try:
            self.get(remote_file, local_temp_path)

            with open(local_temp_path, 'r', encoding='utf-8', errors='replace') as f:
                original_text = f.read() # Read as is first
                
            try:
                # modify_func is expected to handle internal normalization if needed
                # and return content with LF endings.
                modified_text = modify_func(original_text) 
            except ValueError as e:
                self.logger.error(f"Modification failed: {str(e)}")
                return {"success": False, "error": str(e)}

            # Compare after normalizing original_text to LF for accurate change detection
            if modified_text != original_text.replace('\r\n', '\n'):
                self.logger.info(f"Content modified for {remote_file}. Uploading changes.")
                # Write with LF endings for consistency before upload
                with open(local_temp_path, 'w', encoding='utf-8', newline='\n') as f:
                    f.write(modified_text)
                self.put(local_temp_path, remote_file)
                return {"success": True}
            else:
                self.logger.info(f"Content for {remote_file} not modified, skipping upload.")
                return {"success": True, "message": "No changes needed"}

        except Exception as e:
            self.logger.error(f"File operation failed: {str(e)}")
            return {"success": False, "error": str(e)}
        finally:
            if os.path.exists(local_temp_path):
                self.logger.debug(f"Cleaning up local temp file: {local_temp_path}")
                os.unlink(local_temp_path)

    def _replace_content_sudo(self, remote_file: str, remote_temp_path: str, 
                            modify_func: Callable[[str], str], force: bool = False) -> dict:
        """
        Internal helper for sudo-based file modification.
        """
        local_temp_fd, local_temp_path = tempfile.mkstemp(text=True)
        os.close(local_temp_fd)
        self.logger.debug(f"Created local temp file: {local_temp_path}")
        original_text = None 

        try:
            try:
                self.get(remote_file, local_temp_path)
                with open(local_temp_path, 'r', encoding='utf-8', errors='replace') as f:
                    original_text = f.read()
                self.logger.debug(f"Successfully downloaded original file {remote_file}")
            except Exception as e:
                self.logger.warning(f"Could not download original {remote_file}: {e}. Checking force flag.")
                if not force:
                    raise SshError(f"Cannot read original file {remote_file} and force=False. Aborting replacement.") from e
                else:
                    self.logger.warning("force=True specified. Proceeding with modification assuming empty or irrelevant original content.")
                    original_text = "" 

            try:
                # modify_func is expected to handle internal normalization if needed
                # and return content with LF endings.
                modified_text = modify_func(original_text)
            except ValueError as e:
                self.logger.error(f"Modification failed: {str(e)}")
                return {"success": False, "error": str(e)}

            if modified_text == original_text.replace('\r\n', '\n'):
                self.logger.info(f"Content for {remote_file} not modified, skipping sudo replacement.")
                return {"success": True, "message": "No changes needed"}

            self.logger.info(f"Content modified for {remote_file}. Proceeding with sudo replacement.")
            # Write with LF endings for consistency before upload
            with open(local_temp_path, 'w', encoding='utf-8', newline='\n') as f:
                f.write(modified_text)

            self.put(local_temp_path, remote_temp_path)

            perms = owner = group = None
            if original_text is not None: 
                stat_cmd = f"stat -c '%a %u %g' {shlex.quote(remote_file)}"
                try:
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

            mv_cmd = f"mv {shlex.quote(remote_temp_path)} {shlex.quote(remote_file)}"
            chown_cmd = f"chown {owner}:{group} {shlex.quote(remote_file)}" if owner and group else None
            chmod_cmd = f"chmod {perms} {shlex.quote(remote_file)}" if perms else None

            self.logger.info(f"Executing sudo mv: {mv_cmd}")
            self.ssh_client.run(mv_cmd, sudo=True) 
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
            if os.path.exists(local_temp_path):
                self.logger.debug(f"Cleaning up local temp file: {local_temp_path}")
                os.unlink(local_temp_path)
            try:
                self.logger.debug(f"Cleaning up remote temp file: {remote_temp_path}")
                self.ssh_client.run(f"rm -f {shlex.quote(remote_temp_path)}", io_timeout=10, runtime_timeout=15, sudo=False)
            except Exception as cleanup_err:
                self.logger.warning(f"Failed to cleanup remote temp file {remote_temp_path}: {cleanup_err}")
