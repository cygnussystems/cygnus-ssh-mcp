from cygnus_ssh_mcp.ops.run import SshRunOperations_Linux


class _FakeStream:
    """Minimal stand-in for paramiko's exec_command file-like returns."""
    def __init__(self, data=b""):
        self._data = data

    def read(self):
        return self._data

    def close(self):
        pass


class _FakeChannel:
    def __init__(self, exit_status):
        self._exit_status = exit_status

    def recv_exit_status(self):
        return self._exit_status


class _FakeStdout(_FakeStream):
    def __init__(self, exit_status):
        super().__init__(b"")
        self.channel = _FakeChannel(exit_status)


class _FakeParamikoClient:
    """Stands in for ssh_client._client, whose exec_command _handle_sudo uses
    directly for its passwordless sudo pre-check."""
    def __init__(self, sudo_n_exit_code, sudo_n_stderr=b""):
        self._sudo_n_exit_code = sudo_n_exit_code
        self._sudo_n_stderr = sudo_n_stderr

    def exec_command(self, cmd, timeout=5):
        return _FakeStream(), _FakeStdout(self._sudo_n_exit_code), _FakeStream(self._sudo_n_stderr)


class _FakeSshClient:
    """Minimal stand-in - _handle_sudo only reads .capabilities/.sudo_password
    and ._client.exec_command (the passwordless sudo pre-check)."""
    def __init__(self, capabilities=None, sudo_password=None, sudo_n_exit_code=0, sudo_n_stderr=b""):
        self.capabilities = capabilities if capabilities is not None else {}
        self.sudo_password = sudo_password
        self._client = _FakeParamikoClient(sudo_n_exit_code, sudo_n_stderr)


def _make_ops(**kwargs):
    return SshRunOperations_Linux(_FakeSshClient(**kwargs))


def test_handle_sudo_passwordless_prefers_bash_when_present():
    ops = _make_ops(capabilities={'bash': True}, sudo_n_exit_code=0)
    cmd, pwd_attempted = ops._handle_sudo("whoami")
    assert cmd == "sudo -n bash -c whoami"
    assert pwd_attempted is False


def test_handle_sudo_passwordless_falls_back_to_sh_when_bash_confirmed_missing():
    """Regression test: live-verified on FreeBSD (sudo present, bash absent)
    that "sudo -n bash -c ..." fails with "sudo: bash: command not found" -
    sudo execs the named shell as its target command, independent of what
    shell is invoking sudo itself."""
    ops = _make_ops(capabilities={'bash': False}, sudo_n_exit_code=0)
    cmd, pwd_attempted = ops._handle_sudo("whoami")
    assert cmd == "sudo -n sh -c whoami"
    assert pwd_attempted is False


def test_handle_sudo_passwordless_prefers_bash_when_unconfirmed():
    """macOS/Windows never populate .capabilities (empty dict) - an
    unconfirmed capability must default to 'assume present', preserving
    today's exact behavior on those platforms."""
    ops = _make_ops(capabilities={}, sudo_n_exit_code=0)
    cmd, _ = ops._handle_sudo("whoami")
    assert cmd == "sudo -n bash -c whoami"


def test_handle_sudo_password_path_falls_back_to_sh_and_uses_portable_pipe():
    """Regression test: live-verified on FreeBSD that the old `<<<` here-string
    breaks under a plain POSIX sh login shell ("sh: Syntax error: redirection
    unexpected") - bash/zsh-only syntax, independent of whether bash exists
    on the target at all. Fixed with a portable printf | sudo -S pipe."""
    ops = _make_ops(
        capabilities={'bash': False}, sudo_password="secret",
        sudo_n_exit_code=1, sudo_n_stderr=b"sudo: a password is required"
    )
    cmd, pwd_attempted = ops._handle_sudo("whoami")
    assert pwd_attempted is True
    assert "<<<" not in cmd
    assert "bash" not in cmd
    assert cmd == "printf '%s\\n' secret | sudo -S -p '' sh -c whoami"


def test_handle_sudo_password_path_prefers_bash_when_present():
    ops = _make_ops(
        capabilities={'bash': True}, sudo_password="secret",
        sudo_n_exit_code=1, sudo_n_stderr=b"sudo: a password is required"
    )
    cmd, pwd_attempted = ops._handle_sudo("whoami")
    assert pwd_attempted is True
    assert "bash -c whoami" in cmd
    assert "<<<" not in cmd
