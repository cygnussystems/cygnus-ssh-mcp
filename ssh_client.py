from __future__ import annotations
import paramiko
import socket
import time
import tempfile
import os
import shlex
from collections import deque
from datetime import datetime
import logging
import threading
import select
from typing import Optional, Callable, Dict, Deque, Any, Union
from ssh_ops_run import SshRunOperations
from ssh_ops_task import SshTaskOperations
from ssh_ops_file import SshFileOperations

# Configure basic logging for the library
log = logging.getLogger(__name__)
# Example basic config (users of the library should configure logging themselves)
# logging.basicConfig(level=logging.INFO)


class SshError(Exception):
    """Base exception for SSH manager errors."""


class CommandTimeout(SshError):
    """Raised for I/O timeouts during command execution."""
    def __init__(self, seconds):
        super().__init__(f"Command I/O timed out after {seconds} seconds of inactivity")
        self.seconds = seconds

class CommandRuntimeTimeout(SshError):
    """Raised when a command exceeds its total allowed runtime_timeout."""
    def __init__(self, handle, seconds):
        super().__init__(f"Command exceeded runtime timeout of {seconds}s (PID: {handle.pid}, ID: {handle.id})")
        self.handle = handle
        self.seconds = seconds


class CommandFailed(SshError):
    def __init__(self, exit_code, stdout, stderr):
        # Ensure stderr is string for consistent error message
        stderr_str = stderr if isinstance(stderr, str) else stderr.decode('utf-8', errors='replace')
        super().__init__(f"Command failed with exit code {exit_code}. Stderr: {stderr_str[:200]}") # Limit stderr in message
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


class SudoRequired(SshError):
    def __init__(self, cmd):
        super().__init__(f"Password-less sudo required, or sudo password not provided for: {cmd}")
        self.cmd = cmd


class BusyError(SshError):
    def __init__(self):
        super().__init__("Another synchronous command (run) is currently executing")


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
        self.pid = pid # Store the PID for launched commands and run() commands

    def tail(self, n=50):
        """Return the last n lines of output captured by run()."""
        # Output buffer is primarily populated by run()
        return list(self._buf)[-n:]

    def chunk(self, start, length=50):
        """Return `length` lines starting at zero-based index `start` from run()."""
        # Output chunking works for run() commands.
        if start < 0: # Allow start=0 even if total_lines is 0
             raise ValueError(f"Start index {start} cannot be negative")

        buf_list = list(self._buf)
        # Calculate the absolute index of the first element currently in the deque buffer
        buf_start_abs_index = max(0, self.total_lines - len(buf_list))

        if start < buf_start_abs_index:
            # Requested start index is before the first line currently stored
            raise OutputPurged(self.id)

        # Calculate the index relative to the start of the current buffer
        relative_start_idx = start - buf_start_abs_index
        return buf_list[relative_start_idx : relative_start_idx + length]

    def info(self):
        """Return metadata about the command."""
        info_dict = {
            "id": self.id,
            "cmd": self.cmd,
            "pid": self.pid, # Include PID for both run() and launch()
            "start_ts": self.start_ts.isoformat() + 'Z',
            "end_ts": self.end_ts.isoformat() + 'Z' if self.end_ts else None,
            "exit_code": self.exit_code,
            "running": self.running,
            # Output details are primarily for run() commands, but might have partial data on timeout
            "total_lines": self.total_lines,
            "truncated": self.truncated,
        }
        return info_dict


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
        self._history = {}
        self._history_order = deque()
        self._history_limit = history_limit
        self._tail_keep = tail_keep
        self._next_id = 1
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
        """Adds a handle to history and trims if necessary."""
        if len(self._history) >= self._history_limit:
            oldest_id = self._history_order.popleft()
            if oldest_id in self._history:
                del self._history[oldest_id]
                self._logger.debug(f"Trimmed history, removed handle {oldest_id}")

        self._history[handle.id] = handle
        self._history_order.append(handle.id)

    def _kill_remote_process(self, pid, sudo=False):
        """Internal helper to attempt killing a remote PID. Avoids self.run()."""
        if not pid:
            return False
            
        self._logger.warning(f"Attempting to kill remote process PID {pid} (sudo={sudo}).")
        killed = False
        
        for signal in [15, 9]: # Try TERM then KILL
            cmd = f"kill -{signal} {pid}"
            full_cmd = f"sudo -n bash -c {shlex.quote(cmd)}" if sudo else cmd
            
            with self._client.get_transport().open_session() as chan:
                chan.settimeout(5.0)
                try:
                    chan.exec_command(full_cmd)
                    # Read stderr before checking exit status
                    stderr = chan.makefile_stderr('r', encoding='utf-8', errors='replace').read()
                    exit_status = chan.recv_exit_status()
                    
                    if exit_status == 0:
                        self._logger.info(f"Kill command (signal {signal}) for PID {pid} succeeded.")
                        killed = True
                        break
                    else:
                        self._logger.warning(f"Kill command failed with exit code {exit_status}. Stderr: {stderr.strip()}")
                except Exception as e:
                    self._logger.error(f"Error executing kill command: {e}", exc_info=True)
                    break

        return killed


    def run(self, cmd, io_timeout=60.0, runtime_timeout=None, sudo=False):
        """
        Execute a command synchronously, streaming output into a CommandHandle.
        This method BLOCKS until the command finishes, fails, or times out.
        Supports I/O inactivity timeout (io_timeout) and total runtime timeout (runtime_timeout).
        Returns the CommandHandle upon completion or raises CommandFailed, CommandTimeout, CommandRuntimeTimeout, SudoRequired.
        """
        return self.run_ops.execute_command(cmd, io_timeout, runtime_timeout, sudo)
    #     """
    #     Execute a command synchronously, streaming output into a CommandHandle.
    #     This method BLOCKS until the command finishes, fails, or times out.
    #     Supports I/O inactivity timeout (io_timeout) and total runtime timeout (runtime_timeout).
    #     Returns the CommandHandle upon completion or raises CommandFailed, CommandTimeout, CommandRuntimeTimeout, SudoRequired.
    #     """
    #     if not self._busy_lock.acquire(blocking=False):
    #         raise BusyError()
    #
    #     handle = None
    #     chan = None
    #     stdout = None
    #     stderr = None
    #     remote_pid = None
    #     start_time = time.monotonic()
    #     sudo_pwd_attempted = False
    #
    #     try:
    #         handle_id = self._next_id
    #         self._next_id += 1
    #         # Create handle early for potential timeout exceptions
    #         handle = CommandHandle(handle_id, cmd, tail_keep=self._tail_keep)
    #         self._add_to_history(handle) # Add to history immediately
    #
    #         # --- Sudo Handling ---
    #         use_sudo_password_flow = False
    #         if sudo:
    #             # Try passwordless sudo first
    #             self._logger.info(f"Attempting passwordless sudo for: {cmd}")
    #             # Use a simple command like 'whoami' for the check
    #             test_sudo_cmd = "sudo -n whoami"
    #             try:
    #                 # Use exec_command for a quick check, short timeout
    #                 stdin_t, stdout_t, stderr_t = self._client.exec_command(test_sudo_cmd, timeout=5)
    #                 sudo_n_stderr = stderr_t.read().decode('utf-8', errors='replace')
    #                 # Ensure exit status is read *after* stderr/stdout
    #                 sudo_n_exit_code = stdout_t.channel.recv_exit_status()
    #                 stdin_t.close(); stdout_t.close(); stderr_t.close()
    #
    #                 if sudo_n_exit_code == 0:
    #                     self._logger.info("Passwordless sudo successful.")
    #                     # Proceed using sudo -n in the main execution
    #                 elif sudo_n_exit_code == 1 and ("sudo:" in sudo_n_stderr or "password is required" in sudo_n_stderr.lower()):
    #                     self._logger.warning("Passwordless sudo failed. Checking for sudo password.")
    #                     if self.sudo_password:
    #                         self._logger.info("Sudo password provided. Will attempt interactive sudo.")
    #                         use_sudo_password_flow = True
    #                         sudo_pwd_attempted = True # Mark that we are using the password flow
    #                     else:
    #                         raise SudoRequired(cmd)
    #                 else:
    #                     # sudo -n failed for other reasons (e.g., command not found, permissions)
    #                     # This check might be too simple. What if 'whoami' is allowed but the actual command isn't?
    #                     # Reverting check to use the actual command with sudo -n
    #                     self._logger.info(f"Retrying sudo check with actual command prefix: {cmd[:50]}...")
    #                     test_sudo_cmd_orig = f"sudo -n bash -c {shlex.quote(cmd)}" # Check with original command
    #                     stdin_o, stdout_o, stderr_o = self._client.exec_command(test_sudo_cmd_orig, timeout=5)
    #                     sudo_n_stderr_o = stderr_o.read().decode('utf-8', errors='replace')
    #                     sudo_n_exit_code_o = stdout_o.channel.recv_exit_status()
    #                     stdin_o.close(); stdout_o.close(); stderr_o.close()
    #
    #                     if sudo_n_exit_code_o == 0:
    #                          self._logger.info("Passwordless sudo successful for the specific command.")
    #                     elif sudo_n_exit_code_o == 1 and ("sudo:" in sudo_n_stderr_o or "password is required" in sudo_n_stderr_o.lower()):
    #                          self._logger.warning("Passwordless sudo failed for the specific command. Checking for sudo password.")
    #                          if self.sudo_password:
    #                              self._logger.info("Sudo password provided. Will attempt interactive sudo.")
    #                              use_sudo_password_flow = True
    #                              sudo_pwd_attempted = True
    #                          else:
    #                              raise SudoRequired(cmd)
    #                     else:
    #                          # sudo -n failed for other reasons related to the actual command
    #                          raise CommandFailed(sudo_n_exit_code_o, "", sudo_n_stderr_o)
    #
    #             except Exception as sudo_check_err:
    #                  # Handle timeouts or other errors during the check
    #                  self._logger.error(f"Error during sudo check: {sudo_check_err}", exc_info=True)
    #                  raise SshError(f"Failed during sudo pre-check: {sudo_check_err}") from sudo_check_err
    #
    #         # --- Command Execution ---
    #         # Prepend command to get PID, handle sudo variations
    #         pid_capture_cmd = f"echo $$; exec {cmd}" # Basic command
    #         if use_sudo_password_flow:
    #             # Need PTY, use sudo -S -p ''
    #             full_cmd = f"sudo -S -p '' bash -c {shlex.quote(pid_capture_cmd)}"
    #         elif sudo: # Passwordless sudo worked
    #             full_cmd = f"sudo -n bash -c {shlex.quote(pid_capture_cmd)}"
    #         else: # No sudo
    #             full_cmd = f"bash -c {shlex.quote(pid_capture_cmd)}"
    #
    #         self._logger.info(f"Executing command (ID: {handle.id}): {full_cmd}")
    #         chan = self._client.get_transport().open_session()
    #
    #         if use_sudo_password_flow:
    #             self._logger.debug("Requesting PTY for sudo password.")
    #             chan.get_pty()
    #
    #         # Set I/O timeout for channel operations
    #         chan.settimeout(io_timeout)
    #
    #         chan.exec_command(full_cmd)
    #
    #         # Send sudo password if using that flow
    #         if use_sudo_password_flow:
    #             self._logger.debug("Sending sudo password.")
    #             try:
    #                 # Ensure channel is active before sending
    #                 if chan.active:
    #                     chan.sendall(self.sudo_password + "\n")
    #                 else:
    #                     raise SshError("Channel inactive before sending sudo password.")
    #             except Exception as send_err:
    #                 # Handle error sending password (e.g., channel closed)
    #                 self._logger.error(f"Failed to send sudo password: {send_err}", exc_info=True)
    #                 raise SshError(f"Failed to send sudo password: {send_err}") from send_err
    #
    #         # --- Output Reading Loop ---
    #         stdout = chan.makefile('r')
    #         stderr = chan.makefile_stderr('r')
    #         got_pid = False
    #
    #         while True:
    #             # 1. Check Runtime Timeout
    #             if runtime_timeout is not None:
    #                 elapsed = time.monotonic() - start_time
    #                 if elapsed > runtime_timeout:
    #                     self._logger.warning(f"Command ID {handle.id} (PID: {remote_pid}) exceeded runtime timeout of {runtime_timeout}s.")
    #                     handle.running = False # Mark as not running due to timeout
    #                     handle.end_ts = datetime.utcnow()
    #                     # Attempt to kill the process
    #                     self._kill_remote_process(remote_pid, sudo=sudo) # Use original sudo flag
    #                     raise CommandRuntimeTimeout(handle, runtime_timeout)
    #
    #             # 2. Check for I/O readiness (non-blocking)
    #             # Check exit status *before* blocking on select/readline
    #             if chan.exit_status_ready():
    #                 self._logger.debug(f"Command ID {handle.id} exit status ready, breaking read loop.")
    #                 break
    #
    #             read_ready, _, _ = select.select([chan], [], [], 0.1) # Wait up to 100ms
    #
    #             if chan in read_ready:
    #                 # 3. Read available stdout/stderr
    #                 try:
    #                     # Read stdout line by line (respects io_timeout)
    #                     line = stdout.readline()
    #                     if line:
    #                         if not got_pid:
    #                             # First line should be the PID
    #                             pid_str = line.strip()
    #                             if pid_str.isdigit():
    #                                 remote_pid = int(pid_str)
    #                                 handle.pid = remote_pid # Store PID in handle
    #                                 got_pid = True
    #                                 self._logger.info(f"Captured remote PID {remote_pid} for command ID {handle.id}")
    #                                 continue # Don't store PID line as output
    #                             else:
    #                                 # First line wasn't PID, log warning and treat as output
    #                                 self._logger.warning(f"Failed to capture PID. First line: '{pid_str}'")
    #                                 got_pid = True # Stop checking for PID
    #
    #                         # Store actual output
    #                         handle.total_lines += 1
    #                         if handle.total_lines > handle._buf.maxlen:
    #                             handle.truncated = True
    #                         handle._buf.append(line)
    #
    #                     # Check for stderr without blocking (less critical than stdout loop)
    #                     if chan.recv_stderr_ready():
    #                          stderr_line = stderr.readline()
    #                          if stderr_line:
    #                              # Log stderr or add to handle buffer? For now, log.
    #                              self._logger.warning(f"[STDERR] ID {handle.id}: {stderr_line.strip()}")
    #                              # Optionally append to handle._buf as well:
    #                              # handle._buf.append(f"[STDERR] {stderr_line}")
    #                              # handle.total_lines += 1 # If appending
    #
    #                     # If readline returned empty, check exit status again
    #                     if not line and chan.exit_status_ready():
    #                         self._logger.debug(f"Command ID {handle.id} readline empty and exit status ready.")
    #                         break # Command finished
    #
    #                 except socket.timeout:
    #                     # readline timed out waiting for data. Check if command finished.
    #                     if chan.exit_status_ready():
    #                         self._logger.debug(f"Command ID {handle.id} socket timeout but exit status ready.")
    #                         break # Command finished while we were waiting
    #                     else:
    #                         # I/O timeout occurred, but command still running and runtime timeout not hit.
    #                         # This indicates inactivity. Raise the specific I/O timeout exception.
    #                         self._logger.warning(f"Command ID {handle.id} hit I/O timeout ({io_timeout}s inactivity).")
    #                         raise CommandTimeout(io_timeout) from None
    #
    #             # 4. Check if command finished externally (if no I/O occurred in select)
    #             elif chan.exit_status_ready():
    #                 self._logger.debug(f"Command ID {handle.id} exit status ready after select timeout.")
    #                 break # Command finished
    #
    #             # 5. Small sleep if no I/O and not finished, prevent busy-wait
    #             # time.sleep(0.01) # Already handled by select timeout
    #
    #         # --- Command Finished ---
    #         # Ensure PID is captured if it was the very last thing printed before exit
    #         if not got_pid and remote_pid is None:
    #              # Check buffer in case PID was the only output
    #              buffered_lines = list(handle._buf)
    #              if len(buffered_lines) == 1 and buffered_lines[0].strip().isdigit():
    #                  remote_pid = int(buffered_lines[0].strip())
    #                  handle.pid = remote_pid
    #                  handle._buf.clear() # Remove PID from output buffer
    #                  handle.total_lines = 0
    #                  self._logger.info(f"Captured remote PID {remote_pid} from buffer for command ID {handle.id}")
    #              else:
    #                  self._logger.warning(f"Command ID {handle.id} finished without capturing PID.")
    #
    #
    #         handle.exit_code = chan.recv_exit_status()
    #         handle.end_ts = datetime.utcnow()
    #         handle.running = False
    #         self._logger.info(f"Command ID {handle.id} (PID: {remote_pid}) finished with exit code {handle.exit_code}.")
    #
    #         # Read any remaining stderr *after* command completion
    #         stderr_output = stderr.read().decode('utf-8', errors='replace')
    #         if stderr_output:
    #              self._logger.warning(f"[FINAL STDERR] ID {handle.id}: {stderr_output.strip()}")
    #
    #
    #         if handle.exit_code != 0:
    #             # Combine stdout buffer and final stderr for the exception
    #             stdout_all = ''.join(handle.tail(handle.total_lines))
    #             # If sudo password failed, provide a clearer error
    #             if sudo_pwd_attempted and handle.exit_code == 1 and ("incorrect password attempt" in stderr_output.lower() or "try again" in stderr_output.lower()):
    #                  raise SudoRequired(f"{cmd} (Incorrect sudo password provided or required)")
    #             raise CommandFailed(handle.exit_code, stdout_all, stderr_output)
    #
    #         return handle
    #
    #     except (CommandTimeout, CommandRuntimeTimeout, CommandFailed, SudoRequired, BusyError, SshError) as e:
    #          # Log known exceptions before re-raising
    #          if isinstance(e, CommandRuntimeTimeout):
    #              self._logger.error(f"Command ID {e.handle.id if e.handle else 'N/A'} failed: {e}", exc_info=False) # Already logged kill attempt
    #          elif isinstance(e, CommandFailed):
    #               # Ensure handle exists before accessing id
    #               handle_id_log = handle.id if handle else 'N/A'
    #               self._logger.error(f"Command ID {handle_id_log} failed with exit code {e.exit_code}. Stderr: {e.stderr}", exc_info=False)
    #          else:
    #              handle_id_log = handle.id if handle else 'N/A'
    #              self._logger.error(f"Command ID {handle_id_log} encountered error: {e}", exc_info=False)
    #
    #          # Ensure handle state is updated on error if possible
    #          if handle and handle.running:
    #              handle.running = False
    #              handle.end_ts = datetime.utcnow()
    #          raise # Re-raise the caught exception
    #     except Exception as e:
    #         # Catch unexpected errors
    #         handle_id_log = handle.id if handle else 'N/A'
    #         self._logger.error(f"Unexpected error during run (ID: {handle_id_log}): {e}", exc_info=True)
    #         if handle and handle.running:
    #              handle.running = False
    #              handle.end_ts = datetime.utcnow()
    #         raise SshError(f"Unexpected error during command execution: {e}") from e
    #     finally:
    #         # Cleanup: Close streams and channel
    #         if stdout: stdout.close()
    #         if stderr: stderr.close()
    #         if chan: chan.close()
    #         # Release the lock
    #         self._busy_lock.release()
    #         self._logger.debug(f"Released busy lock for command ID {handle.id if handle else 'N/A'}.")


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
    #     """
    #     Send a signal to a process with the given PID on the remote host.
    #     Uses self.run() internally, so it respects the busy lock and handles sudo.
    #     Tries the specified signal, waits, checks status, then tries force_kill_signal (default SIGKILL) if needed.
    #     Returns:
    #         'killed': Process was successfully terminated (by signal or force_kill_signal).
    #         'already_exited': Process was already gone before signaling.
    #         'failed_to_kill': Signaling attempts failed or process remained running.
    #         'error': An error occurred during the kill attempt.
    #     """
    #     if not isinstance(pid, int) or pid <= 0:
    #         raise ValueError("Invalid PID provided.")
    #     if not isinstance(signal, int):
    #         raise ValueError("Signal must be an integer.")
    #     if force_kill_signal is not None and not isinstance(force_kill_signal, int):
    #         raise ValueError("force_kill_signal must be an integer or None.")
    #
    #     self._logger.info(f"Attempting to kill PID {pid} with signal {signal} (sudo={sudo}). Fallback signal: {force_kill_signal}.")
    #
    #     # 1. Check initial status
    #     initial_status = self.task_status(pid)
    #     if initial_status == "exited":
    #         self._logger.info(f"PID {pid} was already exited before sending signal.")
    #         return "already_exited"
    #     if initial_status == "error":
    #         self._logger.warning(f"Could not determine initial status for PID {pid}. Proceeding with kill attempt.")
    #         # Continue, maybe kill will work anyway
    #
    #     # 2. Try initial signal
    #     cmd = f"kill -{signal} {pid}"
    #     kill_cmd_succeeded = False
    #     try:
    #         # Use run() for the kill command itself, as it handles sudo and errors
    #         handle = self.run(cmd, io_timeout=10, runtime_timeout=15, sudo=sudo)
    #         if handle.exit_code == 0:
    #             self._logger.info(f"Successfully sent signal {signal} to PID {pid}.")
    #             kill_cmd_succeeded = True
    #         else:
    #             # kill command failed, but maybe process died anyway? Or permissions?
    #              self._logger.warning(f"Command 'kill -{signal} {pid}' failed with exit code {handle.exit_code}. Checking status.")
    #              # Proceed to status check
    #
    #     except CommandFailed as e:
    #         # kill returns non-zero if process doesn't exist or permission denied
    #         self._logger.warning(f"Command 'kill -{signal} {pid}' failed: {e}. Process might be gone or permissions insufficient.")
    #         # Check status to be sure
    #     except BusyError:
    #          self._logger.error("Cannot execute task_kill: client is busy with another run() command.")
    #          raise # Re-raise busy error
    #     except (CommandTimeout, CommandRuntimeTimeout) as e:
    #          self._logger.error(f"Timeout executing kill command 'kill -{signal} {pid}': {e}")
    #          return "error" # Error during the kill command itself
    #     except Exception as e:
    #         self._logger.error(f"Error sending signal {signal} to PID {pid}: {e}", exc_info=True)
    #         return "error" # Error during the kill command itself
    #
    #     # 3. Wait and Check Status (only if kill command didn't obviously fail due to non-existence)
    #     # If kill_cmd_succeeded is False, it might be because the process was already gone.
    #     if wait_seconds > 0:
    #         self._logger.debug(f"Waiting {wait_seconds}s after signal {signal} attempt...")
    #         time.sleep(wait_seconds)
    #
    #     current_status = self.task_status(pid)
    #     if current_status == "exited":
    #         self._logger.info(f"PID {pid} confirmed exited after signal {signal} attempt.")
    #         return "killed"
    #     if current_status == "error":
    #         self._logger.warning(f"Could not determine status for PID {pid} after signal {signal}. Assuming it might still be running.")
    #         # Proceed to force kill if configured
    #
    #     # 4. Try Force Kill Signal (if needed and configured)
    #     if force_kill_signal is not None and current_status == "running":
    #         self._logger.warning(f"PID {pid} still running after signal {signal}. Attempting force kill with signal {force_kill_signal}.")
    #         cmd_force = f"kill -{force_kill_signal} {pid}"
    #         try:
    #             handle_force = self.run(cmd_force, io_timeout=10, runtime_timeout=15, sudo=sudo)
    #             if handle_force.exit_code == 0:
    #                 self._logger.info(f"Successfully sent force signal {force_kill_signal} to PID {pid}.")
    #                 # Check status one last time after short delay
    #                 time.sleep(0.5)
    #                 final_status = self.task_status(pid)
    #                 if final_status == "exited":
    #                     self._logger.info(f"PID {pid} confirmed exited after force signal {force_kill_signal}.")
    #                     return "killed"
    #                 else:
    #                      self._logger.error(f"PID {pid} still not exited after force signal {force_kill_signal} (status: {final_status}).")
    #                      return "failed_to_kill"
    #             else:
    #                 self._logger.error(f"Force kill command 'kill -{force_kill_signal} {pid}' failed with exit code {handle_force.exit_code}.")
    #                 # Check status again - maybe it died just before?
    #                 if self.task_status(pid) == "exited": return "killed"
    #                 return "failed_to_kill"
    #         except CommandFailed as e_force:
    #              self._logger.error(f"Force kill command 'kill -{force_kill_signal} {pid}' failed: {e_force}.")
    #              # Check status - maybe it died just before force kill?
    #              if self.task_status(pid) == "exited": return "killed"
    #              return "failed_to_kill"
    #         except BusyError:
    #              self._logger.error("Cannot execute force kill: client is busy with another run() command.")
    #              raise # Re-raise busy error
    #         except (CommandTimeout, CommandRuntimeTimeout) as e_force:
    #              self._logger.error(f"Timeout executing force kill command 'kill -{force_kill_signal} {pid}': {e_force}")
    #              return "error" # Error during the force kill command itself
    #         except Exception as e_force:
    #             self._logger.error(f"Error sending force signal {force_kill_signal} to PID {pid}: {e_force}", exc_info=True)
    #             return "error"
    #     elif current_status == "running":
    #          # Still running, but no force kill configured or attempted
    #          self._logger.warning(f"PID {pid} still running after signal {signal}, no force kill attempted.")
    #          return "failed_to_kill"
    #
    #     # Should not be reached if logic is correct, but as fallback:
    #     return "failed_to_kill"


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
    #     """Internal helper for sudo-based file modification."""
    #     local_temp_fd, local_temp_path = tempfile.mkstemp(text=True)
    #     os.close(local_temp_fd)
    #     self._logger.debug(f"Created local temp file: {local_temp_path}")
    #     original_text = None # Initialize
    #
    #     try:
    #         # 1. Download original file (best effort, might fail if no read permission)
    #         try:
    #              self.get(remote_file, local_temp_path)
    #              with open(local_temp_path, 'r', encoding='utf-8', errors='replace') as f:
    #                  original_text = f.read()
    #              self._logger.debug(f"Successfully downloaded original file {remote_file}")
    #         except Exception as e:
    #              self._logger.warning(f"Could not download original {remote_file}: {e}. Checking force flag.")
    #              if not force:
    #                  raise SshError(f"Cannot read original file {remote_file} and force=False. Aborting replacement.") from e
    #              else:
    #                  self._logger.warning("force=True specified. Proceeding with modification assuming empty or irrelevant original content.")
    #                  original_text = "" # Proceed with empty content if forced
    #
    #         # 2. Modify content
    #         modified_text = modify_func(original_text)
    #
    #         # 3. Check if content actually changed (important!)
    #         if modified_text == original_text:
    #              self._logger.info(f"Content for {remote_file} not modified, skipping sudo replacement.")
    #              return # Exit early, no need to upload or move
    #
    #         # 4. Write modified content to local temp
    #         self._logger.info(f"Content modified for {remote_file}. Proceeding with sudo replacement.")
    #         with open(local_temp_path, 'w', encoding='utf-8') as f:
    #             f.write(modified_text)
    #
    #         # 5. Upload modified content to REMOTE temp path
    #         self.put(local_temp_path, remote_temp_path)
    #
    #         # 6. Use `sudo mv` to replace the original file atomically
    #         #    Also copy permissions and ownership from original if possible
    #         perms = owner = group = None
    #         if original_text is not None: # Only try stat if we could potentially read the original
    #             stat_cmd = f"stat -c '%a %u %g' {shlex.quote(remote_file)}"
    #             try:
    #                 # Use run() for stat, ensure sudo is False for stat command itself
    #                 stat_handle = self.run(stat_cmd, io_timeout=10, sudo=False)
    #                 stat_output = stat_handle.tail(1)[0].strip()
    #                 parts = stat_output.split()
    #                 if len(parts) == 3:
    #                     perms, owner, group = parts
    #                     self._logger.debug(f"Got permissions for {remote_file}: {perms} {owner}:{group}")
    #                 else:
    #                     self._logger.warning(f"Unexpected output from stat command: '{stat_output}'. Cannot restore permissions.")
    #             except Exception as stat_err:
    #                 self._logger.warning(f"Could not get permissions/owner for {remote_file}: {stat_err}. Using defaults.")
    #
    #         # Build the move and permission commands
    #         mv_cmd = f"mv {shlex.quote(remote_temp_path)} {shlex.quote(remote_file)}"
    #         chown_cmd = f"chown {owner}:{group} {shlex.quote(remote_file)}" if owner and group else None
    #         chmod_cmd = f"chmod {perms} {shlex.quote(remote_file)}" if perms else None
    #
    #         # Execute commands with sudo (original sudo flag for the replace operation)
    #         self._logger.info(f"Executing sudo mv: {mv_cmd}")
    #         self.run(mv_cmd, sudo=True) # run() handles potential sudo errors
    #         if chown_cmd:
    #             try:
    #                 self._logger.info(f"Executing sudo chown: {chown_cmd}")
    #                 self.run(chown_cmd, sudo=True)
    #             except Exception as chown_err:
    #                 self._logger.warning(f"Failed to sudo chown {remote_file}: {chown_err}")
    #         if chmod_cmd:
    #              try:
    #                  self._logger.info(f"Executing sudo chmod: {chmod_cmd}")
    #                  self.run(chmod_cmd, sudo=True)
    #              except Exception as chmod_err:
    #                  self._logger.warning(f"Failed to sudo chmod {remote_file}: {chmod_err}")
    #
    #         self._logger.info(f"Successfully replaced {remote_file} using sudo.")
    #
    #     finally:
    #         # 7. Cleanup local and remote temp files
    #         if os.path.exists(local_temp_path):
    #             self._logger.debug(f"Cleaning up local temp file: {local_temp_path}")
    #             os.unlink(local_temp_path)
    #         # Try removing remote temp file, ignore errors, use run()
    #         try:
    #             self._logger.debug(f"Cleaning up remote temp file: {remote_temp_path}")
    #             # Use run with short timeout, ignore BusyError. Don't use sudo for /tmp cleanup.
    #             self.run(f"rm -f {shlex.quote(remote_temp_path)}", io_timeout=10, runtime_timeout=15, sudo=False)
    #         except BusyError:
    #              self._logger.warning(f"Client busy, could not cleanup remote temp file {remote_temp_path}")
    #         except Exception as cleanup_err:
    #              self._logger.warning(f"Failed to cleanup remote temp file {remote_temp_path}: {cleanup_err}")

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
    #     """Reboot the remote host and optionally wait until it comes back."""
    #     self._logger.warning("Attempting reboot...")
    #     reboot_cmd_sent = False
    #     try:
    #         # Send reboot command, don't wait for output as connection will drop
    #         # Use a short runtime_timeout for the reboot command itself
    #         self.run('reboot', sudo=True, runtime_timeout=10)
    #         reboot_cmd_sent = True
    #         self._logger.info("Reboot command executed successfully (connection likely dropping).")
    #     except CommandFailed as e:
    #         # Handle cases where reboot command itself fails immediately
    #         self._logger.error(f"Reboot command failed: {e}", exc_info=True)
    #         return # Do not proceed with wait/close if command failed
    #     except CommandRuntimeTimeout:
    #          # Reboot command itself timed out - unusual, but assume it might be proceeding
    #          self._logger.warning("Reboot command timed out, assuming reboot is proceeding.")
    #          reboot_cmd_sent = True
    #     except SshError as e:
    #          # Catch potential connection errors during the run call itself
    #          self._logger.warning(f"SSH error during reboot command: {e}. Assuming connection lost and reboot proceeding.")
    #          reboot_cmd_sent = True # Assume it might have worked
    #     except Exception as e:
    #          self._logger.error(f"Unexpected error sending reboot command: {e}", exc_info=True)
    #          # Don't proceed if we couldn't even send the command
    #          return
    #     finally:
    #         # Always close the connection after sending reboot attempt
    #          self._logger.info("Closing connection post-reboot command attempt.")
    #          self.close()
    #
    #     # Only proceed with waiting if command was likely sent
    #     if not reboot_cmd_sent:
    #          return
    #
    #     start = time.time()
    #     if not wait:
    #         self._logger.info("Reboot initiated, not waiting for reconnect.")
    #         return
    #
    #     self._logger.info(f"Waiting up to {timeout} seconds for host {self.host} to come back online...")
    #     while True:
    #         elapsed = time.time() - start
    #         if elapsed > timeout:
    #             self._logger.error(f"Host did not come back online within {timeout} seconds.")
    #             raise CommandTimeout(timeout) # Re-use CommandTimeout for this wait failure
    #         try:
    #             self._logger.info(f"Attempting to reconnect ({int(elapsed)}s elapsed)...")
    #             # Create a fresh client instance for reconnect attempt
    #             # Re-initialize self instead of creating a new instance? Risky state.
    #             # Let's stick to requiring the caller to create a new instance after reboot.
    #             # This method should just wait and confirm reachability.
    #             # Re-use internal _connect logic on a new client object.
    #             temp_client = paramiko.SSHClient()
    #             temp_client.load_system_host_keys()
    #             temp_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    #             kwargs = dict(hostname=self.host, port=self.port, username=self.user, timeout=5) # Short timeout for check
    #             if self.keyfile: kwargs['key_filename'] = self.keyfile
    #             if self.password: kwargs['password'] = self.password
    #             temp_client.connect(**kwargs)
    #             temp_client.close() # Close immediately after successful connect
    #
    #             # Re-establish connection for the current instance
    #             self._logger.info("Reconnect successful. Re-establishing client state.")
    #             self._connect()
    #             return # Host is back
    #
    #         except Exception as e:
    #             # Expected connection errors while host is down
    #             self._logger.debug(f"Reconnect attempt failed: {e}")
    #             time.sleep(5) # Wait before retrying


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
        """Return metadata for recent CommandHandles, respecting history order."""
        return [self._history[handle_id].info() for handle_id in self._history_order if handle_id in self._history]

    # _build_cmd helper removed as logic is inlined or handled directly
