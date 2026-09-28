"""Hosts without an SFTP subsystem (issues/2026-09-28-openwrt-missing-sftp-*: Dropbear on
OpenWrt). Every SFTP-based file tool failed with a bare "EOF during negotiation", and
ssh_file_stat answered exists: false for a file that existed. Offline - no SSH host needed.
"""
import json
import pytest
import paramiko
from types import SimpleNamespace

from cygnus_ssh_mcp import server
from cygnus_ssh_mcp.client import SshClient
from cygnus_ssh_mcp.models import SshError
from fastmcp import Client


def _fake_client(capabilities, open_sftp_error=None):
    def open_sftp():
        if open_sftp_error:
            raise open_sftp_error
        return "sftp-session"
    return SimpleNamespace(capabilities=capabilities, _client=SimpleNamespace(open_sftp=open_sftp),
                           SFTP_UNAVAILABLE_MESSAGE=SshClient.SFTP_UNAVAILABLE_MESSAGE)


def test_open_sftp_gives_clear_error_when_probe_found_no_sftp():
    with pytest.raises(SshError, match="SFTP is not available on this host.*ssh_cmd_run"):
        SshClient.open_sftp(_fake_client({'sftp': False}))


@pytest.mark.parametrize("error", [paramiko.SSHException("EOF during negotiation"), EOFError()])
def test_open_sftp_translates_negotiation_failure(error):
    # Hosts that were never probed (macOS/Windows) or whose probe passed still get the clear message
    with pytest.raises(SshError, match="SFTP is not available on this host"):
        SshClient.open_sftp(_fake_client({}, open_sftp_error=error))


def test_open_sftp_passes_through_when_available():
    assert SshClient.open_sftp(_fake_client({'sftp': True})) == "sftp-session"


class _StatFailsClient:
    """Just enough of SshClient for ssh_file_stat: stat() fails the way it does without SFTP."""
    def stat(self, path):
        raise SshError(SshClient.SFTP_UNAVAILABLE_MESSAGE)

    def __getattr__(self, name):
        return None


@pytest.mark.asyncio
async def test_file_stat_never_reports_missing_when_the_check_failed(monkeypatch):
    monkeypatch.setattr(server.mcp, 'ssh_client', _StatFailsClient(), raising=False)
    async with Client(server.mcp) as client:
        result = await client.call_tool("ssh_file_stat", {"path": "/etc/config/network"})
    data = json.loads(result.content[0].text)
    assert data['exists'] is None, f"a failed check must not claim the file is missing: {data}"
    assert "SFTP is not available" in data['error'], data
