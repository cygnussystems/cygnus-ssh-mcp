"""Limited-platform suite: Alpine (BusyBox), FreeBSD (flex) and OpenWrt (BusyBox, Dropbear,
no sudo, no SFTP, no bash).

The main suite (testing_mcp/) assumes sudo, bash, SFTP and GNU tools, so it can't simply
be pointed at these hosts. This suite instead covers the behaviors that broke on them
(issues/_archive_/2026-09-28-*) and checks each host against what it is known to support.

It deliberately has its own conftest - testing_mcp/conftest.py wipes the Debian test
workspace on import, which would collide with a main-suite run on that VM.

The Synology NAS is a PRODUCTION machine: its tests only work inside a uniquely named scratch
folder in the login user's home directory, and never use sudo (production=True).

Credentials: testing_mcp/.env, per host (hosts without them are skipped):
    ALPINE_SSH_HOST / _USER / _PASSWORD  [/ _PORT, _SUDO_PASSWORD (default: _PASSWORD)]
    FREEBSD_SSH_HOST / ...
    OPENWRT_SSH_HOST / ...
    SYNOLOGY_SSH_HOST / ...
"""
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', 'testing_mcp', '.env'))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from fastmcp import Client  # noqa: E402
from cygnus_ssh_mcp import server as _server  # noqa: E402
from cygnus_ssh_mcp.host_manager import SshHostManager  # noqa: E402

# Throwaway host config, never the user's real ~/.mcp_ssh_hosts.toml
_server.host_manager = _server._default_host_manager = SshHostManager(
    config_path=Path(tempfile.mkdtemp(prefix="mcp_ssh_limited_hosts_")) / "mcp_ssh_hosts.toml"
)


@dataclass(frozen=True)
class LimitedHost:
    """What each host is known to support - the tests check the server agrees."""
    name: str
    os_type: str      # what the server should detect
    has_sudo: bool
    has_sftp: bool    # usable SFTP (Synology's share-only SFTP doesn't count)
    production: bool = False  # scratch only in the home dir, no sudo tests


KNOWN_HOSTS = [
    LimitedHost('alpine', os_type='linux', has_sudo=False, has_sftp=True),
    LimitedHost('freebsd', os_type='flex', has_sudo=True, has_sftp=True),
    LimitedHost('openwrt', os_type='linux', has_sudo=False, has_sftp=False),
    LimitedHost('synology', os_type='linux', has_sudo=True, has_sftp=False, production=True),
]


def _credentials(name):
    prefix = f"{name.upper()}_SSH_"
    host, user, password = (os.environ.get(prefix + k) for k in ('HOST', 'USER', 'PASSWORD'))
    if not (host and user and password):
        return None
    return {"host": host, "user": user, "password": password,
            "port": int(os.environ.get(prefix + 'PORT', 22)),
            "sudo_password": os.environ.get(prefix + 'SUDO_PASSWORD', password)}


def _json(result):
    return json.loads(result.content[0].text)


class Session:
    """A connected MCP client for one host, plus a fresh scratch directory on it."""

    def __init__(self, client, host: LimitedHost, workdir: str, connect_result: dict):
        self.client, self.host, self.workdir, self.connect_result = client, host, workdir, connect_result

    async def call(self, tool, params=None):
        """Tool result as a dict/list; a tool-level exception is returned as {'EXCEPTION': msg}."""
        try:
            return _json(await self.client.call_tool(tool, params or {}))
        except Exception as e:
            return {'EXCEPTION': str(e)}

    async def sh(self, command, use_sudo=False):
        """Run a shell command, asserting it succeeds; returns its output."""
        result = await self.call("ssh_cmd_run", {"command": command, "use_sudo": use_sudo})
        assert result.get('status') == 'success', f"setup command failed: {command!r} -> {result}"
        return result.get('output', '')


@pytest.fixture(params=KNOWN_HOSTS, ids=lambda h: h.name)
def limited_host(request):
    host = request.param
    if _credentials(host.name) is None:
        pytest.skip(f"no {host.name.upper()}_SSH_HOST/_USER/_PASSWORD in testing_mcp/.env")
    return host


@pytest_asyncio.fixture
async def session(limited_host):
    creds = _credentials(limited_host.name)
    async with Client(_server.mcp) as client:
        await client.call_tool("ssh_conn_add_host", creds)
        connect_result = _json(await client.call_tool(
            "ssh_conn_connect", {"host_name": f"{creds['user']}@{creds['host']}"}))
        assert connect_result.get('status') == 'success', connect_result
        base = "/tmp"
        if limited_host.production:
            probe = _json(await client.call_tool("ssh_cmd_run", {"command": "pwd"}))
            base = probe.get('output', '').strip()
            assert base.startswith('/') and base.count('/') >= 2, f"unexpected home dir: {probe}"
        workdir = f"{base}/mcp_limited_{limited_host.name}_{int(time.time() * 1000)}"
        s = Session(client, limited_host, workdir, connect_result)
        await s.sh(f"rm -rf {workdir}; mkdir -p {workdir} && chmod 755 {workdir}")
        try:
            yield s
        finally:
            await s.call("ssh_cmd_run", {"command": f"rm -rf {workdir}",
                                         "use_sudo": limited_host.has_sudo and not limited_host.production})
            await s.call("ssh_host_disconnect", {})
