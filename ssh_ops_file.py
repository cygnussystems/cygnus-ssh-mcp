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
                   Note: For portability with `grep -E`, avoid PCRE-specific syntax like '`\\d`'.
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
        else: 
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
            for idx, line_content_iter in enumerate(file_lines): # Renamed line_content to avoid conflict
                stripped_line_content = line_content_iter.strip()
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
        elif content is None and not (sudo and force): 
            return {"success": False, "error": "Internal error: Content is None without sudo and force."}


        def modify_func(text):
            text_normalized = text.replace('\r\n', '\n')
            lines = text_normalized.splitlines(keepends=True)
            modified = False
            result = []
            
            for line_iter in lines: # Renamed line to avoid conflict
                if line_iter.strip() == normalized_match_line and not modified: 
                    for new_line_content in new_lines:
                        result.append(new_line_content + '\n') 
                    modified = True
                else:
                    result.append(line_iter) 
            
            return "".join(result) if modified else text_normalized 
        
        if sudo:
            remote_temp_path = f"/tmp/replace_line_{os.path.basename(remote_file)}_{int(time.time())}"
            try:
                op_result = self._replace_content_sudo(remote_file, remote_temp_path, modify_func, 
                                                       sudo=sudo, force=force, 
                                                       original_content_for_check=content if can_check_duplicates else None)
                if isinstance(op_result, dict) and not op_result.get("success", False):
                    return op_result
                if op_result.get("message") == "No changes needed":
                     return {"success": True, "message": "No changes needed (match line not found or content identical)."}
                return {"success": True, "lines_written": len(new_lines)}
            except Exception as e:
                self.logger.error(f"Failed to replace line in {remote_file}: {e}", exc_info=True)
                return {"success": False, "error": str(e)}
        else: 
            if force: self.logger.warning("force=True has no effect when sudo=False for replace_line_by_content")
            if content is None: 
                 return {"success": False, "error": "Internal error: content is None for non-sudo operation."}
            try:
                modified_content = modify_func(content) 
                if modified_content != content.replace('\r\n', '\n'): 
                    local_temp_fd, local_temp_path = tempfile.mkstemp(text=True)
                    try:
                        with os.fdopen(local_temp_fd, 'w', encoding='utf-8', newline='\n') as f: 
                            f.write(modified_content)
                        self.logger.info(f"Content modified for {remote_file}. Uploading changes.")
                        self.put(local_temp_path, remote_file)
                        return {"success": True, "lines_written": len(new_lines)}
                    finally:
                        if os.path.exists(local_temp_path): os.unlink(local_temp_path)
                else: 
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
        else: 
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
            for idx, line_content_iter in enumerate(file_lines): # Renamed line_content to avoid conflict
                stripped_line_content = line_content_iter.strip()
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
            
            for line_iter in lines: # Renamed line to avoid conflict
                result.append(line_iter)
                if line_iter.strip() == normalized_match_line and not modified: 
                    for new_line_content in lines_to_insert:
                        result.append(new_line_content + '\n') 
                    modified = True
            
            return "".join(result) if modified else text_normalized

        if sudo:
            remote_temp_path = f"/tmp/insert_after_{os.path.basename(remote_file)}_{int(time.time())}"
            try:
                op_result = self._replace_content_sudo(remote_file, remote_temp_path, modify_func, 
                                                       sudo=sudo, force=force,
                                                       original_content_for_check=content if can_check_duplicates else None)
                if isinstance(op_result, dict) and not op_result.get("success", False):
                    return op_result
                if op_result.get("message") == "No changes needed":
                     return {"success": True, "message": "No changes needed (match line not found or content identical)."}
                return {"success": True, "lines_inserted": len(lines_to_insert)}
            except Exception as e:
                self.logger.error(f"Failed to insert lines in {remote_file}: {e}", exc_info=True)
                return {"success": False, "error": str(e)}
        else: 
            if force: self.logger.warning("force=True has no effect when sudo=False for insert_lines_after_match")
            if content is None:
                 return {"success": False, "error": "Internal error: content is None for non-sudo operation."}
            try:
                modified_content = modify_func(content) 
                if modified_content != content.replace('\r\n', '\n'): 
                    local_temp_fd, local_temp_path = tempfile.mkstemp(text=True)
                    try:
                        with os.fdopen(local_temp_fd, 'w', encoding='utf-8', newline='\n') as f: 
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
        else: 
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
            for idx, line_content_iter in enumerate(file_lines): # Renamed line_content to avoid conflict
                stripped_line_content = line_content_iter.strip()
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
            
            for line_iter in lines: # Renamed line to avoid conflict
                if line_iter.strip() == normalized_match_line and not modified: 
                    modified = True 
                else:
                    result.append(line_iter)
            
            return "".join(result) if modified else text_normalized
        
        if sudo:
            remote_temp_path = f"/tmp/delete_line_{os.path.basename(remote_file)}_{int(time.time())}"
            try:
                op_result = self._replace_content_sudo(remote_file, remote_temp_path, modify_func, 
                                                       sudo=sudo, force=force,
                                                       original_content_for_check=content if can_check_duplicates else None)
                if isinstance(op_result, dict) and not op_result.get("success", False):
                    return op_result
                if op_result.get("message") == "No changes needed":
                     return {"success": True, "message": "No changes needed (match line not found or content identical)."}
                return {"success": True}
            except Exception as e:
                self.logger.error(f"Failed to delete line in {remote_file}: {e}", exc_info=True)
                return {"success": False, "error": str(e)}
        else: 
            if force: self.logger.warning("force=True has no effect when sudo=False for delete_line_by_content")
            if content is None:
                 return {"success": False, "error": "Internal error: content is None for non-sudo operation."}
            try:
                modified_content = modify_func(content) 
                if modified_content != content.replace('\r\n', '\n'): 
                    local_temp_fd, local_temp_path = tempfile.mkstemp(text=True)
                    try:
                        with os.fdopen(local_temp_fd, 'w', encoding='utf-8', newline='\n') as f: 
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
        actual_destination = destination_path
        if append_timestamp:
            timestamp = time.strftime("%Y%m%dT%H%M%S")
            base, ext = os.path.splitext(destination_path)
            actual_destination = f"{base}.{timestamp}{ext}"
        
        self.logger.info(f"Copying file from {source_path} to {actual_destination} (sudo={sudo})")
        
        try:
            with self.ssh_client._client.open_sftp() as sftp:
                sftp.stat(source_path) 
        except FileNotFoundError:
            self.logger.error(f"Source file not found: {source_path}")
            return {"success": False, "error": f"Source file not found: {source_path}"}
        except Exception as e: 
            if not sudo:
                self.logger.error(f"Error checking source file {source_path}: {e}")
                return {"success": False, "error": f"Error checking source file: {str(e)}"}
            self.logger.warning(f"Could not stat source file {source_path} (error: {e}), but proceeding with sudo copy.")

        
        if sudo:
            cmd = f"cp {shlex.quote(source_path)} {shlex.quote(actual_destination)}"
            try:
                self.ssh_client.run(cmd, sudo=True) 
                return {
                    "success": True,
                    "copied_to": actual_destination
                }
            except CommandFailed as e: 
                stderr_info = f" Stderr: {e.stderr.strip()}" if hasattr(e, 'stderr') and e.stderr else ""
                error_message = f"sudo cp command failed with exit code {e.exit_code}.{stderr_info}"
                self.logger.error(f"Failed to copy file with sudo: {error_message} (Full exception: {e})")
                return {"success": False, "error": error_message}
            except Exception as e: 
                self.logger.error(f"Failed to copy file with sudo: {e}", exc_info=True)
                return {"success": False, "error": str(e)}
        else:
            local_temp_fd, local_temp_path = tempfile.mkstemp()
            os.close(local_temp_fd) 
            try:
                self.get(source_path, local_temp_path) 
                self.put(local_temp_path, actual_destination) 
                return {
                    "success": True,
                    "copied_to": actual_destination
                }
            except FileNotFoundError: 
                self.logger.error(f"Source file not found during SFTP copy: {source_path}")
                return {"success": False, "error": f"Source file not found: {source_path}"}
            except Exception as e: 
                self.logger.error(f"Failed to copy file via SFTP: {e}", exc_info=True)
                return {"success": False, "error": str(e)}
            finally:
                if os.path.exists(local_temp_path):
                    os.unlink(local_temp_path)

    def _replace_content_sftp(self, remote_file: str, modify_func: Callable[[str], str]) -> dict:
        """
        Internal helper for SFTP-based file modification.
        Downloads, modifies, then uploads. Ensures LF line endings on upload.
        """
        local_temp_fd, local_temp_path = tempfile.mkstemp(text=True)
        os.close(local_temp_fd) 
        self.logger.debug(f"Created local temp file: {local_temp_path}")

        try:
            self.get(remote_file, local_temp_path)
            with open(local_temp_path, 'r', encoding='utf-8', errors='replace') as f:
                original_text = f.read() 
                
            modified_text = modify_func(original_text) 

            if modified_text != original_text.replace('\r\n', '\n'):
                self.logger.info(f"Content modified for {remote_file}. Uploading changes.")
                with open(local_temp_path, 'w', encoding='utf-8', newline='\n') as f:
                    f.write(modified_text)
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
                            modify_func: Callable[[str], str], sudo: bool, # Added sudo parameter
                            force: bool = False,
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
        
        try:
            original_text = original_content_for_check
            if original_text is None: 
                try:
                    self.get(remote_file, local_temp_path) 
                    with open(local_temp_path, 'r', encoding='utf-8', errors='replace') as f:
                        original_text = f.read()
                    self.logger.debug(f"Successfully downloaded original file {remote_file} for sudo op.")
                except Exception as e:
                    self.logger.warning(f"Could not download original {remote_file} for sudo op: {e}.")
                    if not force:
                        raise SshError(f"Cannot read original file {remote_file} and force=False. Aborting sudo replacement.") from e
                    else:
                        self.logger.warning("force=True specified. Proceeding with modification assuming empty or irrelevant original content for comparison.")
                        original_text = "" 

            modified_text = modify_func(original_text) 

            if modified_text == original_text.replace('\r\n', '\n'):
                self.logger.info(f"Content for {remote_file} not modified by function, skipping sudo replacement.")
                return {"success": True, "message": "No changes needed"}

            self.logger.info(f"Content modified for {remote_file}. Proceeding with sudo replacement.")
            with open(local_temp_path, 'w', encoding='utf-8', newline='\n') as f:
                f.write(modified_text)

            self.put(local_temp_path, remote_temp_path) 

            perms = owner = group = None
            if not (force and original_text == "" and original_content_for_check is None): 
                stat_cmd = f"stat -c '%a %u %g' {shlex.quote(remote_file)}"
                try:
                    stat_handle = self.ssh_client.run(stat_cmd, io_timeout=10, sudo=False) 
                    stat_output = stat_handle.get_full_output().strip() 
                    parts = stat_output.split()
                    if len(parts) == 3:
                        perms, owner, group = parts
                        self.logger.debug(f"Got permissions for {remote_file}: {perms} {owner}:{group}")
                    else:
                        self.logger.warning(f"Unexpected output from stat command for {remote_file}: '{stat_output}'. Cannot restore permissions/owner.")
                except Exception as stat_err: 
                    self.logger.warning(f"Could not get permissions/owner for {remote_file} (error: {stat_err}). Using defaults if any, or system defaults.")

            mv_cmd = f"mv {shlex.quote(remote_temp_path)} {shlex.quote(remote_file)}"
            self.logger.info(f"Executing sudo mv: {mv_cmd}")
            self.ssh_client.run(mv_cmd, sudo=True) 

            if owner and group:
                chown_cmd = f"chown {owner}:{group} {shlex.quote(remote_file)}"
                try:
                    self.logger.info(f"Executing sudo chown: {chown_cmd}")
                    self.ssh_client.run(chown_cmd, sudo=True)
                except Exception as chown_err: 
                    self.logger.warning(f"Failed to sudo chown {remote_file} to {owner}:{group}: {chown_err}")
            if perms:
                chmod_cmd = f"chmod {perms} {shlex.quote(remote_file)}"
                try:
                    self.logger.info(f"Executing sudo chmod: {chmod_cmd}")
                    self.ssh_client.run(chmod_cmd, sudo=True)
                except Exception as chmod_err: 
                    self.logger.warning(f"Failed to sudo chmod {remote_file} to {perms}: {chmod_err}")

            self.logger.info(f"Successfully replaced {remote_file} using sudo.")
            return {"success": True}

        except ValueError as e: 
            self.logger.error(f"Modification function failed for {remote_file}: {str(e)}")
            return {"success": False, "error": f"Modification logic failed: {str(e)}"}
        except CommandFailed as e:
            stderr_info = f" Stderr: {e.stderr.strip()}" if hasattr(e, 'stderr') and e.stderr else ""
            error_message = f"Sudo file operation (mv/chown/chmod/stat) failed with exit code {e.exit_code}.{stderr_info}"
            self.logger.error(f"{error_message} (Full exception: {e})")
            return {"success": False, "error": error_message}
        except SshError as e: 
            self.logger.error(f"SshError during sudo file operation for {remote_file}: {str(e)}", exc_info=True)
            return {"success": False, "error": f"SSH operation failed: {str(e)}"}
        except Exception as e: 
            self.logger.error(f"Unexpected error during sudo file operation for {remote_file}: {str(e)}", exc_info=True)
            return {"success": False, "error": f"An unexpected error occurred: {str(e)}"}
        finally:
            if os.path.exists(local_temp_path):
                self.logger.debug(f"Cleaning up local temp file: {local_temp_path}")
                os.unlink(local_temp_path)
            
            try:
                self.logger.debug(f"Attempting to clean up remote temp file: {remote_temp_path}")
                if remote_temp_path: 
                    self.ssh_client.run(f"rm -f {shlex.quote(remote_temp_path)}", io_timeout=10, runtime_timeout=15, sudo=False) 
            except Exception as cleanup_err:
                self.logger.warning(f"Failed to cleanup remote temp file {remote_temp_path} (non-sudo): {cleanup_err}. Attempting with sudo if main op was sudo.")
                if sudo and remote_temp_path: # Now 'sudo' is correctly defined in this scope
                    try:
                        self.ssh_client.run(f"rm -f {shlex.quote(remote_temp_path)}", io_timeout=10, runtime_timeout=15, sudo=True)
                    except Exception as sudo_cleanup_err:
                         self.logger.error(f"Failed to cleanup remote temp file {remote_temp_path} even with sudo: {sudo_cleanup_err}")
