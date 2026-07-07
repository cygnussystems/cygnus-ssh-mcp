from __future__ import annotations
import paramiko
import socket
import time
import tempfile
import os
import shlex
from datetime import datetime, UTC
import logging
import threading
import select
from typing import Optional, Callable, Dict, Deque, Any, Union, List, Literal
from cygnus_ssh_mcp.ops.history import CommandHistoryManager
from cygnus_ssh_mcp.ps_encode import powershell_encoded_command as _powershell_encoded_command
from cygnus_ssh_mcp.models import (
    SshError, CommandTimeout, CommandRuntimeTimeout, CommandFailed,
    SudoRequired, BusyError, OutputPurged, TaskNotFound, CommandHandle
)

# Configure basic logging for the library
log = logging.getLogger(__name__)
# Example basic config (users of the library should configure logging themselves)
# logging.basicConfig(level=logging.INFO)


def parse_capability_probe_output(output: str) -> dict:
    """Parse SshClient._CAPABILITY_PROBE_SCRIPT's 'key:yes'/'key:no' output
    lines into a {key: bool} dict. Module-level (not a method) so it's
    testable without a live connection. Lines that don't match the expected
    'key:yes'/'key:no' shape are silently skipped, not treated as failures -
    leaves that key absent rather than guessing.
    """
    capabilities = {}
    for line in output.splitlines():
        line = line.strip()
        if ':' not in line:
            continue
        key, _, value = line.partition(':')
        key = key.strip()
        value = value.strip()
        if value in ('yes', 'no'):
            capabilities[key] = (value == 'yes')
    return capabilities


class SshClient:
    """
    SSH manager for running commands, transferring files, and tracking history.
    Includes support for launching background tasks and monitoring them.
    Uses logging for output. Implements wall-clock timeouts for run().
    """
    def __init__(self, host, user, port=22, keyfile=None, key_passphrase=None,
                 password=None, sudo_password=None,
                 connect_timeout=10, history_limit=50, tail_keep=100):
        # Initialize platform detection
        self.os_type = None  # 'windows', 'linux', 'macos', or 'flex' (any other
                              # responsive POSIX kernel - see _detect_os/_create_operations)
        self.os_subtype = None  # 'windows10', 'debian', 'centos', etc. - for 'flex',
                                 # the real kernel name reported by uname -s (e.g. 'freebsd')
        self.capabilities = {}  # Populated by _probe_capabilities() for os_type in
                                 # ('linux', 'flex') only - see CapabilityGate in
                                 # ops/capability_gate.py. A key's absence (not just a
                                 # False value) is treated as "not confirmed missing" by
                                 # the gate, so a probe hiccup never blocks an operation
                                 # that would otherwise have worked - only a *confirmed*
                                 # False result blocks anything.

        # Initialize connection status tracking
        self._connection_status = {
            'os_type': None,
            'os_version': None,
            'user': None,
            'cwd': None,
            'has_sudo': sudo_password is not None,  # Assume sudo if password provided
            'last_updated': None
        }
        self._status_lock = threading.Lock()  # For thread safety
        self.host = host
        self.user = user
        self.port = port
        self.alias = None  # Set by the caller after connect, if the host has a configured alias
        self.keyfile = keyfile
        self.key_passphrase = key_passphrase
        self.password = password
        self.sudo_password = sudo_password
        self.connect_timeout = connect_timeout
        self._busy_lock = threading.Lock()
        self.history_limit = history_limit
        self.tail_keep = tail_keep
        self.history_manager = CommandHistoryManager(history_limit, tail_keep)
        self._logger = logging.getLogger(f"{__name__}.SshClient")

        # Initialize operations after connection
        self.run_ops = None
        self.task_ops = None
        self.file_ops = None
        self.dir_ops = None
        self.os_ops = None
        
        # Setup Paramiko client
        self._client = paramiko.SSHClient()
        self._client.load_system_host_keys()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        # Connect and detect OS
        self._connect()
        self._detect_os()

        # Validate supported OS
        if self.os_type not in ('linux', 'macos', 'windows', 'flex'):
            raise SshError(f"Unsupported OS detected: {self.os_type}. Only Linux, macOS, Windows, and generic POSIX ('flex') targets are supported.")

        # For Windows, detect elevation status and verify PowerShell version
        if self.os_type == 'windows':
            self._detect_windows_elevation()
            self._check_windows_powershell_version()

        # For anything that might not have full GNU coreutils (a real Linux
        # host is nearly always fine; this also catches BusyBox-based embedded
        # devices that detect as 'linux', plus the 'flex' catch-all) - probe
        # once, cheaply, so operations can check before running instead of
        # failing with a cryptic remote error. Not needed for macOS/Windows -
        # already fully supported and tested.
        if self.os_type in ('linux', 'flex'):
            self._probe_capabilities()

        self._create_operations()

    def _describe_os_detection_failure(self, uname_result='', uname_exit=1, uname_stderr='',
                                        uname_probe_error=None, win_result=None, win_exit=None,
                                        ps_result=None, ps_exit=None, win_probe_error=None):
        """Build a diagnostic message for a completely failed OS detection -
        rather than a generic "nothing worked", surface exactly what each
        probe returned (or how it failed), plus a raw connectivity sanity
        check, so a genuinely new kind of target (e.g. a vendor CLI shell
        that's neither POSIX nor PowerShell) can be diagnosed from the error
        alone instead of a second investigation."""
        lines = ["Failed to detect OS type - neither Linux/macOS nor Windows commands succeeded."]
        if uname_probe_error is not None:
            lines.append(f"  'uname -s' probe raised: {uname_probe_error!r}")
        else:
            lines.append(f"  'uname -s' -> exit={uname_exit}, stdout={uname_result!r}, stderr={uname_stderr!r}")
        if win_probe_error is not None:
            lines.append(f"  Windows detection probe raised: {win_probe_error!r}")
        else:
            lines.append(f"  'echo %OS%' -> exit={win_exit}, stdout={win_result!r}")
            lines.append(f"  PowerShell version probe -> exit={ps_exit}, stdout={ps_result!r}")
        # One last raw sanity check: does the shell respond to ANYTHING at
        # all? Distinguishes "no real shell (e.g. a vendor menu/CLI)" from "a
        # real shell that just doesn't have uname/cmd.exe in its PATH".
        raw_stderr = ''
        try:
            marker = "___SSH_MCP_PROBE_ALIVE___"
            stdin, stdout, stderr = self._client.exec_command(f'echo {marker}', timeout=5)
            raw_out = stdout.read().decode('utf-8', errors='replace').strip()
            raw_stderr = stderr.read().decode('utf-8', errors='replace').strip()
            raw_exit = stdout.channel.recv_exit_status()
            lines.append(f"  Raw 'echo {marker}' sanity check -> exit={raw_exit}, stdout={raw_out!r}, stderr={raw_stderr!r}")
        except Exception as sanity_err:
            lines.append(f"  Raw sanity check also failed to complete: {sanity_err!r}")

        # Heuristic hint for the most common real-world cause of "every probe
        # rejected identically, even a bare echo": the SSH login itself
        # succeeded, but this account isn't allowed to open a shell at all -
        # live-confirmed on a Synology SRM router (interactive PTY session,
        # not just exec_command, was torn down immediately post-auth with the
        # same "Permission denied, please try again.", i.e. rejected in a PAM
        # account/session phase, not the password/auth phase). This is an
        # account/device permission gap, not a shell-syntax or capability gap
        # - no capability probe or command rewrite can work around it. Two
        # concrete causes confirmed common on Synology SRM/DSM specifically:
        # some SRM firmware only allows SSH shell access for the original/
        # primary admin account, regardless of other accounts' admin role;
        # and 2FA/OTP enabled on the account can fail this same way, since a
        # non-interactive SSH session has no way to supply the OTP.
        combined_stderr = f"{uname_stderr} {raw_stderr}".lower()
        if 'permission denied' in combined_stderr:
            lines.append(
                "  Likely cause: SSH authentication succeeded, but this account isn't "
                "permitted to open a shell or run commands at all (every probe above was "
                "rejected identically, including a bare 'echo', immediately after login). "
                "This is an account/device permission restriction, not a capability gap or "
                "unsupported shell - no command rewrite can work around it. Common causes on "
                "routers/NAS devices (e.g. Synology SRM/DSM): SSH shell access limited to the "
                "original/primary admin account regardless of other accounts' admin role, or "
                "2FA/OTP enabled on this account (a non-interactive SSH session has no way to "
                "supply the OTP). Try the original/primary admin account, try an account with "
                "2FA disabled, or check the device's own SSH/shell-access settings."
            )
        return "\n".join(lines)

    def _detect_os(self):
        """Detect the remote OS type and subtype."""
        # First verify we have an active connection
        if not self._client or not self._client.get_transport() or not self._client.get_transport().is_active():
            raise SshError("Cannot detect OS - no active SSH connection")

        try:
            # Use direct Paramiko command execution for OS detection.
            # 'uname' can hang instead of failing fast on some Windows hosts
            # (e.g. slow PATH resolution for an unrecognized command under a
            # PowerShell default shell), so a timeout here must be treated the
            # same as "uname failed" and fall through to Windows detection,
            # not abort the whole detection routine.
            result = ''
            exit_status = 1
            uname_stderr = ''
            uname_probe_error = None
            try:
                stdin, stdout, stderr = self._client.exec_command('uname -s', timeout=5)
                result = stdout.read().decode('utf-8', errors='replace').strip()
                uname_stderr = stderr.read().decode('utf-8', errors='replace').strip()
                exit_status = stdout.channel.recv_exit_status()
            except Exception as uname_err:
                uname_probe_error = uname_err
                self._logger.debug(f"'uname -s' probe did not complete, assuming non-Unix: {uname_err!r}")

            if exit_status == 0 and 'Linux' in result:
                self.os_type = 'linux'
                self._detect_linux_distro()
            elif exit_status == 0 and 'Darwin' in result:
                self.os_type = 'macos'
            elif exit_status == 0 and result:
                # uname succeeded but reported a kernel we don't have dedicated
                # support for (e.g. 'FreeBSD', 'SunOS', 'NetBSD') - a real,
                # responsive POSIX-ish target, not a detection failure. Composed
                # from existing Linux/Mac ops classes in _create_operations
                # (best-effort fit), gated behind _probe_capabilities() rather
                # than assumed to behave identically to Linux/macOS.
                self.os_type = 'flex'
                self.os_subtype = result.lower()
                self._logger.info(
                    f"Detected non-standard POSIX kernel '{result}' - using 'flex' platform support"
                )
            else:
                # uname failed or returned unknown - try Windows detection
                # Use 'echo %OS%' which is fast and returns 'Windows_NT' on Windows
                try:
                    stdin, stdout, stderr = self._client.exec_command('echo %OS%', timeout=5)
                    win_result = stdout.read().decode('utf-8', errors='replace').strip()
                    win_exit = stdout.channel.recv_exit_status()

                    if win_exit == 0 and 'Windows' in win_result:
                        self.os_type = 'windows'
                        self._detect_windows_version()
                    else:
                        # Try PowerShell as a fallback
                        stdin, stdout, stderr = self._client.exec_command(
                            _powershell_encoded_command('$PSVersionTable.PSVersion.Major'), timeout=5)
                        ps_result = stdout.read().decode('utf-8', errors='replace').strip()
                        ps_exit = stdout.channel.recv_exit_status()

                        if ps_exit == 0 and ps_result.isdigit():
                            self.os_type = 'windows'
                            self._detect_windows_version()
                        else:
                            raise SshError(self._describe_os_detection_failure(
                                result, exit_status, uname_stderr, uname_probe_error,
                                win_result, win_exit, ps_result, ps_exit
                            ))
                except SshError:
                    raise
                except Exception as win_err:
                    raise SshError(self._describe_os_detection_failure(
                        result, exit_status, uname_stderr, uname_probe_error,
                        win_probe_error=win_err
                    ))
        except SshError:
            self.close()
            raise
        except Exception as e:
            # Close the connection and raise an error instead of defaulting to Linux
            self.close()
            raise SshError(f"Failed to detect OS: {e!r}")
            
        self._logger.info(f"Detected remote OS: {self.os_type} ({self.os_subtype})")
        
        # Update connection status with OS info
        with self._status_lock:
            self._connection_status.update({
                'os_type': self.os_type,
                'os_version': self.os_subtype
            })

    def _detect_linux_distro(self):
        """Detect Linux distribution subtype."""
        try:
            result = self.run('cat /etc/os-release', origin='connection_probe', parent_tool='ssh_conn_connect')
            if 'debian' in result.lower():
                self.os_subtype = 'debian'
            elif 'centos' in result.lower():
                self.os_subtype = 'centos'
            else:
                self.os_subtype = 'unknown_linux'
        except Exception:
            self.os_subtype = 'unknown_linux'

    def _detect_windows_version(self):
        """Detect Windows version subtype."""
        try:
            # Use 'ver' command which is fast and returns Windows version
            stdin, stdout, stderr = self._client.exec_command('ver', timeout=5)
            result = stdout.read().decode('utf-8', errors='replace').strip()

            if 'Windows Server 2019' in result or '10.0.17' in result:
                self.os_subtype = 'windows_server_2019'
            elif 'Windows Server 2022' in result or '10.0.20' in result:
                self.os_subtype = 'windows_server_2022'
            elif 'Windows Server 2016' in result or '10.0.14' in result:
                self.os_subtype = 'windows_server_2016'
            elif 'Windows 10' in result:
                self.os_subtype = 'windows_10'
            elif 'Windows 11' in result:
                self.os_subtype = 'windows_11'
            else:
                self.os_subtype = 'unknown_windows'

            self._logger.debug(f"Windows version detected: {result}")
        except Exception as e:
            self._logger.warning(f"Failed to detect Windows version: {e}")
            self.os_subtype = 'unknown_windows'

    def _detect_windows_elevation(self):
        """Detect if the Windows session is running with Administrator privileges."""
        self._is_elevated = False
        try:
            # Check if current session has Administrator role
            check_cmd = _powershell_encoded_command(
                "([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent())"
                ".IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)"
            )
            stdin, stdout, stderr = self._client.exec_command(check_cmd, timeout=10)
            result = stdout.read().decode('utf-8', errors='replace').strip().lower()
            self._is_elevated = result == 'true'
            self._logger.info(f"Windows elevation status: {'elevated (Administrator)' if self._is_elevated else 'not elevated'}")
        except Exception as e:
            self._logger.warning(f"Failed to detect Windows elevation status: {e}. Assuming not elevated.")
            self._is_elevated = False

    def _check_windows_powershell_version(self):
        """Check that Windows has PowerShell 5.0+ which is required for all operations."""
        try:
            check_cmd = _powershell_encoded_command('$PSVersionTable.PSVersion.Major')
            stdin, stdout, stderr = self._client.exec_command(check_cmd, timeout=10)
            result = stdout.read().decode('utf-8', errors='replace').strip()

            if result.isdigit():
                major_version = int(result)
                if major_version < 5:
                    self.close()
                    raise SshError(
                        f"PowerShell {major_version}.x detected. This tool requires PowerShell 5.0 or later. "
                        f"Windows Server 2016+, Windows 10+ have PowerShell 5.0+ built-in. "
                        f"For older Windows versions, install Windows Management Framework (WMF) 5.1."
                    )
                self._logger.info(f"PowerShell version: {major_version}.x")
            else:
                self._logger.warning(f"Could not parse PowerShell version from: {result}")
        except SshError:
            raise
        except Exception as e:
            self._logger.warning(f"Failed to check PowerShell version: {e}")

    # Feature-tests exactly what the codebase depends on (see
    # ops/capability_gate.py for the call sites each key gates). Run under
    # 'sh -c' explicitly, since we can't assume bash exists - one of the
    # things being probed. Every probe writes 'key:yes' or 'key:no'; the tar
    # check builds and extracts a real tiny archive rather than parsing
    # --version output, since BusyBox/GNU/bsdtar version strings aren't a
    # reliable way to tell --strip-components support apart. All checks that
    # need *some* directory (find/stat/du/tar) use a fresh $PROBE_DIR under
    # /tmp rather than the login shell's starting directory ('.') - live-
    # verified this matters: '.' is often the SSH user's home directory, which
    # can contain leftover root-owned subdirectories (e.g. from prior sudo'd
    # test runs) that make a real, fully GNU-compatible `du -sb .` exit
    # non-zero on "Permission denied" for an unrelated subdirectory, a false
    # negative unrelated to whether -s/-b themselves are supported. Known
    # remaining edge case: if /tmp itself isn't writable, $PROBE_DIR can't be
    # created and every one of these checks reports 'no' even on a fully
    # GNU-compatible host - not fixed further since 'tmp_writable' already
    # surfaces that fact directly, and most of this codebase's temp-file-based
    # operations need a writable /tmp regardless.
    #
    # NOTE: designed by reading BusyBox/GNU documentation, not yet
    # live-verified against a real BusyBox target - see
    # planning/2026-07-06-non-standard-ssh-targets-capability-scoping.md.
    _CAPABILITY_PROBE_SCRIPT = r"""sh -c '
PROBE_DIR="/tmp/.ssh_mcp_cap_probe_$$"
mkdir -p "$PROBE_DIR" 2>/dev/null
command -v bash >/dev/null 2>&1 && echo "bash:yes" || echo "bash:no"
find "$PROBE_DIR" -maxdepth 0 -printf "" >/dev/null 2>&1 && echo "find_printf:yes" || echo "find_printf:no"
find "$PROBE_DIR" -maxdepth 0 -depth >/dev/null 2>&1 && echo "find_depth:yes" || echo "find_depth:no"
stat -c "%a" "$PROBE_DIR" >/dev/null 2>&1 && echo "stat_c:yes" || echo "stat_c:no"
du -sb "$PROBE_DIR" >/dev/null 2>&1 && echo "du_sb:yes" || echo "du_sb:no"
ps -o pgid= -p $$ >/dev/null 2>&1 && echo "ps_pgid:yes" || echo "ps_pgid:no"
printf "a\0" | xargs -0 true >/dev/null 2>&1 && echo "xargs_0:yes" || echo "xargs_0:no"
command -v sudo >/dev/null 2>&1 && echo "sudo:yes" || echo "sudo:no"
[ -w /tmp ] && echo "tmp_writable:yes" || echo "tmp_writable:no"
mkdir -p "$PROBE_DIR/src/a" 2>/dev/null && touch "$PROBE_DIR/src/a/f" 2>/dev/null && tar -cf "$PROBE_DIR/t.tar" -C "$PROBE_DIR/src" a >/dev/null 2>&1 && mkdir -p "$PROBE_DIR/out" 2>/dev/null && tar -xf "$PROBE_DIR/t.tar" -C "$PROBE_DIR/out" --strip-components=1 >/dev/null 2>&1 && echo "tar_strip_components:yes" || echo "tar_strip_components:no"
mkdir -p "$PROBE_DIR/out2" 2>/dev/null && tar -xf "$PROBE_DIR/t.tar" -C "$PROBE_DIR/out2" --keep-old-files >/dev/null 2>&1 && echo "tar_keep_old_files:yes" || echo "tar_keep_old_files:no"
rm -rf "$PROBE_DIR" 2>/dev/null
'
"""

    def _probe_capabilities(self):
        """Feature-test the specific GNU/BusyBox-coreutils capabilities this
        codebase depends on, caching results on self.capabilities. Only called
        for os_type in ('linux', 'flex') - see __init__. A capability that
        can't be confirmed (probe failed entirely, or an individual line
        wasn't parsed) is left absent from the dict rather than defaulted to
        False - CapabilityGate (ops/capability_gate.py) only blocks on a
        *confirmed* False, so a probe hiccup can never regress a normal Linux
        host that would otherwise have worked fine.
        """
        self.capabilities = {}
        try:
            stdin, stdout, stderr = self._client.exec_command(self._CAPABILITY_PROBE_SCRIPT, timeout=15)
            output = stdout.read().decode('utf-8', errors='replace')
            self.capabilities = parse_capability_probe_output(output)
            self._logger.info(f"Probed capabilities: {self.capabilities}")
        except Exception as e:
            self._logger.warning(f"Capability probe failed, proceeding with no confirmed capabilities: {e}")

    def _create_operations(self):
        """Create platform-specific operation classes based on detected OS."""
        from cygnus_ssh_mcp.ops.file import SshFileOperations_Linux, SshFileOperations_Mac, SshFileOperations_Win
        from cygnus_ssh_mcp.ops.task import SshTaskOperations_Linux, SshTaskOperations_Win
        from cygnus_ssh_mcp.ops.run import SshRunOperations_Linux, SshRunOperations_Win
        from cygnus_ssh_mcp.ops.directory import SshDirectoryOperations_Linux, SshDirectoryOperations_Mac, SshDirectoryOperations_Win
        from cygnus_ssh_mcp.ops.os_ops import SshOsOperations_Linux, SshOsOperations_Mac, SshOsOperations_Win
        from cygnus_ssh_mcp.ops.capability_gate import CapabilityGate, LINUX_DIRECTORY_GUARDS, FLEX_DIRECTORY_GUARDS, TASK_GUARDS

        # Select platform-specific operations based on detected OS
        if self.os_type == 'linux':
            self.run_ops = SshRunOperations_Linux(self, self.tail_keep)
            self.task_ops = CapabilityGate(SshTaskOperations_Linux(self), self, TASK_GUARDS)
            self.file_ops = SshFileOperations_Linux(self)
            self.dir_ops = CapabilityGate(SshDirectoryOperations_Linux(self), self, LINUX_DIRECTORY_GUARDS)
            self.os_ops = SshOsOperations_Linux(self)
        elif self.os_type == 'macos':
            # macOS uses bash like Linux for run/task operations
            self.run_ops = SshRunOperations_Linux(self, self.tail_keep)
            self.task_ops = SshTaskOperations_Linux(self)
            self.file_ops = SshFileOperations_Mac(self)
            self.dir_ops = SshDirectoryOperations_Mac(self)
            self.os_ops = SshOsOperations_Mac(self)
        elif self.os_type == 'windows':
            self.run_ops = SshRunOperations_Win(self, self.tail_keep)
            self.task_ops = SshTaskOperations_Win(self)
            self.file_ops = SshFileOperations_Win(self)
            self.dir_ops = SshDirectoryOperations_Win(self)
            self.os_ops = SshOsOperations_Win(self)
        elif self.os_type == 'flex':
            # Catch-all for any responsive POSIX kernel that isn't Linux/macOS/
            # Windows - composed entirely from existing classes, no new ones:
            # reuse _Linux's run/task ops (bash-family shell behavior is the
            # same across POSIX targets, same as macOS already does) and
            # _Mac's file/dir/os ops (BSD-flavored basics like `stat -f` vs
            # `-c` already correct there - a closer fit for an unrecognized
            # non-Linux kernel than GNU/Linux syntax would be). Gated behind
            # the same capability probe as 'linux', since none of this is
            # assumed to work perfectly out of the box.
            self.run_ops = SshRunOperations_Linux(self, self.tail_keep)
            self.task_ops = CapabilityGate(SshTaskOperations_Linux(self), self, TASK_GUARDS)
            self.file_ops = SshFileOperations_Mac(self)
            self.dir_ops = CapabilityGate(SshDirectoryOperations_Mac(self), self, FLEX_DIRECTORY_GUARDS)
            self.os_ops = SshOsOperations_Mac(self)
        else:
            # This shouldn't happen due to the check in __init__, but defensive programming
            raise SshError(f"No operations available for OS type: {self.os_type}")

    def _connect(self):
        """Establish SSH connection and update connection status."""
        self._logger.info(f"Connecting to {self.user}@{self.host}:{self.port}...")
        
        # Update connection status with initial info. Deliberately do NOT stamp
        # 'last_updated' here - cwd isn't probed until update_connection_status()
        # actually runs, and stamping it early would let the 5-minute cache mask
        # a None cwd for the first 5 minutes of every connection.
        with self._status_lock:
            self._connection_status.update({
                'user': self.user,
                'host': self.host
            })
        kwargs = dict(
            hostname=self.host,
            port=self.port,
            username=self.user,
            timeout=self.connect_timeout
        )
        if self.keyfile:
            kwargs['key_filename'] = self.keyfile
            self._logger.info(f"Using keyfile: {self.keyfile}")
            if self.key_passphrase:
                kwargs['passphrase'] = self.key_passphrase
                self._logger.info("Using passphrase for encrypted key")
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

    def is_connected(self) -> bool:
        """
        Check if the SSH client is connected.
        
        Returns:
            bool: True if connected, False otherwise.
        """
        return (self._client is not None and 
                self._client.get_transport() is not None and 
                self._client.get_transport().is_active())

    def close(self):
        """Close the SSH connection and clear status."""
        if self._client:
            self._logger.info("Closing SSH connection.")
            self._client.close()
            
        # Clear connection status
        with self._status_lock:
            self._connection_status = {
                'os_type': None,
                'os_version': None,
                'user': None,
                'cwd': None,
                'has_sudo': False,
                'last_updated': None
            }

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def _add_to_history(self, handle):
        """Adds a handle to history (delegates to history_manager)."""
        self.history_manager.add_command(handle.cmd, handle.pid)

    def update_connection_status(self, force=False, parent_tool=None):
        """Update cached connection status if stale (>5 minutes) or forced.

        parent_tool: name of the MCP tool that triggered this, if known - passed
        through for history labeling. Left as None (rather than guessing) when
        the caller can't know for certain: this refresh can be triggered by any
        of several tools (ssh_conn_connect/_status/_host_info directly, or any
        mutating tool indirectly via _connection_metadata()), so a specific
        wrong guess would be more misleading than an honest 'unknown'.
        """
        with self._status_lock:
            now = time.time()
            last_update = self._connection_status.get('last_updated') or 0

            if not force and (now - last_update) < 300:  # 5 minute cache
                return

            try:
                # Get basic info using OS-appropriate commands
                if self.os_type == 'windows':
                    # Windows: use separate commands
                    user_handle = self.run('whoami', io_timeout=5,
                                            origin='connection_probe', parent_tool=parent_tool)
                    user = user_handle.get_full_output().strip() if user_handle.exit_code == 0 else 'Unknown'
                    cwd_handle = self.run('cd', io_timeout=5,
                                           origin='connection_probe', parent_tool=parent_tool)
                    cwd = cwd_handle.get_full_output().strip() if cwd_handle.exit_code == 0 else 'Unknown'
                    self._connection_status['user'] = user
                    self._connection_status['cwd'] = cwd
                else:
                    # Linux/macOS: use bash command
                    cmd = """
                    echo "USER:$(whoami)"
                    echo "CWD:$(pwd)"
                    """
                    handle = self.run(cmd, io_timeout=5,
                                       origin='connection_probe', parent_tool=parent_tool)
                    output = "".join(handle.tail(handle.total_lines))

                    # Parse output
                    for line in output.splitlines():
                        if 'USER:' in line:
                            self._connection_status['user'] = line.split(':', 1)[1].strip()
                        elif 'CWD:' in line:
                            self._connection_status['cwd'] = line.split(':', 1)[1].strip()

                # Update timestamp
                self._connection_status['last_updated'] = now

            except Exception as e:
                self._logger.warning(f"Failed to update connection status: {e}")

    def get_connection_status(self, parent_tool=None) -> dict:
        """Return current connection status with timestamp.

        parent_tool: forwarded to update_connection_status() for history
        labeling, if known.
        """
        self.update_connection_status(parent_tool=parent_tool)  # Refresh if needed
        with self._status_lock:
            return {
                **self._connection_status,
                'timestamp': datetime.now(UTC).isoformat(),
                'host': self.host,
                'alias': self.alias
            }

    def verify_sudo_access(self) -> bool:
        """Verify sudo/admin access. For Windows, returns elevation status."""
        try:
            if self.os_type == 'windows':
                # For Windows, return the cached elevation status
                return getattr(self, '_is_elevated', False)
            else:
                # For Linux/macOS, check passwordless sudo
                handle = self.run('sudo -n true 2>/dev/null && echo true || echo false', io_timeout=5,
                                   origin='sudo_probe', parent_tool='ssh_conn_verify_sudo')
                return 'true' in handle.last_nonblank()
        except Exception as e:
            self._logger.warning(f"Failed to verify sudo access: {e}")
            return False



    def run(self, cmd: str, io_timeout: float = 60.0, runtime_timeout: Optional[float] = None,
           sudo: bool = False, cwd: Optional[str] = None, wait_timeout: Optional[float] = None,
           origin: str = 'user', parent_tool: Optional[str] = None) -> CommandHandle:
        """
        Execute a command synchronously, streaming output into a CommandHandle.
        This method BLOCKS until the command finishes, fails, or times out.
        Supports I/O inactivity timeout (io_timeout), total elapsed wait regardless of
        activity (wait_timeout), and total runtime timeout (runtime_timeout) - only the
        last of these ever kills the remote command; io_timeout/wait_timeout hand
        monitoring off to a background thread instead.
        cwd (optional): run the command in this directory for this call only - no state is
        remembered across calls (each call is a fresh remote process regardless). Fails closed:
        if the directory doesn't exist, the command never runs at all (raises CwdNotFound).
        origin/parent_tool (optional): label this as internal plumbing rather than a
        directly user-requested command - see ssh_cmd_history's include_internal filter.
        Returns the CommandHandle upon completion or raises CommandFailed, CommandTimeout, CommandRuntimeTimeout, SudoRequired, CwdNotFound.
        """
        return self.run_ops.execute_command(cmd, io_timeout, runtime_timeout, sudo, cwd,
                                              wait_timeout=wait_timeout, origin=origin, parent_tool=parent_tool)


    def launch(self, cmd: str, sudo: bool = False, stdout_log: Optional[str] = None,
              stderr_log: Optional[str] = None, log_output: bool = True, add_to_history: bool = True) -> CommandHandle:
        """
        Launch a command in the background and return a CommandHandle with the PID.
        This method returns almost immediately, it does NOT block waiting for the command.
        Output is NOT captured in the handle's buffer; it's redirected to files or /dev/null.
        If log_output=True (default) and stdout_log/stderr_log are None, redirects
        output to /tmp/task-<pid>.log.
        If add_to_history=False, the command won't appear in command history.
        WARNING: Does not work for interactive commands requiring input.
        """
        return self.task_ops.launch_task(cmd, stdout_log, stderr_log, log_output, sudo, add_to_history)

    def task_status(self, pid: int) -> Literal['running', 'exited', 'invalid', 'error']:
        """
        Check the status of a process with the given PID on the remote host using a direct channel.
        Returns:
            'running': Process exists.
            'exited': Process does not exist (assumed completed or killed).
            'error': Failed to check status.
        """
        return self.task_ops.get_task_status(pid)


    def task_kill(self, pid: int, signal: int = 15, sudo: bool = False,
                 force_kill_signal: int = 9, wait_seconds: float = 1.0) -> tuple[Literal['killed', 'already_exited', 'failed_to_kill', 'invalid_pid', 'error'], bool]:
        """
        Send a signal to a process with the given PID on the remote host.
        Uses self.run() internally, so it respects the busy lock and handles sudo.
        Tries the specified signal, waits, checks status, then tries force_kill_signal (default SIGKILL) if needed.
        Returns:
            Tuple of (status, force_kill_used):
            - 'killed': Process was successfully terminated (by signal or force_kill_signal).
            - 'already_exited': Process was already gone before signaling.
            - 'failed_to_kill': Signaling attempts failed or process remained running.
            - 'error': An error occurred during the kill attempt.
            force_kill_used is True iff the force_kill_signal fallback was actually
            attempted (the initial signal alone was not enough), regardless of
            whether that fallback itself succeeded.
        """
        return self.task_ops.kill_task(pid, signal, sudo, force_kill_signal, wait_seconds)

    def mark_kill_confirmed(self, handle_id: int) -> None:
        """Record that a command handle's remote process is confirmed no longer
        running (killed, already exited, or not running), so ssh_cmd_check_status
        can report a terminal status instead of 'unknown_still_running' forever.
        No-op if the handle_id isn't in history.
        """
        try:
            handle = self.history_manager.get_handle(handle_id)
        except KeyError:
            return
        handle.kill_confirmed = True

    def output(self, handle_id: int, mode: Literal['tail', 'chunk'] = 'tail',
              n: int = 50, start: Optional[int] = None, lines: Optional[int] = None,
              stream: Literal['stdout', 'stderr'] = 'stdout') -> List[str]:
        """Retrieve output from a previous CommandHandle created by run().

        stream: 'stdout' (default) or 'stderr' - only affects 'tail' mode; 'chunk'
        mode remains stdout-only (nothing currently exposes stderr chunking).
        """
        try:
            handle = self.history_manager.get_handle(handle_id)
        except KeyError: # history_manager.get_handle raises KeyError if handle_id not found.
            raise TaskNotFound(handle_id)
        # CommandHandle.tail() or .chunk() can raise OutputPurged if output is no longer available.

        if mode == 'tail':
            num_lines_to_tail = n  # Default to n
            if lines is not None:  # If lines is provided, it takes precedence for tail mode
                num_lines_to_tail = lines
            if stream == 'stderr':
                return handle.tail_stderr(num_lines_to_tail)
            return handle.tail(num_lines_to_tail)
        elif mode == 'chunk':
            if start is None:
                raise ValueError("`start` is required for chunk mode")
            try:
                start_idx = int(start)
            except ValueError:
                raise ValueError("`start` must be an integer for chunk mode.")
            # 'n' is used as length for chunk mode. 'lines' is not typically used here.
            return handle.chunk(start_idx, n)
        else:
            raise ValueError(f"Unknown mode for output: {mode}")

    def get(self, remote_path: str, local_path: str) -> None:
        """Download a file from remote to local."""
        return self.file_ops.get(remote_path, local_path)

    def put(self, local_path: str, remote_path: str) -> None:
        """Upload a file from local to remote."""
        return self.file_ops.put(local_path, remote_path)

    def mkdir(self, path: str, sudo: bool = False, mode: int = 0o755) -> None:
        """Create a remote directory with optional sudo."""
        return self.file_ops.mkdir(path, sudo, mode)

    def rmdir(self, path: str, sudo: bool = False, recursive: bool = False) -> None:
        """Remove a remote directory with optional sudo."""
        return self.file_ops.rmdir(path, sudo, recursive)

    def listdir(self, path: str) -> List[str]:
        """List contents of a remote directory."""
        return self.file_ops.listdir(path)

    def stat(self, path: str) -> Dict:
        """Get file/directory status info."""
        return self.file_ops.stat(path)

    def read_file(self, remote_path: str, encoding: str = 'utf-8',
                  max_size: int = 10 * 1024 * 1024) -> str:
        """
        Read file contents directly via SFTP.

        This method uses SFTP to read raw bytes from the remote file and decodes
        them on the client side. This completely bypasses any shell or console
        encoding issues (like Windows PowerShell's OEM code page problem).

        Args:
            remote_path: Path to the file to read
            encoding: Character encoding to use when decoding (default: utf-8)
            max_size: Maximum file size in bytes to read (default: 10MB).
                     Set to 0 for no limit.

        Returns:
            The file contents as a string

        Raises:
            SshError: If the file cannot be read or exceeds max_size
        """
        return self.file_ops.read_file(remote_path, encoding, max_size)

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
        return self.file_ops.find_lines_with_pattern(remote_file, pattern, regex, sudo)
    
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
        return self.file_ops.get_context_around_line(remote_file, match_line, context, sudo)
    
    def replace_line_by_content(self, remote_file: str, match_line: str, new_lines: list,
                               sudo: bool = False, force: bool = False, **kwargs) -> dict:
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
        return self.file_ops.replace_line_by_content(remote_file, match_line, new_lines, sudo, force, **kwargs)

    def insert_lines_after_match(self, remote_file: str, match_line: str, lines_to_insert: list,
                                sudo: bool = False, force: bool = False, **kwargs) -> dict:
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
        return self.file_ops.insert_lines_after_match(remote_file, match_line, lines_to_insert, sudo, force, **kwargs)

    def delete_line_by_content(self, remote_file: str, match_line: str,
                              sudo: bool = False, force: bool = False, **kwargs) -> dict:
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
        return self.file_ops.delete_line_by_content(remote_file, match_line, sudo, force, **kwargs)
    
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
        return self.file_ops.copy_file(source_path, destination_path, append_timestamp, sudo)



    def reboot(self, wait: bool = True, timeout: int = 300) -> None:
        """Reboot the remote host and optionally wait until it comes back."""
        return self.os_ops.reboot(wait, timeout)


    def full_status(self, parent_tool=None) -> Dict[str, Any]:
        """Return a snapshot of system state using a combined command.

        parent_tool: name of the MCP tool that triggered this (for history
        labeling - every sub-command run here is tagged origin='connection_probe').
        """
        return self.os_ops.status(parent_tool=parent_tool)


    def history(self) -> List[Dict[str, Any]]:
        """Return metadata for recent CommandHandles."""
        return self.history_manager.get_history()

    # Directory operations wrappers
    def search_files_recursive(self, start_path: str, name_pattern: str,
                             max_depth: Optional[int] = None, include_dirs: bool = False) -> List[Dict[str, str]]:
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
        return self.dir_ops.search_files_recursive(start_path, name_pattern, max_depth, include_dirs)
    
    def calculate_directory_size(self, path: str) -> int:
        """
        Compute total size of a directory recursively in bytes.
        
        Args:
            path: Directory to measure
            
        Returns:
            Total size in bytes
        """
        return self.dir_ops.calculate_directory_size(path)
    
    def delete_directory_recursive(self, path: str, dry_run: bool = True,
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
        return self.dir_ops.delete_directory_recursive(path, dry_run, sudo)
    
    def batch_delete_by_pattern(self, path: str, pattern: str, dry_run: bool = True,
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
        return self.dir_ops.batch_delete_by_pattern(path, pattern, dry_run, sudo)
    
    def safe_move_or_rename(self, source: str, destination: str, overwrite: bool = False,
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
        return self.dir_ops.safe_move_or_rename(source, destination, overwrite, sudo)
    
    def list_directory_recursive(self, path: str, max_depth: Optional[int] = None,
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
        return self.dir_ops.list_directory_recursive(path, max_depth, sudo)
    
    def create_archive_from_directory(self, source_path: str, archive_path: str,
                                    format: str = "tar.gz", sudo: bool = False) -> Dict[str, Any]:
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
        return self.dir_ops.create_archive_from_directory(source_path, archive_path, format, sudo)
    
    def extract_archive_to_directory(self, archive_path: str, destination_path: str,
                                   overwrite: bool = False, sudo: bool = False) -> Dict[str, Any]:
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
        return self.dir_ops.extract_archive_to_directory(archive_path, destination_path, overwrite, sudo)
    
    def search_file_contents(self, path: str, pattern: str, regex: bool = False,
                           case_sensitive: bool = True, sudo: bool = False) -> List[Dict[str, Any]]:
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
        return self.dir_ops.search_file_contents(path, pattern, regex, case_sensitive, sudo)
    
    def copy_directory_recursive(self, source_path: str, destination_path: str, overwrite: bool = False, 
                               preserve_symlinks: bool = True, preserve_permissions: bool = True, 
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
        return self.dir_ops.copy_directory_recursive(
            source_path, destination_path, overwrite, preserve_symlinks, preserve_permissions, sudo
        )

    def transfer_directory(self, direction: str, local_path: str, remote_path: str,
                          sudo: bool = False) -> Dict[str, Any]:
        """
        Transfer a directory between local and remote systems.

        Uses archive-based transfer for efficiency:
        - Upload: Creates archive locally, transfers via SFTP, extracts on remote
        - Download: Creates archive on remote, transfers via SFTP, extracts locally

        Args:
            direction: 'upload' (local to remote) or 'download' (remote to local)
            local_path: Path to local directory
            remote_path: Path to remote directory
            sudo: Use sudo for remote operations (extract/archive)

        Returns:
            Dict with transfer status and metadata
        """
        from cygnus_ssh_mcp.ops.file import create_local_archive, extract_local_archive

        # Determine archive format based on remote OS
        if self.os_type == 'windows':
            archive_format = 'zip'
            archive_ext = '.zip'
        else:
            archive_format = 'tar.gz'
            archive_ext = '.tar.gz'

        timestamp = int(time.time())
        local_temp_archive = None
        remote_temp_archive = None

        try:
            if direction == 'upload':
                # Upload: archive locally -> transfer -> extract remotely
                self._logger.info(f"Uploading directory {local_path} to {remote_path}")

                # Validate local source exists
                if not os.path.isdir(local_path):
                    return {
                        'success': False,
                        'error': f"Local directory does not exist: {local_path}"
                    }

                # Create local archive
                local_temp_archive = create_local_archive(local_path, archive_format)
                archive_size = os.path.getsize(local_temp_archive)

                # Determine remote temp path
                if self.os_type == 'windows':
                    # Use PowerShell to get TEMP path (CMD doesn't understand $env:TEMP)
                    handle = self.run(_powershell_encoded_command('Write-Output $env:TEMP'), io_timeout=10,
                                       origin='tool_internal', parent_tool='ssh_dir_transfer')
                    temp_dir = handle.get_full_output().strip()
                    remote_temp_archive = f"{temp_dir}\\ssh_dir_transfer_{timestamp}{archive_ext}"
                else:
                    remote_temp_archive = f"/tmp/ssh_dir_transfer_{timestamp}{archive_ext}"

                # Transfer archive to remote
                self._logger.info(f"Transferring archive ({archive_size} bytes) to {remote_temp_archive}")
                self.put(local_temp_archive, remote_temp_archive)

                # Extract on remote
                self._logger.info(f"Extracting archive to {remote_path}")
                extract_result = self.extract_archive_to_directory(
                    remote_temp_archive, remote_path, overwrite=True, sudo=sudo
                )

                if not extract_result.get('success'):
                    return {
                        'success': False,
                        'error': extract_result.get('message', 'Failed to extract archive on remote')
                    }

                # Count files from extraction result
                files_count = len(extract_result.get('extracted_files', []))

                return {
                    'success': True,
                    'operation': 'upload',
                    'local_path': local_path,
                    'remote_path': remote_path,
                    'archive_format': archive_format,
                    'files_transferred': files_count,
                    'bytes_transferred': archive_size
                }

            elif direction == 'download':
                # Download: archive remotely -> transfer -> extract locally
                self._logger.info(f"Downloading directory {remote_path} to {local_path}")

                # Determine remote temp archive path
                if self.os_type == 'windows':
                    # Use PowerShell to get TEMP path (CMD doesn't understand $env:TEMP)
                    handle = self.run(_powershell_encoded_command('Write-Output $env:TEMP'), io_timeout=10,
                                       origin='tool_internal', parent_tool='ssh_dir_transfer')
                    temp_dir = handle.get_full_output().strip()
                    remote_temp_archive = f"{temp_dir}\\ssh_dir_transfer_{timestamp}{archive_ext}"
                else:
                    remote_temp_archive = f"/tmp/ssh_dir_transfer_{timestamp}{archive_ext}"

                # Create archive on remote
                self._logger.info(f"Creating archive on remote: {remote_temp_archive}")
                archive_result = self.create_archive_from_directory(
                    remote_path, remote_temp_archive, format=archive_format, sudo=sudo
                )

                if not archive_result.get('success') and archive_result.get('status') != 'success':
                    return {
                        'success': False,
                        'error': archive_result.get('message', 'Failed to create archive on remote')
                    }

                # For sudo, make archive readable before download
                if sudo and self.os_type != 'windows':
                    self.run(f"chmod 644 {shlex.quote(remote_temp_archive)}", sudo=True,
                             origin='tool_internal', parent_tool='ssh_dir_transfer')

                # Create local temp file for download
                fd, local_temp_archive = tempfile.mkstemp(suffix=archive_ext, prefix='ssh_dir_transfer_')
                os.close(fd)

                # Transfer archive to local
                archive_size = archive_result.get('size_bytes', 0)
                self._logger.info(f"Transferring archive ({archive_size} bytes) to local")
                self.get(remote_temp_archive, local_temp_archive)

                # Get actual size if not reported
                if archive_size <= 0:
                    archive_size = os.path.getsize(local_temp_archive)

                # Extract locally
                self._logger.info(f"Extracting archive to {local_path}")
                extract_result = extract_local_archive(local_temp_archive, local_path, archive_format)

                return {
                    'success': True,
                    'operation': 'download',
                    'local_path': local_path,
                    'remote_path': remote_path,
                    'archive_format': archive_format,
                    'files_transferred': extract_result.get('files_extracted', 0),
                    'bytes_transferred': archive_size
                }

            else:
                return {
                    'success': False,
                    'error': f"Invalid direction: {direction}. Must be 'upload' or 'download'."
                }

        except SshError as e:
            self._logger.error(f"Directory transfer failed: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }
        except Exception as e:
            self._logger.error(f"Unexpected error during directory transfer: {e}", exc_info=True)
            return {
                'success': False,
                'error': f"Unexpected error: {e}"
            }
        finally:
            # Cleanup local temp archive
            if local_temp_archive and os.path.exists(local_temp_archive):
                try:
                    os.unlink(local_temp_archive)
                    self._logger.debug(f"Cleaned up local temp archive: {local_temp_archive}")
                except Exception as e:
                    self._logger.warning(f"Failed to clean up local temp archive: {e}")

            # Cleanup remote temp archive
            if remote_temp_archive:
                try:
                    if self.os_type == 'windows':
                        ps_path = remote_temp_archive.replace("'", "''")
                        self.run(_powershell_encoded_command(f"Remove-Item -Path '{ps_path}' -Force -ErrorAction SilentlyContinue"),
                                io_timeout=10, runtime_timeout=30,
                                origin='tool_internal', parent_tool='ssh_dir_transfer')
                    else:
                        self.run(f"rm -f {shlex.quote(remote_temp_archive)}",
                                io_timeout=10, runtime_timeout=30, sudo=sudo,
                                origin='tool_internal', parent_tool='ssh_dir_transfer')
                    self._logger.debug(f"Cleaned up remote temp archive: {remote_temp_archive}")
                except Exception as e:
                    self._logger.warning(f"Failed to clean up remote temp archive: {e}")

    # _build_cmd helper removed as logic is inlined or handled directly
