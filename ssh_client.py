import paramiko
import socket
import time
import tempfile
import os
import shlex
from collections import deque
from datetime import datetime


class SshError(Exception):
    """Base exception for SSH manager errors."""


class CommandTimeout(SshError):
    def __init__(self, seconds):
        super().__init__(f"Command timed out after {seconds} seconds")
        self.seconds = seconds


class CommandFailed(SshError):
    def __init__(self, exit_code, stdout, stderr):
        super().__init__(f"Command failed with exit code {exit_code}")
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


class SudoRequired(SshError):
    def __init__(self, cmd):
        super().__init__(f"Password-less sudo required for: {cmd}")
        self.cmd = cmd


class BusyError(SshError):
    def __init__(self):
        super().__init__("Another command is currently running")


class OutputPurged(SshError):
    def __init__(self, handle_id):
        super().__init__(f"Output for handle {handle_id} has been purged")
        self.handle_id = handle_id


class TaskNotFound(SshError):
    # Can be raised by output(), task_status(), task_kill()
    def __init__(self, identifier):
        super().__init__(f"No command handle or task found with identifier: {identifier}")
        self.identifier = identifier


class CommandHandle:
    """
    Tracks the state and output of a single SSH command execution.
    For launched commands, tracks the PID.
    """
    def __init__(self, handle_id, cmd, tail_keep=100, pid=None): # Added pid
        self.id = handle_id
        self.cmd = cmd
        self.start_ts = datetime.utcnow()
        self.end_ts = None
        self.exit_code = None # None means not finished or not applicable (launched)
        self.running = True   # True for run() until finished, True for launch() initially
        self.total_lines = 0  # Only relevant for run()
        self.truncated = False # Only relevant for run()
        self._buf = deque(maxlen=tail_keep) # Only relevant for run()
        self.pid = pid # Store the PID for launched commands

    def tail(self, n=50):
        """Return the last n lines of output captured by run()."""
        if self.pid is not None:
            # Output is not captured directly for launched commands
            return ["Output not captured directly for launched commands (PID: {}). Check logs if redirected.".format(self.pid)]
        return list(self._buf)[-n:]

    def chunk(self, start, length=50):
        """Return `length` lines starting at zero-based index `start` from run()."""
        if self.pid is not None:
            raise ValueError("Output chunking not available for launched commands (PID: {}).".format(self.pid))

        if start < 0 or start >= self.total_lines:
            raise ValueError(f"Start {start} out of range (total {self.total_lines})")

        buf_list = list(self._buf)
        buf_start = max(0, self.total_lines - len(buf_list))
        if start < buf_start:
            raise OutputPurged(self.id)
        idx = start - buf_start
        return buf_list[idx:idx+length]

    def info(self):
        """Return metadata about the command."""
        info_dict = {
            "id": self.id,
            "cmd": self.cmd,
            "start_ts": self.start_ts.isoformat() + 'Z',
            "end_ts": self.end_ts.isoformat() + 'Z' if self.end_ts else None,
            "exit_code": self.exit_code,
            "running": self.running,
        }
        if self.pid is not None:
            info_dict["pid"] = self.pid
            # For launched tasks, running status needs external check (task_status)
            # The handle's running status reflects launch time state.
        else:
            # Only include output details for run() commands
            info_dict["total_lines"] = self.total_lines
            info_dict["truncated"] = self.truncated
        return info_dict


class SshClient:
    """
    SSH manager for running commands, transferring files, and tracking history.
    Includes support for launching background tasks and monitoring them.
    """
    def __init__(self, host, user, port=22, keyfile=None, password=None, connect_timeout=10, history_limit=50, tail_keep=100):
        self.host = host
        self.user = user
        self.port = port
        self.keyfile = keyfile
        self.password = password
        self.connect_timeout = connect_timeout
        self._busy = False # Protects synchronous run() calls
        self._history = {} # Stores CommandHandle objects
        self._history_order = deque() # Tracks order for trimming
        self._history_limit = history_limit # Max handles to keep
        self._tail_keep = tail_keep # Default lines to keep per handle buffer
        self._next_id = 1

        # Setup Paramiko client
        self._client = paramiko.SSHClient()
        self._client.load_system_host_keys()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self._connect()

    def _connect(self):
        """Establish SSH connection."""
        kwargs = dict(
            hostname=self.host,
            port=self.port,
            username=self.user,
            timeout=self.connect_timeout
        )
        if self.keyfile:
            kwargs['key_filename'] = self.keyfile
        if self.password:
            kwargs['password'] = self.password
        try:
            self._client.connect(**kwargs)
        except Exception as e:
            raise SshError(f"Connection failed: {e}") from e

    def close(self):
        """Close the SSH connection."""
        if self._client:
            self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def _add_to_history(self, handle):
        """Adds a handle to history and trims if necessary."""
        if len(self._history) >= self._history_limit:
            oldest_id = self._history_order.popleft()
            if oldest_id in self._history:
                del self._history[oldest_id]
                # print(f"DEBUG: Trimmed history, removed handle {oldest_id}") # Optional debug

        self._history[handle.id] = handle
        self._history_order.append(handle.id)

    def run(self, cmd, timeout=None, sudo=False):
        """
        Execute a command synchronously, streaming output into a CommandHandle.
        Returns the CommandHandle upon completion or raises CommandFailed/CommandTimeout.
        """
        if self._busy:
            raise BusyError()
        self._busy = True
        handle = None # Ensure handle is defined for finally block

        try:
            full_cmd = self._build_cmd(cmd, sudo)
            chan = self._client.get_transport().open_session()
            # Set environment variables if needed (e.g., DEBIAN_FRONTEND=noninteractive)
            # chan.set_environment_variable('MYVAR', 'value')

            # PTY allocation might be needed for some commands, but can have side effects
            # chan.get_pty()

            chan.exec_command(full_cmd)

            # Set timeout for the channel operations (read/recv_exit_status)
            # Note: This is an I/O timeout, not a total command execution timeout.
            # Paramiko doesn't directly support a total wall-clock timeout for exec_command.
            if timeout:
                chan.settimeout(timeout)

            stdout = chan.makefile('r', encoding='utf-8', errors='replace')
            stderr = chan.makefile_stderr('r', encoding='utf-8', errors='replace')

            handle_id = self._next_id
            self._next_id += 1
            handle = CommandHandle(handle_id, cmd, tail_keep=self._tail_keep)
            self._add_to_history(handle)

            # Read output line by line
            while True:
                try:
                    # Use readline which respects the channel timeout
                    line = stdout.readline()
                    if not line:
                        # Check if the command finished *after* readline returned empty
                        if chan.exit_status_ready():
                            break
                        # If not finished, it might just be waiting for more output or timeout expired
                        # If timeout expired on readline, socket.timeout would be raised
                        # If no timeout set, this could block indefinitely if command hangs without closing stdout
                        # Let's add a check here to prevent potential infinite loop if no timeout is set
                        if not timeout and not chan.exit_status_ready():
                             # Maybe sleep briefly? Or rely on external monitoring if no timeout?
                             # For now, assume commands eventually finish or timeout is used.
                             pass

                    if line: # Only process if line is not empty
                        handle.total_lines += 1
                        if handle.total_lines > handle._buf.maxlen:
                            handle.truncated = True
                        handle._buf.append(line)

                except socket.timeout:
                    # readline timed out waiting for data. Check if command finished.
                    if chan.exit_status_ready():
                        break # Command finished while we were waiting
                    else:
                        # Command still running, readline timed out.
                        # This indicates I/O inactivity timeout, not necessarily command timeout.
                        # Re-raise as a more specific timeout? For now, let CommandTimeout handle it.
                        stderr_output = stderr.read() # Try to get stderr context
                        raise CommandTimeout(timeout) from None # Raise our specific timeout

            # Command finished or loop broken
            handle.exit_code = chan.recv_exit_status()
            handle.end_ts = datetime.utcnow()
            handle.running = False

            # Read any remaining stderr *after* command completion
            stderr_output = stderr.read()

            # Close channel and streams
            stdout.close()
            stderr.close()
            chan.close()

            if handle.exit_code != 0:
                # Combine stdout buffer and any final stderr for the exception
                stdout_all = ''.join(handle.tail(handle.total_lines))
                raise CommandFailed(handle.exit_code, stdout_all, stderr_output)

            return handle

        except socket.timeout as e:
             # Catch timeout specifically if it wasn't handled inside loop correctly
             if handle:
                 handle.running = False # Mark as finished on timeout error
                 handle.end_ts = datetime.utcnow()
             raise CommandTimeout(timeout) from e
        except paramiko.SSHException as e:
            # Catch SSH specific errors (e.g., channel closed unexpectedly)
            if handle:
                 handle.running = False
                 handle.end_ts = datetime.utcnow()
            raise SshError(f"SSH Error during command execution: {e}") from e
        except Exception as e:
            # Catch other unexpected errors
            if handle:
                 handle.running = False
                 handle.end_ts = datetime.utcnow()
            # Re-raise other exceptions
            raise
        finally:
            self._busy = False # Ensure client is marked not busy

    def launch(self, cmd, sudo=False, stdout_log=None, stderr_log=None):
        """
        Launch a command in the background and return a CommandHandle with the PID.
        Optionally redirects stdout and stderr to specified remote files.
        WARNING: Does not work for interactive commands.
        """
        # Build the command with backgrounding and PID echo
        pid_cmd = f"{cmd}"

        # Add redirection if specified
        if stdout_log:
            pid_cmd += f" >{shlex.quote(stdout_log)}"
        if stderr_log:
            # Redirect stderr (2) to the same file as stdout (1) if they are the same
            if stderr_log == stdout_log:
                pid_cmd += " 2>&1"
            else:
                pid_cmd += f" 2>{shlex.quote(stderr_log)}"

        # Run in background and echo PID
        pid_cmd = f"nohup {pid_cmd} & echo $!"

        full_cmd = self._build_cmd(pid_cmd, sudo)

        try:
            # Use exec_command for simple background launch
            stdin, stdout, stderr = self._client.exec_command(full_cmd, timeout=10) # Short timeout for getting PID

            pid_str = stdout.read().decode('utf-8', errors='replace').strip()
            stderr_output = stderr.read().decode('utf-8', errors='replace').strip()

            stdin.close()
            stdout.close()
            stderr.close()

            if not pid_str.isdigit():
                err_msg = f"Failed to launch command or parse PID. stdout: '{pid_str}', stderr: '{stderr_output}'"
                # Create a failed handle for history
                handle_id = self._next_id
                self._next_id += 1
                handle = CommandHandle(handle_id, cmd, pid=None)
                handle.running = False
                handle.exit_code = -1 # Indicate launch failure
                handle.end_ts = datetime.utcnow()
                self._add_to_history(handle)
                raise SshError(err_msg)

            pid = int(pid_str)
            handle_id = self._next_id
            self._next_id += 1
            # Create handle, mark as running=True initially (process launched)
            handle = CommandHandle(handle_id, cmd, pid=pid)
            self._add_to_history(handle)
            return handle

        except Exception as e:
            raise SshError(f"Failed to launch command: {e}") from e

    def task_status(self, pid):
        """
        Check the status of a process with the given PID on the remote host.
        Returns:
            'running': Process exists.
            'exited': Process does not exist (assumed completed or killed).
            'error': Failed to check status.
        """
        if not isinstance(pid, int) or pid <= 0:
            raise ValueError("Invalid PID provided.")

        # Use kill -0 PID. Exit code 0 means process exists, non-zero means it doesn't.
        cmd = f"kill -0 {pid}"
        try:
            # Use run with a short timeout. We don't care about output, just exit code.
            # Run without sudo first.
            handle = self.run(cmd, timeout=5)
            if handle.exit_code == 0:
                return "running"
            else:
                # This shouldn't happen if kill -0 fails gracefully, but handle defensively
                return "exited"
        except CommandFailed as e:
            # Expected failure if process doesn't exist (kill returns non-zero)
            # stderr might contain "kill: (...) No such process"
            if e.exit_code != 0:
                return "exited"
            else:
                # Unexpected CommandFailed with exit code 0?
                return "error"
        except BusyError:
             # Re-raise busy error, status check cannot proceed
             raise
        except Exception as e:
            # Other errors (timeout, connection issue)
            print(f"Error checking status for PID {pid}: {e}")
            return "error"

    def task_kill(self, pid, signal=15, sudo=False):
        """
        Send a signal to a process with the given PID on the remote host.
        Returns True if the kill command executed successfully (exit code 0),
        False otherwise. Does not guarantee the process actually terminated.
        """
        if not isinstance(pid, int) or pid <= 0:
            raise ValueError("Invalid PID provided.")
        if not isinstance(signal, int):
            # Could also support signal names like 'SIGTERM', 'SIGKILL'
            raise ValueError("Signal must be an integer.")

        cmd = f"kill -{signal} {pid}"
        try:
            handle = self.run(cmd, timeout=10, sudo=sudo)
            return handle.exit_code == 0
        except CommandFailed as e:
            # kill returns non-zero if process doesn't exist or permission denied
            print(f"Command 'kill -{signal} {pid}' failed with exit code {e.exit_code}. Process might already be gone or permissions insufficient.")
            return False
        except BusyError:
             # Re-raise busy error, kill command cannot proceed
             raise
        except Exception as e:
            print(f"Error sending signal {signal} to PID {pid}: {e}")
            return False

    def output(self, handle_id, mode='tail', n=50, start=None):
        """Retrieve output from a previous CommandHandle created by run()."""
        handle = self._history.get(handle_id)
        if not handle:
            raise TaskNotFound(handle_id)

        # Check if it was a launched command
        if handle.pid is not None:
             raise ValueError(f"Direct output retrieval not available for launched command (ID: {handle_id}, PID: {handle.pid}). Check logs if redirected.")

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
        try:
            sftp = self._client.open_sftp()
            sftp.get(remote_path, local_path)
            sftp.close()
        except Exception as e:
            raise SshError(f"SFTP get failed: {e}") from e

    def put(self, local_path, remote_path):
        """Upload a file from local to remote."""
        try:
            sftp = self._client.open_sftp()
            sftp.put(local_path, remote_path)
            sftp.close()
        except Exception as e:
            raise SshError(f"SFTP put failed: {e}") from e

    def replace_line(self, remote_file, old_line, new_line, count=1, sudo=False):
        """
        Replace occurrences of a line in a remote text file.
        Uses temporary local file. Requires write permissions on remote dir/file.
        If sudo=True, attempts to use sudo for the final 'mv' command.
        """
        if sudo:
            # If sudo is needed, direct SFTP put won't work for privileged files.
            # We need to upload to a temp location and then use `sudo mv`.
            remote_temp_path = f"/tmp/replace_line_{os.path.basename(remote_file)}_{int(time.time())}"
            self._replace_content_sudo(remote_file, remote_temp_path, lambda text: self._perform_replace_line(text, old_line, new_line, count))
        else:
            # Standard SFTP approach
            self._replace_content_sftp(remote_file, lambda text: self._perform_replace_line(text, old_line, new_line, count))

    def _perform_replace_line(self, text, old_line, new_line, count):
        """Helper function containing the actual line replacement logic."""
        lines = text.splitlines(keepends=True)
        replaced = 0
        modified = False
        for i, line in enumerate(lines):
            # Check if old_line is a substring of the current line
            if old_line in line and replaced < count:
                lines[i] = line.replace(old_line, new_line)
                replaced += 1
                modified = True
                # No break here, replace up to 'count' occurrences if found in different lines
        return "".join(lines) if modified else text # Return original text if no changes

    def replace_block(self, remote_file, old_block, new_block, sudo=False):
        """
        Replace a block of text in a remote text file.
        Uses temporary local file. Requires write permissions on remote dir/file.
        If sudo=True, attempts to use sudo for the final 'mv' command.
        """
        # Ensure blocks are strings
        old_block_str = "".join(old_block) if isinstance(old_block, (list, tuple)) else str(old_block)
        new_block_str = "".join(new_block) if isinstance(new_block, (list, tuple)) else str(new_block)

        if sudo:
            remote_temp_path = f"/tmp/replace_block_{os.path.basename(remote_file)}_{int(time.time())}"
            self._replace_content_sudo(remote_file, remote_temp_path, lambda text: text.replace(old_block_str, new_block_str))
        else:
            self._replace_content_sftp(remote_file, lambda text: text.replace(old_block_str, new_block_str))

    def _replace_content_sftp(self, remote_file, modify_func):
        """Internal helper for SFTP-based file modification."""
        local_temp_fd, local_temp_path = tempfile.mkstemp(text=True)
        os.close(local_temp_fd) # Close handle, we just need the name

        try:
            # 1. Download
            self.get(remote_file, local_temp_path)

            # 2. Read, Modify, Write locally
            with open(local_temp_path, 'r', encoding='utf-8', errors='replace') as f:
                original_text = f.read()
            modified_text = modify_func(original_text)

            # Only upload if content changed
            if modified_text != original_text:
                with open(local_temp_path, 'w', encoding='utf-8') as f:
                    f.write(modified_text)

                # 3. Upload back
                self.put(local_temp_path, remote_file)
            else:
                print(f"Content for {remote_file} not modified, skipping upload.")

        finally:
            # 4. Cleanup local temp file
            if os.path.exists(local_temp_path):
                os.unlink(local_temp_path)

    def _replace_content_sudo(self, remote_file, remote_temp_path, modify_func):
        """Internal helper for sudo-based file modification."""
        local_temp_fd, local_temp_path = tempfile.mkstemp(text=True)
        os.close(local_temp_fd)

        try:
            # 1. Download original file (best effort, might fail if no read permission)
            try:
                 self.get(remote_file, local_temp_path)
                 with open(local_temp_path, 'r', encoding='utf-8', errors='replace') as f:
                     original_text = f.read()
            except Exception as e:
                 # If we can't read the original, we can't modify based on content.
                 # This approach might need rethinking if read isn't possible.
                 # For now, assume read is possible, or modification doesn't depend on original content.
                 print(f"Warning: Could not download original {remote_file}: {e}. Modification might be incorrect if based on original content.")
                 original_text = "" # Proceed with empty content? Or fail? Let's assume modify_func handles this.

            # 2. Modify content
            modified_text = modify_func(original_text)

            # 3. Write modified content to local temp
            with open(local_temp_path, 'w', encoding='utf-8') as f:
                f.write(modified_text)

            # 4. Upload modified content to REMOTE temp path
            self.put(local_temp_path, remote_temp_path)

            # 5. Use `sudo mv` to replace the original file atomically
            #    Also copy permissions and ownership from original if possible
            #    Get original permissions/owner first
            stat_cmd = f"stat -c '%a %u %g' {shlex.quote(remote_file)}"
            perms = owner = group = None
            try:
                stat_handle = self.run(stat_cmd, timeout=10)
                perms, owner, group = stat_handle.tail(1)[0].strip().split()
            except Exception as stat_err:
                print(f"Warning: Could not get permissions/owner for {remote_file}: {stat_err}. Using defaults.")

            # Build the move and permission commands
            mv_cmd = f"mv {shlex.quote(remote_temp_path)} {shlex.quote(remote_file)}"
            chown_cmd = f"chown {owner}:{group} {shlex.quote(remote_file)}" if owner and group else None
            chmod_cmd = f"chmod {perms} {shlex.quote(remote_file)}" if perms else None

            # Execute commands with sudo
            self.run(mv_cmd, sudo=True)
            if chown_cmd:
                try:
                    self.run(chown_cmd, sudo=True)
                except Exception as chown_err:
                    print(f"Warning: Failed to sudo chown {remote_file}: {chown_err}")
            if chmod_cmd:
                 try:
                     self.run(chmod_cmd, sudo=True)
                 except Exception as chmod_err:
                     print(f"Warning: Failed to sudo chmod {remote_file}: {chmod_err}")

        finally:
            # 6. Cleanup local and remote temp files
            if os.path.exists(local_temp_path):
                os.unlink(local_temp_path)
            # Try removing remote temp file, ignore errors
            try:
                self.run(f"rm -f {shlex.quote(remote_temp_path)}", timeout=10)
            except Exception:
                pass # Ignore cleanup errors

    def reboot(self, wait=True, timeout=300):
        """Reboot the remote host and optionally wait until it comes back."""
        print("Attempting reboot...")
        try:
            # Send reboot command, don't wait for output as connection will drop
            self.run('reboot', sudo=True) # Assuming passwordless sudo for reboot
        except CommandFailed as e:
            # Handle cases where reboot command itself fails immediately
            print(f"Reboot command failed: {e}")
            # Decide if we should still proceed with close/wait logic
            # For now, let's assume failure means no reboot happened.
            return
        except SshError as e:
             # Catch potential connection errors during the run call itself
             print(f"SSH error during reboot command: {e}. Assuming connection lost.")
             # Proceed to close/wait logic as reboot might have started
        finally:
            # Always close the connection after sending reboot
             print("Closing connection post-reboot command.")
             self.close()

        start = time.time()
        if not wait:
            print("Reboot initiated, not waiting for reconnect.")
            return

        print(f"Waiting up to {timeout} seconds for host {self.host} to come back online...")
        while True:
            if time.time() - start > timeout:
                raise CommandTimeout(timeout)
            try:
                print(f"Attempting to reconnect ({int(time.time() - start)}s elapsed)...")
                # Create a fresh client instance for reconnect attempt
                self._client = paramiko.SSHClient()
                self._client.load_system_host_keys()
                self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                self._connect() # Use internal connect method
                print("Reconnect successful.")
                return # Host is back
            except SshError as e:
                # Expected connection errors while host is down
                # print(f"Reconnect attempt failed: {e}") # Verbose logging
                time.sleep(5) # Wait before retrying
            except Exception as e:
                 # Unexpected errors during reconnect attempt
                 print(f"Unexpected error during reconnect attempt: {e}")
                 time.sleep(5)


    def status(self):
        """Return a snapshot of system state using a combined command."""
        # Combined command for efficiency
        cmd = """
        bash -c '
          echo "USER:$(whoami)"
          echo "CWD:$(pwd)"
          echo "TIME:$(date -Is)"
          echo "HOST:$(hostname)"
          echo "UP:$(uptime -p 2>/dev/null || uptime)"
          echo "LOAD:$(cut -d" " -f1-3 /proc/loadavg 2>/dev/null || echo n/a)"
          echo "DISK:$(df -h / 2>/dev/null | awk "NR==2{print $4}" || echo n/a)"
          echo "MEM:$(free -m 2>/dev/null | awk "/^Mem:/{print $4\\" MB\\"}" || echo n/a)"
          if [ -f /etc/os-release ]; then . /etc/os-release; echo "OS:${NAME} ${VERSION_ID}"; else uname -srm; fi
        '
        """
        status_info = {}
        try:
            handle = self.run(cmd.strip(), timeout=5) # Use short timeout
            output = "".join(handle.tail(20)) # Get all lines
            for line in output.splitlines():
                if ':' in line:
                    key, value = line.split(':', 1)
                    # Basic key mapping/cleaning if needed
                    key_map = {
                        'USER': 'user', 'CWD': 'cwd', 'TIME': 'time', 'HOST': 'host',
                        'UP': 'uptime', 'LOAD': 'load_avg', 'DISK': 'free_disk',
                        'MEM': 'mem_free', 'OS': 'os'
                    }
                    status_info[key_map.get(key.strip(), key.strip().lower())] = value.strip()
        except BusyError:
            raise # Propagate busy error
        except Exception as e:
            print(f"Warning: Failed to get full status: {e}")
            # Return partial or default status? For now, return empty dict on error.
            return {'error': str(e)}

        # Ensure all expected keys are present, even if 'n/a'
        expected_keys = ['user', 'cwd', 'time', 'os', 'host', 'uptime', 'load_avg', 'free_disk', 'mem_free']
        for key in expected_keys:
            if key not in status_info:
                status_info[key] = 'n/a'

        return status_info


    def history(self):
        """Return metadata for recent CommandHandles, respecting history order."""
        return [self._history[handle_id].info() for handle_id in self._history_order if handle_id in self._history]

    def _build_cmd(self, cmd, sudo):
        """Internal: wrap command with sudo if requested."""
        if sudo:
            # Use -n for non-interactive sudo. Assumes passwordless setup.
            # Consider adding -S and password handling if needed later.
            # Using bash -c ensures complex commands with pipes/redirects work
            return f"sudo -n bash -c {shlex.quote(cmd)}"
        return cmd

