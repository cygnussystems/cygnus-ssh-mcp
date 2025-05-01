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
from typing import Optional, Callable, Dict, Deque, Any, Union
from ssh_ops_run import SshRunOperations
from ssh_ops_task import SshTaskOperations
from ssh_ops_file import SshFileOperations
from ssh_history import CommandHistoryManager

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
        handle = self._history.get(handle_id)
        if not handle:
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

    def _perform_replace_line(self, text, old_line, new_line, count):
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

    def replace_block(self, remote_file, old_block, new_block, sudo=False, force=False):
        """
        Replace a block of text in a remote text file.
        Uses temporary local file. Requires write permissions on remote dir/file.
        If sudo=True, attempts to use sudo for the final 'mv' command.
        If force=True, proceeds even if original file cannot be read (sudo only).
        """
        return self.file_ops.replace_block(remote_file, old_block, new_block, sudo, force)

    def _replace_content_sftp(self, remote_file, modify_func):
        """Internal helper for SFTP-based file modification."""
        local_temp_fd, local_temp_path = tempfile.mkstemp(text=True)
        os.close(local_temp_fd) # Close handle, we just need the name
        self._logger.debug(f"Created local temp file: {local_temp_path}")

        try:
            # 1. Download
            self.get(remote_file, local_temp_path)

            # 2. Read, Modify, Write locally
            with open(local_temp_path, 'r', encoding='utf-8', errors='replace') as f:
                original_text = f.read()
            modified_text = modify_func(original_text)

            # Only upload if content changed
            if modified_text != original_text:
                self._logger.info(f"Content modified for {remote_file}. Uploading changes.")
                with open(local_temp_path, 'w', encoding='utf-8') as f:
                    f.write(modified_text)
                # 3. Upload back
                self.put(local_temp_path, remote_file)
            else:
                self._logger.info(f"Content for {remote_file} not modified, skipping upload.")

        finally:
            # 4. Cleanup local temp file
            if os.path.exists(local_temp_path):
                self._logger.debug(f"Cleaning up local temp file: {local_temp_path}")
                os.unlink(local_temp_path)

    def _replace_content_sudo(self, remote_file, remote_temp_path, modify_func, force=False):
        """
        Internal helper for sudo-based file modification.
        """
        return self.file_ops._replace_content_sudo(remote_file, remote_temp_path, modify_func, force)

    def reboot(self, wait=True, timeout=300):
        """Reboot the remote host and optionally wait until it comes back."""
        # Note: This method needs to stay in SshClient since it handles connection state
        self._logger.warning("Attempting reboot...")
        try:
            # Use run_ops to execute the reboot command
            self.run_ops.execute_command('reboot', sudo=True, runtime_timeout=10)
            self._logger.info("Reboot command executed successfully (connection likely dropping).")
        except Exception as e:
            self._logger.error(f"Reboot failed: {e}")
            raise SshError(f"Reboot failed: {e}") from e
        finally:
            self.close()

        if wait:
            self._logger.info(f"Waiting up to {timeout} seconds for host {self.host} to come back online...")
            start = time.time()
            while time.time() - start < timeout:
                try:
                    self._connect()  # Try to reconnect
                    self._logger.info("Reconnected successfully after reboot.")
                    return
                except Exception:
                    time.sleep(5)
            raise CommandTimeout(timeout)


    def status(self):
        """Return a snapshot of system state using a combined command."""
        # Combined command for efficiency - use raw string literal
        cmd = r"""
        bash -c '
          echo "USER:$(whoami)"
          echo "CWD:$(pwd)"
          echo "TIME:$(date -Is)"
          echo "HOST:$(hostname)"
          echo "UP:$(uptime -p 2>/dev/null || uptime)"
          echo "LOAD:$(cut -d" " -f1-3 /proc/loadavg 2>/dev/null || echo n/a)"
          echo "DISK:$(df -h / 2>/dev/null | awk "NR==2{print $4}" || echo n/a)"
          echo "MEM:$(free -m 2>/dev/null | awk "/^Mem:/{print $4\" MB\"}" || echo n/a)"
          if [ -f /etc/os-release ]; then . /etc/os-release; echo "OS:${NAME} ${VERSION_ID}"; else uname -srm; fi
        '
        """
        # Note: $4 in awk commands no longer needs escaping due to raw string
        # Escaped quote for " MB" still needed: \"
        status_info = {}
        try:
            # Use run with short timeouts
            handle = self.run(cmd.strip(), io_timeout=5, runtime_timeout=10)
            output = "".join(handle.tail(20)) # Get all lines
            for line in output.splitlines():
                if ':' in line:
                    key, value = line.split(':', 1) # Split only on the first colon
                    key_map = {
                        'USER': 'user', 'CWD': 'cwd', 'TIME': 'time', 'HOST': 'host',
                        'UP': 'uptime', 'LOAD': 'load_avg', 'DISK': 'free_disk',
                        'MEM': 'mem_free', 'OS': 'os'
                    }
                    status_info[key_map.get(key.strip(), key.strip().lower())] = value.strip()
        except BusyError:
            self._logger.warning("Cannot get status: client is busy.")
            raise # Propagate busy error
        except Exception as e:
            self._logger.warning(f"Failed to get full status: {e}", exc_info=True)
            return {'error': str(e)} # Return error dict

        # Ensure all expected keys are present, even if 'n/a'
        expected_keys = ['user', 'cwd', 'time', 'os', 'host', 'uptime', 'load_avg', 'free_disk', 'mem_free']
        for key in expected_keys:
            if key not in status_info:
                status_info[key] = 'n/a'

        return status_info


    def history(self):
        """Return metadata for recent CommandHandles."""
        return self.history_manager.get_history()

    # _build_cmd helper removed as logic is inlined or handled directly
