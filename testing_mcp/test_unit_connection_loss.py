"""Unit tests for connection-loss detection in server.py (no SSH needed)."""
import paramiko

from cygnus_ssh_mcp import server
from cygnus_ssh_mcp.models import SshError


def test_connection_level_errors_are_recognised():
    wrapped = SshError("Unexpected error during command execution: [WinError 10054] "
                       "An existing connection was forcibly closed by the remote host")
    for exc in (ConnectionResetError(10054, "forcibly closed"), EOFError(), BrokenPipeError(),
                paramiko.SSHException("SSH session not active"), wrapped,
                OSError("Socket is closed")):
        assert server._looks_like_connection_loss(exc), repr(exc)


def test_cause_chain_is_followed():
    try:
        try:
            raise ConnectionResetError("reset")
        except ConnectionResetError as inner:
            raise SshError("Unexpected error during command execution") from inner
    except SshError as outer:
        assert server._looks_like_connection_loss(outer)


def test_ordinary_errors_are_not_connection_loss():
    for exc in (ValueError("bad value"), SshError("File not found: /etc/x"),
                SshError("Command failed with exit code 1. Stderr: no such file"),
                PermissionError("Permission denied")):
        assert not server._looks_like_connection_loss(exc), repr(exc)


def test_connection_lost_message_names_host_and_next_steps():
    message = server._connection_lost_message("linux-test", "[WinError 10054] reset")
    assert message.startswith("CONNECTION_LOST")
    assert "ssh_conn_connect(host_name='linux-test')" in message
    assert "took effect before running it again" in message
