import os
import tempfile
import shlex
import logging
import time
from typing import Optional, Callable
from ssh_models import SshError

class SshFileOperations:
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

    def replace_line(self, remote_file: str, old_line: str, new_line: str, 
                    count: int = 1, sudo: bool = False, force: bool = False) -> None:
        """
        Replace occurrences of a line in a remote text file.
        
        Args:
            remote_file: Path to remote file
            old_line: Line content to replace
            new_line: New line content
            count: Maximum number of replacements to make
            sudo: Whether to use sudo for the operation
            force: Whether to proceed if original file cannot be read (sudo only)
        """
        self.logger.info(f"Replacing line in {remote_file} (sudo={sudo}, force={force})")
        if sudo:
            remote_temp_path = f"/tmp/replace_line_{os.path.basename(remote_file)}_{int(time.time())}"
            self._replace_content_sudo(
                remote_file, 
                remote_temp_path,
                lambda text: self._perform_replace_line(text, old_line, new_line, count),
                force=force
            )
        else:
            if force:
                self.logger.warning("force=True has no effect when sudo=False for replace_line.")
            self._replace_content_sftp(
                remote_file,
                lambda text: self._perform_replace_line(text, old_line, new_line, count)
            )

    def replace_block(self, remote_file: str, old_block: str, new_block: str, 
                     sudo: bool = False, force: bool = False) -> None:
        """
        Replace a block of text in a remote text file.
        
        Args:
            remote_file: Path to remote file
            old_block: Block content to replace
            new_block: New block content
            sudo: Whether to use sudo for the operation
            force: Whether to proceed if original file cannot be read (sudo only)
        """
        self.logger.info(f"Replacing block in {remote_file} (sudo={sudo}, force={force})")
        # Ensure blocks are strings
        old_block_str = "".join(old_block) if isinstance(old_block, (list, tuple)) else str(old_block)
        new_block_str = "".join(new_block) if isinstance(new_block, (list, tuple)) else str(new_block)

        modify_func = lambda text: text.replace(old_block_str, new_block_str)

        if sudo:
            remote_temp_path = f"/tmp/replace_block_{os.path.basename(remote_file)}_{int(time.time())}"
            self._replace_content_sudo(remote_file, remote_temp_path, modify_func, force=force)
        else:
            if force:
                self.logger.warning("force=True has no effect when sudo=False for replace_block.")
            self._replace_content_sftp(remote_file, modify_func)

    def _perform_replace_line(self, text: str, old_line: str, new_line: str, count: int) -> str:
        """Helper function containing the actual line replacement logic."""
        lines = text.splitlines(keepends=True)
        replaced_count = 0
        modified = False
        new_lines = []
        for line in lines:
            if old_line in line and replaced_count < count:
                new_lines.append(line.replace(old_line, new_line))
                replaced_count += 1
                modified = True
            else:
                new_lines.append(line)
        # Return original text if no changes were made
        return "".join(new_lines) if modified else text

    def _replace_content_sftp(self, remote_file: str, modify_func: Callable[[str], str]) -> None:
        """Internal helper for SFTP-based file modification."""
        local_temp_fd, local_temp_path = tempfile.mkstemp(text=True)
        os.close(local_temp_fd) # Close handle, we just need the name
        self.logger.debug(f"Created local temp file: {local_temp_path}")

        try:
            # 1. Download
            self.get(remote_file, local_temp_path)

            # 2. Read, Modify, Write locally
            with open(local_temp_path, 'r', encoding='utf-8', errors='replace') as f:
                original_text = f.read()
            modified_text = modify_func(original_text)

            # Only upload if content changed
            if modified_text != original_text:
                self.logger.info(f"Content modified for {remote_file}. Uploading changes.")
                with open(local_temp_path, 'w', encoding='utf-8') as f:
                    f.write(modified_text)
                # 3. Upload back
                self.put(local_temp_path, remote_file)
            else:
                self.logger.info(f"Content for {remote_file} not modified, skipping upload.")

        finally:
            # 4. Cleanup local temp file
            if os.path.exists(local_temp_path):
                self.logger.debug(f"Cleaning up local temp file: {local_temp_path}")
                os.unlink(local_temp_path)

    def _replace_content_sudo(self, remote_file: str, remote_temp_path: str, 
                            modify_func: Callable[[str], str], force: bool = False) -> None:
        """Internal helper for sudo-based file modification."""
        local_temp_fd, local_temp_path = tempfile.mkstemp(text=True)
        os.close(local_temp_fd)
        self.logger.debug(f"Created local temp file: {local_temp_path}")
        original_text = None # Initialize

        try:
            # 1. Download original file (best effort, might fail if no read permission)
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
                    original_text = "" # Proceed with empty content if forced

            # 2. Modify content
            modified_text = modify_func(original_text)

            # 3. Check if content actually changed (important!)
            if modified_text == original_text:
                self.logger.info(f"Content for {remote_file} not modified, skipping sudo replacement.")
                return # Exit early, no need to upload or move

            # 4. Write modified content to local temp
            self.logger.info(f"Content modified for {remote_file}. Proceeding with sudo replacement.")
            with open(local_temp_path, 'w', encoding='utf-8') as f:
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
