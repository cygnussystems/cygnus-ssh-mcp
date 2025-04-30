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
    def __init__(self, pid):
        super().__init__(f"No running task with PID: {pid}")
        self.pid = pid


class CommandHandle:
    """
    Tracks the state and output of a single SSH command execution.
    """
    def __init__(self, handle_id, cmd, tail_keep=100):
        self.id = handle_id
        self.cmd = cmd
        self.start_ts = datetime.utcnow()
        self.end_ts = None
        self.exit_code = None
        self.running = True
        self.total_lines = 0
        self.truncated = False
        self._buf = deque(maxlen=tail_keep)

    def tail(self, n=50):
        """Return the last n lines of output so far."""
        return list(self._buf)[-n:]

    def chunk(self, start, length=50):
        """Return `length` lines starting at zero-based index `start`."""
        if start < 0 or start >= self.total_lines:
            raise ValueError(f"Start {start} out of range (total {self.total_lines})")
        # if truncated and requested start is before buffer, raise
        buf_list = list(self._buf)
        buf_start = max(0, self.total_lines - len(buf_list))
        if start < buf_start:
            raise OutputPurged(self.id)
        idx = start - buf_start
        return buf_list[idx:idx+length]

    def info(self):
        """Return metadata about the command."""
        return {
            "id": self.id,
            "cmd": self.cmd,
            "start_ts": self.start_ts.isoformat() + 'Z',
            "end_ts": self.end_ts.isoformat() + 'Z' if self.end_ts else None,
            "exit_code": self.exit_code,
            "running": self.running,
            "total_lines": self.total_lines,
            "truncated": self.truncated
        }


class SshClient:
    """
    SSH manager for running commands, transferring files, and tracking history.
    """
    def __init__(self, host, user, port=22, keyfile=None, password=None, connect_timeout=10):
        self.host = host
        self.user = user
        self.port = port
        self.keyfile = keyfile
        self.password = password
        self.connect_timeout = connect_timeout
        self._busy = False
        self._history = {}
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
        self._client.connect(**kwargs)

    def close(self):
        """Close the SSH connection."""
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def run(self, cmd, timeout=None, sudo=False, tail_keep=100):
        """
        Execute a command synchronously, streaming output into a CommandHandle.
        Returns the CommandHandle.
        """
        if self._busy:
            raise BusyError()
        self._busy = True
        full_cmd = self._build_cmd(cmd, sudo)
        chan = self._client.get_transport().open_session()
        chan.exec_command(full_cmd)
        if timeout:
            chan.settimeout(timeout)
        stdout = chan.makefile('r')
        stderr = chan.makefile_stderr('r')

        handle_id = self._next_id
        self._next_id += 1
        handle = CommandHandle(handle_id, cmd, tail_keep)
        self._history[handle_id] = handle

        # Read output
        while True:
            try:
                line = stdout.readline()
                if not line:
                    if chan.exit_status_ready():
                        break
                    continue
                handle.total_lines += 1
                if handle.id > 10 and handle.total_lines > tail_keep:
                    handle.truncated = True
                handle._buf.append(line)
            except socket.timeout:
                # time slice expired; check if done
                if chan.exit_status_ready():
                    break
                continue

        handle.exit_code = chan.recv_exit_status()
        handle.end_ts = datetime.utcnow()
        handle.running = False
        self._busy = False

        if handle.exit_code != 0:
            stdout_all = ''.join(handle.tail(handle.total_lines))
            stderr_all = stderr.read()
            raise CommandFailed(handle.exit_code, stdout_all, stderr_all)

        return handle

    def launch(self, cmd, sudo=False):
        """
        Launch a background command and return a CommandHandle with PID as id.
        """
        full_cmd = self._build_cmd(cmd + ' & echo $!', sudo)
        stdin, stdout, stderr = self._client.exec_command(full_cmd)
        pid_str = stdout.read().strip()
        try:
            pid = int(pid_str)
        except ValueError:
            raise SshError(f"Failed to parse PID: {pid_str}")

        handle_id = self._next_id
        self._next_id += 1
        handle = CommandHandle(handle_id, cmd)
        handle.exit_code = None
        handle.running = True
        handle.total_lines = 0
        self._history[handle_id] = handle
        return handle

    def output(self, handle_id, mode='tail', n=50, start=None):
        """Retrieve output from a previous CommandHandle."""
        handle = self._history.get(handle_id)
        if not handle:
            raise TaskNotFound(handle_id)
        if mode == 'tail':
            return handle.tail(n)
        elif mode == 'chunk':
            if start is None:
                raise ValueError("`start` is required for chunk mode")
            return handle.chunk(start, n)
        else:
            raise ValueError(f"Unknown mode: {mode}")

    def get(self, remote_path, local_path):
        """Download a file from remote to local."""
        sftp = self._client.open_sftp()
        sftp.get(remote_path, local_path)
        sftp.close()

    def put(self, local_path, remote_path):
        """Upload a file from local to remote."""
        sftp = self._client.open_sftp()
        sftp.put(local_path, remote_path)
        sftp.close()

    def replace_line(self, remote_file, old_line, new_line, count=1):
        """Replace occurrences of a line in a remote text file."""
        tmp = tempfile.NamedTemporaryFile(delete=False)
        self.get(remote_file, tmp.name)
        lines = tmp.read().decode().splitlines(keepends=True)
        tmp.close()

        replaced = 0
        for i, line in enumerate(lines):
            if old_line in line and replaced < count:
                lines[i] = line.replace(old_line, new_line)
                replaced += 1
                if replaced >= count:
                    break

        with open(tmp.name, 'w') as f:
            f.writelines(lines)
        self.put(tmp.name, remote_file)
        os.unlink(tmp.name)

    def replace_block(self, remote_file, old_block, new_block):
        """Replace a block of lines in a remote text file."""
        tmp = tempfile.NamedTemporaryFile(delete=False)
        self.get(remote_file, tmp.name)
        text = tmp.read().decode()
        tmp.close()

        new_text = text.replace("".join(old_block), "".join(new_block))
        with open(tmp.name, 'w') as f:
            f.write(new_text)
        self.put(tmp.name, remote_file)
        os.unlink(tmp.name)

    def reboot(self, wait=True, timeout=300):
        """Reboot the remote host and optionally wait until it comes back."""
        self.run('reboot', sudo=True)
        self.close()
        start = time.time()
        if not wait:
            return
        while True:
            if time.time() - start > timeout:
                raise CommandTimeout(timeout)
            try:
                self._connect()
                return
            except Exception:
                time.sleep(5)

    def status(self):
        """Return a snapshot of system state."""
        def _quick(cmd):
            try:
                h = self.run(cmd, timeout=1)
                lines = h.tail(1)
                return lines[0].strip() if lines else ''
            except Exception:
                return 'n/a'

        return {
            'user':      _quick('whoami'),
            'cwd':       _quick('pwd'),
            'time':      _quick('date -Is'),
            'os':        _quick("bash -c '\n  [ -f /etc/os-release ] && . /etc/os-release && echo \"${NAME} ${VERSION_ID}\" || uname -srm'"),
            'host':      _quick('hostname'),
            'uptime':    _quick('uptime -p'),
            'load_avg':  _quick("cut -d' ' -f1-3 /proc/loadavg"),
            'free_disk': _quick("df -h / | awk 'NR==2{print $4}'"),
            'mem_free':  _quick("free -m | awk '/^Mem:/{print $4 \" MB\"}'")
        }

    def history(self):
        """Return metadata for recent CommandHandles."""
        return [h.info() for h in self._history.values()]

    def _build_cmd(self, cmd, sudo):
        """Internal: wrap command with sudo if requested."""
        if sudo:
            return f"sudo -n bash -c {shlex.quote(cmd)}"
        return cmd
