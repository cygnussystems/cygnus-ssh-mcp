import time
import base64
import logging
import shlex
from abc import ABC, abstractmethod
from typing import Optional
from datetime import datetime, UTC
from cygnus_ssh_mcp.models import (
    CommandHandle, SshError, TaskNotFound, SudoRequired
)
from cygnus_ssh_mcp.ps_encode import powershell_encoded_command


class SshTaskOperations(ABC):
    """Base class for background task management. Platform-specific commands are abstract."""

    def __init__(self, ssh_client):
        """
        Args:
            ssh_client: Reference to parent SSH client
        """
        self.ssh_client = ssh_client
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    # ==========================================================================
    # Abstract methods - implemented by platform-specific subclasses
    # ==========================================================================

    @abstractmethod
    def _get_default_log_dir(self) -> str:
        """Return the default directory for task log files."""
        pass

    @abstractmethod
    def _build_launch_script(self, cmd: str, stdout_log: str, stderr_log: str, sudo: bool) -> tuple:
        """
        Build the script content and path to launch a background task.

        Args:
            cmd: Command to execute
            stdout_log: Path to redirect stdout
            stderr_log: Path to redirect stderr
            sudo: Whether to run with elevated privileges

        Returns:
            Tuple of (script_path, script_content, create_script_command)
        """
        pass

    @abstractmethod
    def _cmd_check_process_running(self, pid: int) -> str:
        """
        Return command to check if a process is running.
        Command should exit 0 if running, non-zero if not.
        """
        pass

    @abstractmethod
    def _cmd_kill_process(self, pid: int, signal: int, sudo: bool, use_process_group: bool = False) -> str:
        """
        Return command to kill a process with the given signal.

        Args:
            pid: Process ID
            signal: Signal number (15=TERM, 9=KILL on Linux)
            sudo: Whether to inline-wrap this kill command itself in `sudo -n` -
                only meaningful for callers executing it via a raw channel
                (`_kill_remote_process`); `kill_task` always passes False here and
                elevates one layer up instead, via `ssh_client.run(sudo=...)`.
            use_process_group: Whether `pid` may be the outermost process of a
                multi-process sudo chain (see `_build_launch_script`'s sudo
                branch) rather than the real target itself - if True, kill the
                whole process group instead of just `pid` (Linux/macOS only;
                Windows already handles this via `taskkill /T`, see below).
        """
        pass

    @abstractmethod
    def _cmd_rename_log(self, old_path: str, new_path: str, pid: int) -> str:
        """Return command to rename a log file, once the task (pid) no longer needs it."""
        pass

    def _merged_stderr_path(self, stdout_log: str) -> str:
        """Where stderr actually lands when it's asked to share stdout_log's file.
        POSIX merges both into the one file (2>&1); Windows overrides this."""
        return stdout_log

    # ==========================================================================
    # Shared implementation methods
    # ==========================================================================

    def launch_task(self, cmd, stdout_log=None, stderr_log=None, log_output=True, sudo=False, add_to_history=False):
        """
        Launch a command in the background and return a CommandHandle with the PID.

        Args:
            cmd: Command to execute
            stdout_log: Path to redirect stdout
            stderr_log: Path to redirect stderr
            log_output: Whether to enable default logging
            sudo: Whether to run with sudo

        Returns:
            CommandHandle with task PID

        Raises:
            SshError: If task launch fails
            SudoRequired: If sudo password is required but not provided
        """
        try:
            # Determine log paths
            effective_stdout_log = stdout_log
            effective_stderr_log = stderr_log
            pid_placeholder = f"pid_{int(time.time())}"
            log_dir = self._get_default_log_dir()
            default_log_path = f"{log_dir}/task-{pid_placeholder}.log"

            if log_output and stdout_log is None and stderr_log is None:
                effective_stdout_log = default_log_path
                effective_stderr_log = default_log_path
                self.logger.info(f"Defaulting log output to {default_log_path} (placeholder)")
            elif log_output and stdout_log is None:
                effective_stdout_log = f"{log_dir}/null" if log_dir.startswith("C:") else "/dev/null"
            elif log_output and stderr_log is None:
                # Documented default: stderr goes wherever stdout goes
                effective_stderr_log = stdout_log

            # Build platform-specific launch script
            script_path, script_content, create_script_cmd = self._build_launch_script(
                cmd, effective_stdout_log, effective_stderr_log, sudo
            )

            # Create the script
            stdin, stdout, stderr = self.ssh_client._client.exec_command(create_script_cmd, timeout=5)
            exit_status = stdout.channel.recv_exit_status()
            if exit_status != 0:
                err_msg = f"Failed to create launch script: {stderr.read().decode('utf-8', errors='replace')}"
                self.logger.error(err_msg)
                raise SshError(err_msg)

            # Execute the script
            self.logger.info(f"Launching background task using script: {script_path}")
            stdin, stdout, stderr = self.ssh_client._client.exec_command(script_path, timeout=10)

            # Read all output
            stdout_data = stdout.read().decode('utf-8', errors='replace').strip()
            stderr_output = stderr.read().decode('utf-8', errors='replace').strip()
            exit_status = stdout.channel.recv_exit_status()

            stdin.close()
            stdout.close()
            stderr.close()

            if exit_status != 0:
                err_msg = f"Task launch failed with exit code {exit_status}. stdout: '{stdout_data}', stderr: '{stderr_output}'"
                self.logger.error(err_msg)
                if sudo and "password is required" in stderr_output:
                    raise SudoRequired(cmd)
                # Markers from the POSIX launcher's pre-launch checks - nothing was started
                for line in stderr_output.splitlines():
                    if line.startswith("LOG_NOT_WRITABLE:"):
                        raise SshError(
                            f"Task NOT launched: can't create log file '{line.split(':', 1)[1]}' "
                            f"(directory missing or not writable"
                            f"{', even with sudo' if sudo else '; use_sudo=True may help'}). "
                            f"Choose another stdout_log/stderr_log path."
                        )
                    if line.startswith("SUDO_FAILED:"):
                        raise SshError(
                            f"Task NOT launched: sudo failed: {line.split(':', 1)[1].strip() or 'unknown error'}. "
                            f"Check with ssh_conn_verify_sudo."
                        )
                raise SshError(err_msg)

            # Extract PID from the "PID:12345" format
            pid_match = None
            for line in stdout_data.splitlines():
                if line.startswith("PID:"):
                    pid_match = line[4:].strip()
                    break

            if not pid_match or not pid_match.isdigit():
                err_msg = f"Failed to parse PID from task launch. stdout: '{stdout_data}', stderr: '{stderr_output}'"
                self.logger.error(err_msg)
                raise SshError(err_msg)

            pid = int(pid_match)
            self.logger.info(f"Task launched successfully with PID: {pid}")

            # Rename default log file if used
            if effective_stdout_log == default_log_path:
                final_log_path = f"{log_dir}/task-{pid}.log"
                try:
                    rename_cmd = self._cmd_rename_log(default_log_path, final_log_path, pid)
                    stdin, stdout, stderr = self.ssh_client._client.exec_command(rename_cmd, timeout=10)
                    exit_status = stdout.channel.recv_exit_status()
                    if exit_status == 0:
                        self.logger.info(f"Requested rename of default log to {final_log_path}")
                    else:
                        stderr_output = stderr.read().decode('utf-8', errors='replace')
                        self.logger.warning(f"Failed to request rename of default log file: exit code {exit_status}, stderr: {stderr_output}")
                except Exception as rename_err:
                    self.logger.warning(f"Failed to request rename of default log file: {rename_err}")

            # Create a handle for the task
            if add_to_history:
                handle = self.ssh_client.history_manager.add_command(cmd, pid)
            else:
                handle = CommandHandle(self.ssh_client.history_manager._next_id, cmd)
                handle.pid = pid
                handle.start_ts = datetime.now(UTC)
            handle.stdout_log, handle.stderr_log = self._actual_log_paths(
                effective_stdout_log, effective_stderr_log, default_log_path, f"{log_dir}/task-{pid}.log"
            ) if log_output else (None, None)
            return handle

        except Exception as e:
            self.logger.error(f"Failed to launch task: {e}", exc_info=True)
            if isinstance(e, SudoRequired):
                raise
            raise SshError(f"Failed to launch task: {e}") from e

    def _actual_log_paths(self, stdout_log, stderr_log, placeholder_path, final_path):
        """Return (stdout_path, stderr_path) as they will really exist on the remote host -
        None for a stream that's discarded - so callers never get a path that isn't there."""
        def real(path):
            if path == placeholder_path:
                return final_path  # renamed to the pid-based name after launch
            if path is None or path == "/dev/null" or path.endswith("/null"):
                return None
            return path

        stdout_path = real(stdout_log)
        if stderr_log is not None and stderr_log == stdout_log and stdout_path:
            stderr_path = self._merged_stderr_path(stdout_path)
        else:
            stderr_path = real(stderr_log)
        return stdout_path, stderr_path

    def get_task_status(self, pid):
        """
        Check the status of a background task.

        Args:
            pid: Process ID to check

        Returns:
            'running', 'exited', 'invalid', or 'error'
        """
        if not isinstance(pid, int) or pid <= 0:
            self.logger.warning(f"Invalid PID provided: {pid}")
            return "invalid"

        cmd = self._cmd_check_process_running(pid)
        self.logger.debug(f"Checking status for PID {pid} using command: {cmd}")
        self.last_status_error = None
        # Two attempts: a busy host (e.g. Windows creating thousands of files) can
        # miss the short per-check timeout once without anything being wrong.
        for attempt in (1, 2):
            chan = None
            try:
                chan = self.ssh_client._client.get_transport().open_session()
                chan.settimeout(10.0)
                chan.exec_command(cmd)
                stderr_output = chan.makefile_stderr('r').read().decode('utf-8', errors='replace')
                exit_status = chan.recv_exit_status()
                chan.close()

                if exit_status == 0:
                    self.logger.debug(f"Status check for PID {pid}: running")
                    return "running"
                else:
                    self.logger.debug(f"Status check for PID {pid}: exited (exit code {exit_status})")
                    return "exited"

            except Exception as e:
                self.logger.warning(f"Error checking status for PID {pid} (attempt {attempt}): {e!r}")
                if chan and not chan.closed:
                    chan.close()
                self.last_status_error = (
                    "the status check timed out (the host may be very busy)"
                    if "timeout" in type(e).__name__.lower() or "timed out" in str(e).lower()
                    else f"the status check failed: {e!r}"
                )
        return "error"

    def _kill_remote_process(self, pid, sudo=False):
        """Internal helper to attempt killing a remote PID."""
        if not pid:
            return False

        self.logger.warning(f"Attempting to kill remote process PID {pid} (sudo={sudo}).")
        killed = False

        for signal in [15, 9]:  # Try TERM then KILL
            # sudo'd commands may have a multi-process chain under the captured
            # pid (see _build_launch_script's sudo branch) - use_process_group
            # reaches the real target, not just the outermost wrapper.
            cmd = self._cmd_kill_process(pid, signal, sudo, use_process_group=sudo)

            with self.ssh_client._client.get_transport().open_session() as chan:
                chan.settimeout(5.0)
                try:
                    chan.exec_command(cmd)
                    stderr_file = chan.makefile_stderr('r')
                    stderr = stderr_file.read()
                    if isinstance(stderr, bytes):
                        stderr = stderr.decode('utf-8', errors='replace')
                    stderr_file.close()
                    exit_status = chan.recv_exit_status()

                    if exit_status == 0:
                        self.logger.info(f"Kill command (signal {signal}) for PID {pid} succeeded.")
                        killed = True
                        break
                    else:
                        self.logger.warning(f"Kill command failed with exit code {exit_status}. Stderr: {stderr.strip()}")
                except Exception as e:
                    self.logger.error(f"Error executing kill command: {e}", exc_info=True)
                    break

        return killed

    def kill_task(self, pid, signal=15, sudo=False, force_kill_signal=9, wait_seconds=1.0):
        """
        Kill a background task.

        Args:
            pid: Process ID to kill
            signal: Initial signal to send (default: 15/SIGTERM)
            sudo: Whether to use sudo
            force_kill_signal: Fallback signal if initial fails (default: 9/SIGKILL)
            wait_seconds: Time to wait after initial signal before checking status

        Returns:
            Tuple of (status, force_kill_used):
            - status: 'killed', 'already_exited', 'failed_to_kill', 'invalid_pid', or 'error'
            - force_kill_used: True iff the force_kill_signal fallback was actually
              attempted (regardless of whether it succeeded) - i.e. the initial
              signal alone was NOT enough. False if the initial signal succeeded on
              its own, the process was already gone, the PID was invalid, or no
              force_kill_signal was configured to try.
        """
        if not isinstance(pid, int) or pid <= 0:
            self.logger.warning(f"Invalid PID provided: {pid}")
            return "invalid_pid", False
        if not isinstance(signal, int):
            raise ValueError("Signal must be an integer.")
        if force_kill_signal is not None and not isinstance(force_kill_signal, int):
            raise ValueError("force_kill_signal must be an integer or None.")

        self.logger.info(f"Attempting to kill PID {pid} with signal {signal} (sudo={sudo}). Fallback signal: {force_kill_signal}")

        # Check initial status
        initial_status = self.get_task_status(pid)
        if initial_status == "exited":
            self.logger.info(f"PID {pid} was already exited before sending signal.")
            return "already_exited", False
        if initial_status == "error":
            self.logger.warning(f"Could not determine initial status for PID {pid}. Proceeding with kill attempt.")

        # Try initial signal
        # Use base kill command and let ssh_client.run() handle sudo (supports
        # password-based sudo) - use_process_group=sudo since a sudo'd task's
        # captured pid may be an outer wrapper, not the real target (see
        # _build_launch_script's sudo branch / planning/2026-07-05-sudo-kill-scope.md)
        cmd = self._cmd_kill_process(pid, signal, sudo=False, use_process_group=sudo)
        kill_cmd_succeeded = False
        try:
            handle = self.ssh_client.run(cmd, io_timeout=10, runtime_timeout=15, sudo=sudo)
            if handle.exit_code == 0:
                self.logger.info(f"Successfully sent signal {signal} to PID {pid}.")
                kill_cmd_succeeded = True
            else:
                self.logger.warning(f"Command 'kill' for PID {pid} failed with exit code {handle.exit_code}.")
        except Exception as e:
            self.logger.warning(f"Error sending signal {signal} to PID {pid}: {e}")

        # Wait and check status
        if wait_seconds > 0:
            self.logger.debug(f"Waiting {wait_seconds}s after signal {signal} attempt...")
            time.sleep(wait_seconds)

        current_status = self.get_task_status(pid)
        if current_status == "exited":
            self.logger.info(f"PID {pid} confirmed exited after signal {signal} attempt.")
            return "killed", False
        if current_status == "error":
            self.logger.warning(f"Could not determine status for PID {pid} after signal {signal}.")

        # Try force kill if needed
        if force_kill_signal is not None and current_status == "running":
            self.logger.warning(f"PID {pid} still running after signal {signal}. Attempting force kill with signal {force_kill_signal}.")
            cmd_force = self._cmd_kill_process(pid, force_kill_signal, sudo=False, use_process_group=sudo)
            try:
                handle_force = self.ssh_client.run(cmd_force, io_timeout=10, runtime_timeout=15, sudo=sudo)
                if handle_force.exit_code == 0:
                    self.logger.info(f"Successfully sent force signal {force_kill_signal} to PID {pid}.")
                    time.sleep(0.5)
                    final_status = self.get_task_status(pid)
                    if final_status == "exited":
                        self.logger.info(f"PID {pid} confirmed exited after force signal {force_kill_signal}.")
                        return "killed", True
                    else:
                        self.logger.error(f"PID {pid} still not exited after force signal {force_kill_signal}.")
                        return "failed_to_kill", True
                else:
                    self.logger.error(f"Force kill command for PID {pid} failed with exit code {handle_force.exit_code}.")
                    return "failed_to_kill", True
            except Exception as e_force:
                self.logger.error(f"Error sending force signal {force_kill_signal} to PID {pid}: {e_force}")
                return "error", True
        elif current_status == "running":
            self.logger.warning(f"PID {pid} still running after signal {signal}, no force kill attempted.")
            return "failed_to_kill", False

        return "failed_to_kill", False


class SshTaskOperations_Linux(SshTaskOperations):
    """Linux implementation using bash scripts and kill signals."""

    def _get_default_log_dir(self) -> str:
        return "/tmp"

    def _build_launch_script(self, cmd: str, stdout_log: str, stderr_log: str, sudo: bool) -> tuple:
        """Build a shell script to launch background task.

        Live-verified against Alpine (BusyBox, no bash) and FreeBSD (bash not
        installed by default): the script used to hardcode `#!/bin/bash` and
        run itself via a bare path, relying on the kernel's shebang-exec
        mechanism - which fails with a confusing "not found" (not "no such
        interpreter") when /bin/bash doesn't exist. Now invoked explicitly via
        `sh {script_path}` (see the returned tuple's first element - the
        actual command to run, not just the path - matching the Windows
        override, which already returns its full execution command there
        rather than a bare path) - `sh` is required by both 'linux' and
        'flex' dispatch's own capability probe, so it's always trustworthy.
        The wrapper script's OWN commands (nohup, pid=$!, echo, rm -f) are
        already POSIX/sh-compatible - only the innermost invocation of the
        user's actual `cmd` needs to preserve bash's fuller feature set where
        available, so that alone still prefers bash when confirmed present
        (or unconfirmed, e.g. real macOS/Windows never probe capabilities at
        all - defaults preserve today's exact behavior there).
        """
        timestamp = int(time.time())
        script_path = f"/tmp/launch_script_{timestamp}.sh"
        # Only the innermost invocation of the user's own `cmd` needs bash's
        # fuller feature set - default True (assume present) when unconfirmed,
        # so real Linux/macOS behave exactly as before.
        cmd_shell = 'bash' if self.ssh_client.capabilities.get('bash', True) else 'sh'

        out_path = stdout_log or "/dev/null"
        err_path = stderr_log or ("/dev/null" if not stdout_log else stdout_log)
        merged = err_path == out_path and out_path != "/dev/null"
        user_redirect = '1>"$__OUT" ' + ('2>&1' if merged else '2>"$__ERR"')

        # Log files and the command are passed as env vars / positional args, never
        # spliced into nested quotes. Before launching anything, each log is created
        # (": >>" - no truncation) as the user if possible. Only if the user can't
        # (e.g. a root-owned directory) and use_sudo is set is it created via sudo -
        # and then the task opens its logs as root (__ROOT_LOGS=1). Otherwise logs are
        # opened by the user's shell exactly as before: a default /tmp log must stay
        # user-owned and be opened immediately, since it's renamed to task-<pid>.log
        # right after launch (/tmp is sticky, and a slow sudo startup would otherwise
        # race the rename). If a log can't be created at all, or sudo itself fails,
        # the launcher exits non-zero with a marker instead of reporting the PID of a
        # job that never started.
        header = f"""#!/bin/sh
export __TASK_CMD={shlex.quote(cmd)}
export __OUT={shlex.quote(out_path)}
export __ERR={shlex.quote(err_path)}
"""
        ensure_logs = """__ROOT_LOGS=0
__ensure_log() {
  [ "$1" = /dev/null ] && return 0
  ( : >> "$1" ) 2>/dev/null && return 0
  __sudo_touch "$1" && { __ROOT_LOGS=1; return 0; }
  echo "LOG_NOT_WRITABLE:$1" >&2
  exit 3
}
__ensure_log "$__OUT"
[ "$__ERR" = "$__OUT" ] || __ensure_log "$__ERR"
"""
        footer = f"""pid=$!
echo "PID:$pid"
rm -f {script_path}
exit 0
"""

        if sudo:
            # Root-side program for __ROOT_LOGS=1: open the logs as root, then exec the
            # user's command (exec keeps the process chain depth kill_task's
            # process-group handling relies on).
            root_prog = 'exec 1>"$1" ' + ('2>&1' if merged else '2>"$2"') + f'; exec {cmd_shell} -c "$3"'
            header += f"export __ROOT_PROG={shlex.quote(root_prog)}\n"
            sudo_password = getattr(self.ssh_client, 'sudo_password', None)
            if sudo_password:
                # Password via env var + printf, not an echo argument visible in ps.
                # The outer 'sh -c' is just plumbing (pipe + sudo) - always sh.
                sudo_prefix = 'printf "%s\\n" "$__SUDO_PW" | sudo -S -p ""'
                header += f"export __SUDO_PW={shlex.quote(sudo_password)}\n"
                launch_root = (f"nohup sh -c '{sudo_prefix} sh -c \"$__ROOT_PROG\" sh "
                               f"\"$__OUT\" \"$__ERR\" \"$__TASK_CMD\"' >/dev/null 2>&1 &")
                launch_user = (f"nohup sh -c '{sudo_prefix} {cmd_shell} -c \"$__TASK_CMD\"' "
                               f"{user_redirect} &")
            else:
                sudo_prefix = 'sudo -n'
                launch_root = ('nohup sudo -n sh -c "$__ROOT_PROG" sh "$__OUT" "$__ERR" "$__TASK_CMD"'
                               ' >/dev/null 2>&1 &')
                launch_user = f'nohup sudo -n {cmd_shell} -c "$__TASK_CMD" {user_redirect} &'
            script_content = header + f"""__sudo_check=$({sudo_prefix} true 2>&1 >/dev/null) || {{
  echo "SUDO_FAILED:$__sudo_check" >&2
  exit 4
}}
__sudo_touch() {{ {sudo_prefix} sh -c ': >> "$1"' sh "$1" 2>/dev/null; }}
""" + ensure_logs + f"""if [ "$__ROOT_LOGS" = 1 ]; then
  {launch_root}
else
  {launch_user}
fi
""" + footer
        else:
            script_content = header + """__sudo_touch() { return 1; }
""" + ensure_logs + f"""{cmd_shell} -c "$__TASK_CMD" {user_redirect} &
""" + footer

        # umask 077: the script can contain the sudo password - never world-readable
        create_script_cmd = f"umask 077; cat > {script_path} << 'EOFSCRIPT'\n{script_content}\nEOFSCRIPT\nchmod 700 {script_path}"
        execution_cmd = f"sh {shlex.quote(script_path)}"
        return execution_cmd, script_content, create_script_cmd

    def _cmd_check_process_running(self, pid: int) -> str:
        return f"kill -0 {pid}"

    def _cmd_kill_process(self, pid: int, signal: int, sudo: bool, use_process_group: bool = False) -> str:
        if use_process_group:
            # Query the real process group at kill-time - never assume a fixed
            # offset from pid. Verified live 2026-07-05: a sudo'd task's real
            # group leader (the original launch script) has often already
            # exited by the time its pid is captured, so the group id and the
            # captured pid are NOT the same number. This reaches sudo's real
            # (possibly root-owned) child, not just the outer wrapper `kill pid`
            # alone would hit - see planning/2026-07-05-sudo-kill-scope.md.
            cmd = f"kill -{signal} -$(ps -o pgid= -p {pid} | tr -d ' ')"
        else:
            cmd = f"kill -{signal} {pid}"
        if sudo:
            # bash-less+sudo targets (e.g. FreeBSD) need `sh` here instead -
            # same bug/fix as ops/run.py's _handle_sudo: sudo execs the named
            # shell as its target command, independent of what's calling it.
            cmd_shell = 'bash' if self.ssh_client.capabilities.get('bash', True) else 'sh'
            return f"sudo -n {cmd_shell} -c {shlex.quote(cmd)}"
        return cmd

    def _cmd_rename_log(self, old_path: str, new_path: str, pid: int) -> str:
        # POSIX rename succeeds regardless of open file handles, so no race to
        # work around here (unlike Windows) - a plain synchronous rename is fine.
        return f"mv {shlex.quote(old_path)} {shlex.quote(new_path)}"


class SshTaskOperations_Win(SshTaskOperations):
    """Windows implementation using PowerShell."""

    def _get_default_log_dir(self) -> str:
        return "C:\\Windows\\Temp"

    def _merged_stderr_path(self, stdout_log: str) -> str:
        # cmd.exe can't send both streams to one file here, so _build_launch_script
        # puts stderr in a sibling <name>_err.log instead
        return stdout_log.replace('.log', '_err.log') if '.log' in stdout_log else stdout_log + '_err'

    def _build_launch_script(self, cmd: str, stdout_log: str, stderr_log: str, sudo: bool) -> tuple:
        """Build PowerShell command to launch background task.

        For Windows, we use PowerShell's -EncodedCommand to avoid needing a script file,
        which simplifies execution over SSH.

        Uses WMI (Win32_Process::Create via Invoke-CimMethod) to spawn the process, NOT
        Start-Process. Win32-OpenSSH puts each SSH session in a Windows Job Object, and
        a Start-Process-launched child inherits membership in that same job regardless
        of -WindowStyle Hidden - when the session's last handle closes (e.g. this
        launch script's own powershell.exe exiting), Windows kills every process still
        in the job, including the "detached" child. Verified live 2026-07-03: a
        Start-Process-launched 'ping -n 15 127.0.0.1' was confirmed dead within 2
        seconds, every time; the identical command launched via WMI stayed alive
        independently. Win32_Process::Create spawns through the WMI provider host (a
        separate service process tree), so the result is never a member of the SSH
        session's job at all. This was a long-standing, silent bug - the existing
        ssh_task_launch tests never caught it because they accept any of
        ['running', 'completed', 'not_found', 'exited'] as valid regardless of cause,
        and tend to use short commands where premature death looks identical to
        natural completion.
        """
        timestamp = int(time.time())
        # We don't actually use a script file anymore, but keep path format for log naming
        script_path = f"powershell_direct_{timestamp}"

        # Base64-encode the raw command and decode it at runtime instead of embedding
        # escaped text - a command containing its own nested quoting (e.g. 'powershell
        # -Command "Start-Sleep ...; Write-Output \'x\'"') silently mis-parses under a
        # naive '"' -> '\"' escape (verified live 2026-07-03: it corrupted the argument
        # into something cmd.exe misinterpreted instead of erroring loudly). Decoding a
        # base64 blob at runtime sidesteps escaping entirely - its alphabet has no
        # shell/PS metacharacters. Same fix as SshRunOperations_Win._wrap_for_pid_capture
        # in ops/run.py.
        cmd_b64 = base64.b64encode(cmd.encode('utf-8')).decode('ascii')
        decode_stmt = (
            f"$__cmdText = [System.Text.Encoding]::UTF8.GetString("
            f"[System.Convert]::FromBase64String('{cmd_b64}'))"
        )

        # Log paths as they'll really be used. stdout and stderr can't share one file
        # here, so a stderr "same as stdout" goes to a sibling <name>_err.log
        # (_merged_stderr_path) - the same contract as before.
        # launch_task passes '<log_dir>/null' for a stream the caller didn't ask to log
        if stdout_log and stdout_log.endswith('/null'):
            stdout_log = None
        if stderr_log and stderr_log.endswith('/null'):
            stderr_log = None
        out_path = stdout_log or None
        if stderr_log and stderr_log != stdout_log:
            err_path = stderr_log
        elif stdout_log:
            err_path = self._merged_stderr_path(stdout_log)
        else:
            err_path = None

        def ps_literal(value):
            return "'" + value.replace("'", "''") + "'"

        # The detached process is a small PowerShell wrapper that starts
        # 'cmd.exe /c <command>' via Start-Process with -RedirectStandardOutput/-Error.
        # The OS-level redirection covers the WHOLE command. The old
        # 'cmd.exe /c <command> > "log" 2> "err"' let cmd.exe bind the redirects to
        # only the last part of an '&' chain (e.g. 'echo a & echo b 1>&2' lost stdout
        # and wrote stderr into the stdout log). The command text is passed as
        # '/c ' + text exactly like ssh_cmd_run's Windows path (ops/run.py), so a
        # command behaves the same in both tools, and it's never re-parsed by an outer
        # cmd.exe. The returned PID is the wrapper's: ssh_task_kill's taskkill /T still
        # kills the whole tree, and the default-log rename watcher still waits for it.
        inner_lines = [
            decode_stmt,
            "$__spArgs = @{ FilePath = 'cmd.exe'; ArgumentList = ('/c ' + $__cmdText); "
            "NoNewWindow = $true; Wait = $true; PassThru = $true }",
        ]
        if out_path:
            inner_lines.append(f"$__spArgs.RedirectStandardOutput = {ps_literal(out_path)}")
        if err_path:
            inner_lines.append(f"$__spArgs.RedirectStandardError = {ps_literal(err_path)}")
        inner_lines.append("$__p = Start-Process @__spArgs; exit $__p.ExitCode")
        inner_script = "; ".join(inner_lines)
        inner_b64 = base64.b64encode(inner_script.encode('utf-16-le')).decode('ascii')
        wrapper_cmdline = (f"powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass "
                           f"-EncodedCommand {inner_b64}")

        # Check the logs can be created BEFORE launching (same contract as Linux/macOS):
        # otherwise Start-Process would fail inside the detached wrapper and we'd still
        # report a PID for a task that never ran.
        log_checks = "".join(
            f"try {{ [System.IO.File]::Open({ps_literal(path)}, 'Append', 'Write').Close() }} "
            f"catch {{ [Console]::Error.WriteLine('LOG_NOT_WRITABLE:' + {ps_literal(path)}); exit 3 }}; "
            for path in (out_path, err_path) if path
        )

        script_content = (
            f"{log_checks}"
            f"$__result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
            f"-Arguments @{{CommandLine = {ps_literal(wrapper_cmdline)}}}; "
            f"if ($__result.ReturnValue -eq 0) {{ Write-Output \"PID:$($__result.ProcessId)\" }} "
            f"else {{ Write-Error \"Win32_Process Create failed with code $($__result.ReturnValue)\"; exit 1 }}"
        )

        # Encode the script for -EncodedCommand (requires UTF-16LE encoding)
        script_bytes = script_content.encode('utf-16-le')
        script_b64 = base64.b64encode(script_bytes).decode('ascii')

        # For Windows, create_script_cmd is empty (no file to create)
        # and script_path is actually the full execution command
        create_script_cmd = "echo OK"  # No-op, just needs to succeed
        execution_cmd = f'powershell -ExecutionPolicy Bypass -EncodedCommand {script_b64}'

        return execution_cmd, script_content, create_script_cmd

    def _cmd_check_process_running(self, pid: int) -> str:
        # PowerShell command to check if process exists
        # Exit 0 if running, exit 1 if not
        return powershell_encoded_command(
            f"if (Get-Process -Id {pid} -ErrorAction SilentlyContinue) {{ exit 0 }} else {{ exit 1 }}"
        )

    def _cmd_kill_process(self, pid: int, signal: int, sudo: bool, use_process_group: bool = False) -> str:
        # Windows doesn't have signals, but we can simulate TERM vs KILL with
        # taskkill's /F flag. Both use /T (tree kill): every PID we hand out
        # (ssh_task_launch, ssh_cmd_run's real-PID capture) is a cmd.exe or
        # powershell.exe WRAPPER process, not the actual workload - cmd.exe /c
        # <command> launches the real work as a child and waits on it rather
        # than replacing itself (no exec() on Windows), so Stop-Process on just
        # the wrapper PID leaves the real process running as an orphan. Verified
        # live 2026-07-03: Stop-Process alone left a spawned ping.exe running
        # after its cmd.exe wrapper was confirmed killed; taskkill /F /T killed
        # the whole tree (wrapper + all descendants) in one call.
        # use_process_group is a no-op here - /T already handles the whole tree,
        # and Windows has no sudo/process-group concept to route around.
        return powershell_encoded_command(f"& taskkill /F /T /PID {pid}")

    def _cmd_rename_log(self, old_path: str, new_path: str, pid: int) -> str:
        # The task keeps an open handle on the placeholder-named log file for its
        # entire lifetime (it's redirected there via 'cmd.exe /c ... > file'), and
        # Windows rejects a rename while another process holds a file open (unlike
        # POSIX rename, which succeeds regardless). A short synchronous retry loop
        # here would only catch tasks that happen to finish within that window -
        # anything longer-running would keep the placeholder name forever. Instead,
        # launch a small detached watcher via WMI (the same survive-the-SSH-session
        # trick _build_launch_script uses) that waits for the task's own PID to
        # exit, then performs the rename whenever that actually happens - seconds
        # or hours later, it doesn't matter, since this call itself returns
        # immediately without waiting on it.
        old_err = old_path.replace('.log', '_err.log') if '.log' in old_path else old_path + '_err'
        new_err = new_path.replace('.log', '_err.log') if '.log' in new_path else new_path + '_err'
        watcher_script = (
            f"Wait-Process -Id {pid} -ErrorAction SilentlyContinue; "
            "Start-Sleep -Milliseconds 200; "
            f"Move-Item -Path '{old_path}' -Destination '{new_path}' -Force -ErrorAction SilentlyContinue; "
            f"Move-Item -Path '{old_err}' -Destination '{new_err}' -Force -ErrorAction SilentlyContinue"
        )
        watcher_b64 = base64.b64encode(watcher_script.encode('utf-16-le')).decode('ascii')
        launch_script = (
            f"$__result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
            f"-Arguments @{{CommandLine = 'powershell -NoProfile -ExecutionPolicy Bypass -EncodedCommand {watcher_b64}'}}; "
            "if ($__result.ReturnValue -ne 0) { exit 1 }"
        )
        return powershell_encoded_command(launch_script)
