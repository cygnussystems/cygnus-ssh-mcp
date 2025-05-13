import os
import tempfile
import shlex
import logging
import time
from typing import Optional, Callable
from ssh_models import SshError, CommandFailed # Added CommandFailed import

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
            regex: Whether to treat pattern as a regular expression. If True, uses grep -E.
                   Note: For portability with `grep -E`, avoid PCRE-specific syntax like `\d`.
                   Use POSIX ERE compatible patterns (e.g., `[0-9]` or `[[:digit:]]` for digits).
            sudo: Whether to use sudo for the operation
            
        Returns:
            Dictionary with total matches and list of matches (line number and content).
            If no matches are found, 'total_matches' is 0 and 'matches' is an empty list.
            If an error occurs (e.g., file not found, permission issues), an 'error' key will be present.
        """
        self.logger.info(f"Searching for pattern '{pattern}' in {remote_file} (regex={regex}, sudo={sudo})")
        
        grep_option = "-E" if regex else "-F" # -F for fixed string (literal), -E for extended regex
        
        # Use shlex.quote for the pattern to handle special characters safely for the shell.
        # The '--' argument signifies the end of options, so patterns starting with '-' are not misinterpreted by grep.
        cmd = f"grep {grep_option} -n -- {shlex.quote(pattern)} {shlex.quote(remote_file)}"
        
        try:
            handle = self.ssh_client.run(cmd, sudo=sudo)
            # If exit_code is 0, grep found matches.
            output_str = handle.get_full_output() # This is a string
            
            matches = []
            # Ensure output_str is not None before splitting. If output_str is empty, splitlines() is empty list.
            for line in (output_str or "").splitlines(): 
                if ":" in line: # Expect "line_number:content"
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        line_num_str, content = parts
                        try:
                            line_num = int(line_num_str)
                            matches.append({"line_number": line_num, "content": content})
                        except ValueError:
                            self.logger.warning(f"Could not parse line number from grep output line: '{line}'")
                    else: 
                        self.logger.warning(f"Unexpected grep output format (malformed split): '{line}'")
            
            return {
                "total_matches": len(matches),
                "matches": matches
            }
        except CommandFailed as e:
            if e.exit_code == 1: # grep returns 1 if no lines were selected (not an error for this function's purpose)
                self.logger.info(f"No matches found in {remote_file} for pattern '{pattern}' (grep exit code 1).")
                return {"total_matches": 0, "matches": []}
            else: # Other exit codes (>=2) indicate an error with grep execution (e.g., file not found, permissions).
                stderr_info = f" Stderr: {e.stderr.strip()}" if hasattr(e, 'stderr') and e.stderr else ""
                error_message = f"grep command failed with exit code {e.exit_code} while searching {remote_file}.{stderr_info}"
                self.logger.error(f"{error_message} (Full exception details: {e})")
                
                # Check for common file-related issues in stderr, which grep might report with exit code 2
                if hasattr(e, 'stderr') and e.stderr: # Ensure stderr exists and is not empty
                    if "No such file or directory" in e.stderr:
                        return {"total_matches": 0, "matches": [], "error": f"File not found: {remote_file}"}
                    if "Permission denied" in e.stderr:
                        err_msg = f"Permission denied for {remote_file}"
                        if not sudo: err_msg += ". Try with sudo=True"
                        return {"total_matches": 0, "matches": [], "error": err_msg}
                
                return {"total_matches": 0, "matches": [], "error": error_message}
        except SshError as e: # Catch other SshErrors (e.g. connection problems, timeouts not caught by run)
            self.logger.error(f"SshError during find_lines_with_pattern for {remote_file}: {e}", exc_info=True)
            # Attempt to provide a more specific common error message based on string content
            str_e = str(e).lower()
            if "no such file or directory" in str_e:
                return {"total_matches": 0, "matches": [], "error": f"File not found: {remote_file}"}
            if "permission denied" in str_e:
                err_msg = f"Permission denied for {remote_file}"
                if not sudo: err_msg += ". Try with sudo=True"
                return {"total_matches": 0, "matches": [], "error": err_msg}
            return {"total_matches": 0, "matches": [], "error": f"SSH operation failed: {str(e)}"}
        except Exception as e: # Catch any other unexpected errors
            self.logger.error(f"Unexpected error in find_lines_with_pattern for {remote_file}: {e}", exc_info=True)
            return {"total_matches": 0, "matches": [], "error": f"An unexpected error occurred: {str(e)}"}

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
        
        if 'error' in find_result: # Propagate error from find_lines_with_pattern
            return {"match_found": False, "error": find_result['error']}

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
            for line_content in output.splitlines(): # Renamed 'line' to 'line_content' to avoid conflict
                context_block.append({"line_number": current_line, "content": line_content})
                current_line += 1
            
            return {
                "match_found": True,
                "match_line_number": match_line_number,
                "context_block": context_block
            }
        except CommandFailed as e: # Handle sed command failure
            stderr_info = f" Stderr: {e.stderr.strip()}" if hasattr(e, 'stderr') and e.stderr else ""
            error_message = f"sed command failed with exit code {e.exit_code}.{stderr_info}"
            self.logger.error(f"Error getting context from {remote_file}: {error_message} (Full exception: {e})")
            return {"match_found": False, "error": error_message}
        except Exception as e: # General errors
            self.logger.error(f"Error getting context around line in {remote_file}: {e}", exc_info=True)
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
            if not sudo or not force: # If not sudo, or sudo but not force, this is an error
                return {"success": False, "error": f"Error accessing file {remote_file}: {str(e)}"}
            # If sudo and force, we might proceed if reading fails later, but stat failure is still problematic.
            # However, the original logic implies we might try to proceed if force=True.
            # For now, let's consider stat failure critical unless forced.
            self.logger.warning(f"Initial stat failed for {remote_file} but sudo and force are set: {e}")


        content = None
        # This block tries to read the file to check for unique match if not (sudo and force)
        # If (sudo and force), it might skip this read or ignore its failure.
        can_check_duplicates = True
        if not (sudo and force): # If not (sudo and force), we must be able to read to check duplicates
            try:
                with self.ssh_client._client.open_sftp() as sftp:
                    with sftp.file(remote_file, 'r') as f:
                        content = f.read().decode('utf-8', errors='replace')
            except Exception as e:
                self.logger.error(f"Cannot read file {remote_file} to check for duplicate lines: {str(e)}")
                return {"success": False, "error": f"Cannot read file to check for duplicate lines: {str(e)}"}
        else: # sudo and force is true
            try: # Still attempt to read, but don't fail outright if it doesn't work
                with self.ssh_client._client.open_sftp() as sftp:
                    with sftp.file(remote_file, 'r') as f:
                        content = f.read().decode('utf-8', errors='replace')
            except Exception as e:
                self.logger.warning(f"Could not read {remote_file} to check duplicates (sudo and force active): {e}. Proceeding without check.")
                can_check_duplicates = False # Cannot check, will assume unique if we proceed

        if can_check_duplicates and content is not None:
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
        elif not can_check_duplicates and sudo and force:
             self.logger.warning(f"Proceeding with replace operation on {remote_file} without duplicate check due to sudo and force flags and read failure.")
        elif content is None and not (sudo and force): # Should have been caught by read error above
            return {"success": False, "error": "Internal error: Content is None without sudo and force."}


        def modify_func(text):
            # Ensure text uses LF line endings before processing
            text_normalized = text.replace('\r\n', '\n')
            lines = text_normalized.splitlines(keepends=True)
            modified = False
            result = []
            
            # In case of (sudo and force) and read failure, text might be empty.
            # The loop will run, find no match, and modified will be false.
            # _replace_content_sudo will then write the "modified" (empty) text if original was also empty.
            # Or, if original was not empty but unreadable, it will overwrite. This is the 'force' aspect.

            for line in lines:
                if line.strip() == normalized_match_line and not modified: # Only replace first unique match
                    for new_line_content in new_lines:
                        result.append(new_line_content + '\n') # Ensure new lines use LF
                    modified = True
                else:
                    result.append(line) # Original line (with LF if normalized)
            
            # If modified is False, it means the match_line wasn't found (e.g. if content was empty due to read failure + force)
            # In this case, return the original (potentially empty) text.
            return "".join(result) if modified else text_normalized 
        
        if sudo:
            remote_temp_path = f"/tmp/replace_line_{os.path.basename(remote_file)}_{int(time.time())}"
            try:
                # Pass content to _replace_content_sudo if it was read, for optimization
                # Pass can_check_duplicates to inform _replace_content_sudo
                op_result = self._replace_content_sudo(remote_file, remote_temp_path, modify_func, force=force, 
                                                       original_content_for_check=content if can_check_duplicates else None)
                if isinstance(op_result, dict) and not op_result.get("success", False):
                    return op_result
                # Check if modification actually happened based on modify_func's behavior
                if op_result.get("message") == "No changes needed":
                     return {"success": True, "message": "No changes needed (match line not found or content identical)."}
                return {"success": True, "lines_written": len(new_lines)}
            except Exception as e:
                self.logger.error(f"Failed to replace line in {remote_file}: {e}", exc_info=True)
                return {"success": False, "error": str(e)}
        else: # Not sudo
            if force: self.logger.warning("force=True has no effect when sudo=False for replace_line_by_content")
            # Content must have been read successfully if not sudo, otherwise earlier error.
            if content is None: # Should not happen due to checks above
                 return {"success": False, "error": "Internal error: content is None for non-sudo operation."}
            try:
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
                else: # No modification occurred (e.g. match line not found)
                    return {"success": True, "message": "No changes needed (match line not found or content identical)."}
            except Exception as e:
                self.logger.error(f"Failed to replace line in {remote_file}: {e}", exc_info=True)
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
                return {"success": False, "error": f"Error accessing file {remote_file}: {str(e)}"}
            self.logger.warning(f"Initial stat failed for {remote_file} but sudo and force are set: {e}")

        content = None
        can_check_duplicates = True
        if not (sudo and force):
            try:
                with self.ssh_client._client.open_sftp() as sftp:
                    with sftp.file(remote_file, 'r') as f:
                        content = f.read().decode('utf-8', errors='replace')
            except Exception as e:
                self.logger.error(f"Cannot read file {remote_file} to check for duplicate lines: {str(e)}")
                return {"success": False, "error": f"Cannot read file to check for duplicate lines: {str(e)}"}
        else: # sudo and force
            try:
                with self.ssh_client._client.open_sftp() as sftp:
                    with sftp.file(remote_file, 'r') as f:
                        content = f.read().decode('utf-8', errors='replace')
            except Exception as e:
                self.logger.warning(f"Could not read {remote_file} to check duplicates (sudo and force active): {e}. Proceeding without check.")
                can_check_duplicates = False
        
        if can_check_duplicates and content is not None:
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
        elif not can_check_duplicates and sudo and force:
            self.logger.warning(f"Proceeding with insert operation on {remote_file} without duplicate check due to sudo and force flags and read failure.")
        elif content is None and not (sudo and force):
            return {"success": False, "error": "Internal error: Content is None without sudo and force."}


        def modify_func(text):
            text_normalized = text.replace('\r\n', '\n')
            lines = text_normalized.splitlines(keepends=True)
            modified = False
            result = []
            
            for line in lines:
                result.append(line)
                if line.strip() == normalized_match_line and not modified: # Insert after first unique match
                    for new_line_content in lines_to_insert:
                        result.append(new_line_content + '\n') # Ensure new lines use LF
                    modified = True
            
            return "".join(result) if modified else text_normalized

        if sudo:
            remote_temp_path = f"/tmp/insert_after_{os.path.basename(remote_file)}_{int(time.time())}"
            try:
                op_result = self._replace_content_sudo(remote_file, remote_temp_path, modify_func, force=force,
                                                       original_content_for_check=content if can_check_duplicates else None)
                if isinstance(op_result, dict) and not op_result.get("success", False):
                    return op_result
                if op_result.get("message") == "No changes needed":
                     return {"success": True, "message": "No changes needed (match line not found or content identical)."}
                return {"success": True, "lines_inserted": len(lines_to_insert)}
            except Exception as e:
                self.logger.error(f"Failed to insert lines in {remote_file}: {e}", exc_info=True)
                return {"success": False, "error": str(e)}
        else: # Not sudo
            if force: self.logger.warning("force=True has no effect when sudo=False for insert_lines_after_match")
            if content is None:
                 return {"success": False, "error": "Internal error: content is None for non-sudo operation."}
            try:
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
                    return {"success": True, "message": "No changes needed (match line not found or content identical)."}
            except Exception as e:
                self.logger.error(f"Failed to insert lines in {remote_file}: {e}", exc_info=True)
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
                return {"success": False, "error": f"Error accessing file {remote_file}: {str(e)}"}
            self.logger.warning(f"Initial stat failed for {remote_file} but sudo and force are set: {e}")

        content = None
        can_check_duplicates = True
        if not (sudo and force):
            try:
                with self.ssh_client._client.open_sftp() as sftp:
                    with sftp.file(remote_file, 'r') as f:
                        content = f.read().decode('utf-8', errors='replace')
            except Exception as e:
                self.logger.error(f"Cannot read file {remote_file} to check for duplicate lines: {str(e)}")
                return {"success": False, "error": f"Cannot read file to check for duplicate lines: {str(e)}"}
        else: # sudo and force
            try:
                with self.ssh_client._client.open_sftp() as sftp:
                    with sftp.file(remote_file, 'r') as f:
                        content = f.read().decode('utf-8', errors='replace')
            except Exception as e:
                self.logger.warning(f"Could not read {remote_file} to check duplicates (sudo and force active): {e}. Proceeding without check.")
                can_check_duplicates = False

        if can_check_duplicates and content is not None:
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
        elif not can_check_duplicates and sudo and force:
            self.logger.warning(f"Proceeding with delete operation on {remote_file} without duplicate check due to sudo and force flags and read failure.")
        elif content is None and not (sudo and force):
            return {"success": False, "error": "Internal error: Content is None without sudo and force."}

        
        def modify_func(text):
            text_normalized = text.replace('\r\n', '\n')
            lines = text_normalized.splitlines(keepends=True)
            modified = False
            result = []
            
            for line in lines:
                if line.strip() == normalized_match_line and not modified: # Delete first unique match
                    modified = True # Skip appending this line
                else:
                    result.append(line)
            
            return "".join(result) if modified else text_normalized
        
        if sudo:
            remote_temp_path = f"/tmp/delete_line_{os.path.basename(remote_file)}_{int(time.time())}"
            try:
                op_result = self._replace_content_sudo(remote_file, remote_temp_path, modify_func, force=force,
                                                       original_content_for_check=content if can_check_duplicates else None)
                if isinstance(op_result, dict) and not op_result.get("success", False):
                    return op_result
                if op_result.get("message") == "No changes needed":
                     return {"success": True, "message": "No changes needed (match line not found or content identical)."}
                return {"success": True}
            except Exception as e:
                self.logger.error(f"Failed to delete line in {remote_file}: {e}", exc_info=True)
                return {"success": False, "error": str(e)}
        else: # Not sudo
            if force: self.logger.warning("force=True has no effect when sudo=False for delete_line_by_content")
            if content is None:
                 return {"success": False, "error": "Internal error: content is None for non-sudo operation."}
            try:
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
                    return {"success": True, "message": "No changes needed (match line not found or content identical)."}
            except Exception as e:
                self.logger.error(f"Failed to delete line in {remote_file}: {e}", exc_info=True)
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
        
        # First check if source file exists using SFTP stat, which is generally more reliable for existence checks
        # This check is done without sudo, assuming source_path should be stat-able by the user.
        # If sudo is required to even stat the source, the cp command with sudo will handle it.
        try:
            with self.ssh_client._client.open_sftp() as sftp:
                sftp.stat(source_path) # Raises FileNotFoundError if not found
        except FileNotFoundError:
            self.logger.error(f"Source file not found: {source_path}")
            return {"success": False, "error": f"Source file not found: {source_path}"}
        except Exception as e: # Other SFTP errors (e.g. permission denied on stat)
            # If we can't stat, and not using sudo for copy, it's an error.
            # If using sudo for copy, we can let `cp` try and fail.
            if not sudo:
                self.logger.error(f"Error checking source file {source_path}: {e}")
                return {"success": False, "error": f"Error checking source file: {str(e)}"}
            self.logger.warning(f"Could not stat source file {source_path} (error: {e}), but proceeding with sudo copy.")

        
        if sudo:
            # Use cp command with sudo
            cmd = f"cp {shlex.quote(source_path)} {shlex.quote(actual_destination)}"
            try:
                self.ssh_client.run(cmd, sudo=True) # run() will raise CommandFailed on error
                return {
                    "success": True,
                    "copied_to": actual_destination
                }
            except CommandFailed as e: # Catch specific command failure
                stderr_info = f" Stderr: {e.stderr.strip()}" if hasattr(e, 'stderr') and e.stderr else ""
                error_message = f"sudo cp command failed with exit code {e.exit_code}.{stderr_info}"
                self.logger.error(f"Failed to copy file with sudo: {error_message} (Full exception: {e})")
                return {"success": False, "error": error_message}
            except Exception as e: # Other SshErrors or unexpected errors
                self.logger.error(f"Failed to copy file with sudo: {e}", exc_info=True)
                return {"success": False, "error": str(e)}
        else:
            # Use SFTP for non-sudo copy
            local_temp_fd, local_temp_path = tempfile.mkstemp()
            os.close(local_temp_fd) # Closed because get/put will open/close
            try:
                self.get(source_path, local_temp_path) # Downloads source to local temp
                self.put(local_temp_path, actual_destination) # Uploads from local temp to dest
                return {
                    "success": True,
                    "copied_to": actual_destination
                }
            except FileNotFoundError: # Should be caught by initial stat, but as a safeguard
                self.logger.error(f"Source file not found during SFTP copy: {source_path}")
                return {"success": False, "error": f"Source file not found: {source_path}"}
            except Exception as e: # Other SFTP errors
                self.logger.error(f"Failed to copy file via SFTP: {e}", exc_info=True)
                return {"success": False, "error": str(e)}
            finally:
                if os.path.exists(local_temp_path):
                    os.unlink(local_temp_path)

    def _replace_content_sftp(self, remote_file: str, modify_func: Callable[[str], str]) -> dict:
        """
        Internal helper for SFTP-based file modification.
        Downloads, modifies, then uploads. Ensures LF line endings on upload.
        
        Args:
            remote_file: Path to remote file
            modify_func: Function that takes file content (str) and returns modified content (str).
                         This function is expected to return content with LF line endings.
            
        Returns:
            Dictionary with success status and error message if applicable
        """
        local_temp_fd, local_temp_path = tempfile.mkstemp(text=True)
        # fdopen immediately after mkstemp to control encoding and newline for reading/writing
        # However, we download first, then read, then write, then upload.
        # So, we'll handle file opening/closing carefully.
        os.close(local_temp_fd) # We'll open it ourselves.
        self.logger.debug(f"Created local temp file: {local_temp_path}")

        try:
            # Download the remote file to the local temporary path
            self.get(remote_file, local_temp_path)

            # Read the content from the local temporary file
            with open(local_temp_path, 'r', encoding='utf-8', errors='replace') as f:
                original_text = f.read() 
                
            # Apply the modification function
            # modify_func is expected to handle internal normalization if needed
            # and return content with LF endings.
            modified_text = modify_func(original_text) 

            # Compare after normalizing original_text to LF for accurate change detection
            # This ensures we only upload if there's an actual content change.
            if modified_text != original_text.replace('\r\n', '\n'):
                self.logger.info(f"Content modified for {remote_file}. Uploading changes.")
                # Write the modified content back to the local temporary file, ensuring LF line endings
                with open(local_temp_path, 'w', encoding='utf-8', newline='\n') as f:
                    f.write(modified_text)
                # Upload the modified local temporary file back to the remote path
                self.put(local_temp_path, remote_file)
                return {"success": True}
            else:
                self.logger.info(f"Content for {remote_file} not modified by function, skipping upload.")
                return {"success": True, "message": "No changes needed"}

        except Exception as e:
            self.logger.error(f"SFTP file operation failed for {remote_file}: {str(e)}", exc_info=True)
            return {"success": False, "error": str(e)}
        finally:
            if os.path.exists(local_temp_path):
                self.logger.debug(f"Cleaning up local temp file: {local_temp_path}")
                os.unlink(local_temp_path)

    def _replace_content_sudo(self, remote_file: str, remote_temp_path: str, 
                            modify_func: Callable[[str], str], force: bool = False,
                            original_content_for_check: Optional[str] = None) -> dict:
        """
        Internal helper for sudo-based file modification.
        Uses a temporary remote file for atomicity. Ensures LF line endings in the temp file.
        If original_content_for_check is provided, it's used to determine if changes occurred.
        If not (e.g. due to read failure + force), it relies on modify_func applied to "" or actual content.
        """
        local_temp_fd, local_temp_path = tempfile.mkstemp(text=True)
        os.close(local_temp_fd)
        self.logger.debug(f"Created local temp file for sudo operation: {local_temp_path}")
        
        # Determine original text: use provided, or download, or empty if forced and download fails.
        original_text = original_content_for_check
        if original_text is None: # Not provided (e.g. initial read failed but sudo+force)
            try:
                self.get(remote_file, local_temp_path) # Download to local temp
                with open(local_temp_path, 'r', encoding='utf-8', errors='replace') as f:
                    original_text = f.read()
                self.logger.debug(f"Successfully downloaded original file {remote_file} for sudo op.")
            except Exception as e:
                self.logger.warning(f"Could not download original {remote_file} for sudo op: {e}.")
                if not force:
                    raise SshError(f"Cannot read original file {remote_file} and force=False. Aborting sudo replacement.") from e
                else:
                    self.logger.warning("force=True specified. Proceeding with modification assuming empty or irrelevant original content for comparison.")
                    original_text = "" # Assume empty for comparison if forced and unreadable

        try:
            # Apply modification. modify_func should return content with LF endings.
            modified_text = modify_func(original_text)
        except ValueError as e: # If modify_func itself raises an error (e.g. validation)
            self.logger.error(f"Modification function failed for {remote_file}: {str(e)}")
            return {"success": False, "error": f"Modification logic failed: {str(e)}"}

        # Compare modified_text with original_text (normalized to LF) to see if changes occurred.
        if modified_text == original_text.replace('\r\n', '\n'):
            self.logger.info(f"Content for {remote_file} not modified by function, skipping sudo replacement.")
            # Clean up local temp file as it's not needed for upload
            if os.path.exists(local_temp_path):
                 os.unlink(local_temp_path)
            return {"success": True, "message": "No changes needed"}

        self.logger.info(f"Content modified for {remote_file}. Proceeding with sudo replacement.")
        # Write modified content (with LF endings) to local temp file for upload
        with open(local_temp_path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(modified_text)

        # Upload the modified local file to a temporary remote path
        self.put(local_temp_path, remote_temp_path)

        # Get permissions and ownership of the original remote file to restore them later
        perms = owner = group = None
        # Only try to get stats if original_text was not from a forced empty string (i.e., file was likely readable)
        if not (force and original_text == "" and original_content_for_check is None): 
            stat_cmd = f"stat -c '%a %u %g' {shlex.quote(remote_file)}"
            try:
                # Run stat without sudo, as the user might own the file or have read perms for stat
                stat_handle = self.ssh_client.run(stat_cmd, io_timeout=10, sudo=False) 
                stat_output = stat_handle.get_full_output().strip() # Use get_full_output
                parts = stat_output.split()
                if len(parts) == 3:
                    perms, owner, group = parts
                    self.logger.debug(f"Got permissions for {remote_file}: {perms} {owner}:{group}")
                else:
                    self.logger.warning(f"Unexpected output from stat command for {remote_file}: '{stat_output}'. Cannot restore permissions/owner.")
            except Exception as stat_err: # Includes CommandFailed if stat fails
                self.logger.warning(f"Could not get permissions/owner for {remote_file} (error: {stat_err}). Using defaults if any, or system defaults.")

        # Atomically replace the original file with the temporary file using sudo mv
        mv_cmd = f"mv {shlex.quote(remote_temp_path)} {shlex.quote(remote_file)}"
        self.logger.info(f"Executing sudo mv: {mv_cmd}")
        self.ssh_client.run(mv_cmd, sudo=True) # run() will raise CommandFailed on error

        # Restore ownership and permissions if obtained
        if owner and group:
            chown_cmd = f"chown {owner}:{group} {shlex.quote(remote_file)}"
            try:
                self.logger.info(f"Executing sudo chown: {chown_cmd}")
                self.ssh_client.run(chown_cmd, sudo=True)
            except Exception as chown_err: # Includes CommandFailed
                self.logger.warning(f"Failed to sudo chown {remote_file} to {owner}:{group}: {chown_err}")
        if perms:
            chmod_cmd = f"chmod {perms} {shlex.quote(remote_file)}"
            try:
                self.logger.info(f"Executing sudo chmod: {chmod_cmd}")
                self.ssh_client.run(chmod_cmd, sudo=True)
            except Exception as chmod_err: # Includes CommandFailed
                self.logger.warning(f"Failed to sudo chmod {remote_file} to {perms}: {chmod_err}")

        self.logger.info(f"Successfully replaced {remote_file} using sudo.")
        return {"success": True}

        # Catch CommandFailed from mv/chown/chmod, or other SshErrors
        except CommandFailed as e:
            stderr_info = f" Stderr: {e.stderr.strip()}" if hasattr(e, 'stderr') and e.stderr else ""
            error_message = f"Sudo file operation (mv/chown/chmod) failed with exit code {e.exit_code}.{stderr_info}"
            self.logger.error(f"{error_message} (Full exception: {e})")
            return {"success": False, "error": error_message}
        except Exception as e: # Catch other unexpected errors during sudo sequence
            self.logger.error(f"Sudo file operation failed for {remote_file}: {str(e)}", exc_info=True)
            return {"success": False, "error": str(e)}
        finally:
            # Clean up local temporary file
            if os.path.exists(local_temp_path):
                self.logger.debug(f"Cleaning up local temp file: {local_temp_path}")
                os.unlink(local_temp_path)
            # Clean up remote temporary file (attempt with sudo=False first, then sudo=True if needed, or just sudo=True if perms are strict)
            # For simplicity, using sudo=True for cleanup if the operation itself was sudo.
            # If remote_temp_path was created by user (put), then user can rm. If by sudo, then sudo rm.
            # Since `put` is by user, `rm` by user should be fine.
            try:
                self.logger.debug(f"Cleaning up remote temp file: {remote_temp_path}")
                self.ssh_client.run(f"rm -f {shlex.quote(remote_temp_path)}", io_timeout=10, runtime_timeout=15, sudo=False) # Try non-sudo first
            except Exception as cleanup_err:
                self.logger.warning(f"Failed to cleanup remote temp file {remote_temp_path} (non-sudo): {cleanup_err}. Attempting with sudo if main op was sudo.")
                if sudo: # If main operation was sudo, temp file might need sudo to delete if ownership changed.
                    try:
                        self.ssh_client.run(f"rm -f {shlex.quote(remote_temp_path)}", io_timeout=10, runtime_timeout=15, sudo=True)
                    except Exception as sudo_cleanup_err:
                         self.logger.error(f"Failed to cleanup remote temp file {remote_temp_path} even with sudo: {sudo_cleanup_err}")
