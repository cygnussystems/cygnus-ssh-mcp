from cygnus_ssh_mcp.client import SshClient


class _FakeStream:
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
    """Stands in for SshClient._client - only exec_command (for the raw
    sanity-check probe inside _describe_os_detection_failure) is used."""
    def __init__(self, sanity_exit=1, sanity_stderr=b""):
        self.sanity_exit = sanity_exit
        self.sanity_stderr = sanity_stderr

    def exec_command(self, cmd, timeout=5):
        return _FakeStream(), _FakeStdout(self.sanity_exit), _FakeStream(self.sanity_stderr)


def _make_client(sanity_exit=1, sanity_stderr=b""):
    """_describe_os_detection_failure only reads self._client - bypass
    __init__ (which requires a real network connection) via __new__."""
    client = SshClient.__new__(SshClient)
    client._client = _FakeParamikoClient(sanity_exit, sanity_stderr)
    return client


def test_describe_os_detection_failure_includes_raw_probe_details():
    client = _make_client(sanity_exit=127, sanity_stderr=b"command not found")
    message = client._describe_os_detection_failure(
        uname_result='', uname_exit=127, uname_stderr='command not found',
        win_result='', win_exit=1, ps_result='', ps_exit=1,
    )
    assert "'uname -s' -> exit=127" in message
    assert "command not found" in message
    assert "sanity check -> exit=127" in message


def test_describe_os_detection_failure_flags_permission_denied_pattern():
    """Regression test: live-confirmed on a Synology SRM router - SSH
    authentication succeeded, but every probe (including a bare echo) was
    rejected identically with "Permission denied, please try again.",
    including under an interactive PTY session, not just exec_command. This
    is a PAM account/session-phase rejection after successful auth, not a
    capability or shell-syntax gap - the diagnostic must call this out
    explicitly rather than leaving it as an opaque "detection failed"."""
    client = _make_client(
        sanity_exit=1, sanity_stderr=b"Permission denied, please try again."
    )
    message = client._describe_os_detection_failure(
        uname_result='', uname_exit=1, uname_stderr='Permission denied, please try again.',
        win_result='', win_exit=1, ps_result='', ps_exit=1,
    )
    assert "Likely cause" in message
    assert "account/device permission restriction" in message
    assert "2FA" in message


def test_describe_os_detection_failure_no_hint_without_permission_denied():
    client = _make_client(sanity_exit=127, sanity_stderr=b"sh: uname: not found")
    message = client._describe_os_detection_failure(
        uname_result='', uname_exit=127, uname_stderr='sh: uname: not found',
        win_result='', win_exit=1, ps_result='', ps_exit=1,
    )
    assert "Likely cause" not in message


def test_describe_os_detection_failure_handles_probe_exceptions():
    client = _make_client()
    message = client._describe_os_detection_failure(
        uname_probe_error=TimeoutError("timed out"),
        win_probe_error=ConnectionError("reset"),
    )
    assert "'uname -s' probe raised" in message
    assert "Windows detection probe raised" in message
