"""Stale / lost connections (round-4 issue 2, issues/_archive_/2026-09-27-stale-connection-health-after-idle.md).

Seen in OpenCode after an overnight idle: ssh_conn_is_connected said true, while the next
call failed with "[WinError 10054] An existing connection was forcibly closed" and gave
no next step.

- test_frozen_connection_is_reported_dead reproduces a SILENTLY dead connection: the
  server-side sshd process for our session is frozen (SIGSTOP), so the TCP connection
  stays open but nothing answers - paramiko's local flag still says "active". The old
  ssh_conn_is_connected returned True here.
- test_killed_connection_gives_connection_lost: the session's sshd is killed; the next
  calls must say CONNECTION_LOST with the reconnect step, and reconnecting must work.

The sshd session process is found as the parent of a command's shell, and frozen/killed
from a separate, independent connection (conftest.paramiko_verify). Linux/macOS only.
"""
import pytest
import json
import time
import logging
from conftest import (
    print_test_header, print_test_footer, make_connection, disconnect_ssh,
    mcp_test_environment, extract_result_text, skip_on_windows, paramiko_verify,
    SSH_TEST_PASSWORD, TEST_WORKSPACE
)

from cygnus_ssh_mcp import server
from cygnus_ssh_mcp.server import mcp
from fastmcp import Client

logger = logging.getLogger(__name__)


def _json(result):
    return json.loads(extract_result_text(result))


async def _session_sshd_pid(client):
    run = _json(await client.call_tool("ssh_cmd_run", {"command": "ps -o ppid= -p $$"}))
    assert run['status'] == 'success', run
    return int(run['output'].strip())


def _signal(pid, sig):
    """Send a signal from the independent connection (sudo if the plain kill fails)."""
    code, _, err = paramiko_verify(f"kill -{sig} {pid}")
    if code != 0:
        code, _, err = paramiko_verify(f"echo '{SSH_TEST_PASSWORD}' | sudo -S -p '' kill -{sig} {pid}")
    assert code == 0, f"couldn't send SIG{sig} to {pid}: {err}"


async def _is_connected(client):
    result = _json(await client.call_tool("ssh_conn_is_connected", {}))
    return result['result'] if isinstance(result, dict) else result


@pytest.mark.asyncio
@skip_on_windows
async def test_frozen_connection_is_reported_dead(mcp_test_environment):
    print_test_header("Testing a silently dead (frozen) connection")
    async with Client(mcp) as client:
        pid = None
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            pid = await _session_sshd_pid(client)
            _signal(pid, "STOP")
            assert mcp.ssh_client.is_connected(), "precondition: paramiko still thinks it's active"

            start = time.monotonic()
            assert await _is_connected(client) is False, "a frozen connection was reported as working"
            assert time.monotonic() - start < 10

            with pytest.raises(Exception) as exc_info:
                await client.call_tool("ssh_file_stat", {"path": TEST_WORKSPACE})
            message = str(exc_info.value)
            assert "ssh_conn_connect" in message and "previous connection was lost" in message, message
        finally:
            if pid:
                _signal(pid, "KILL")
            await disconnect_ssh(client)
            print_test_footer()


@pytest.mark.asyncio
@skip_on_windows
async def test_killed_connection_gives_connection_lost(mcp_test_environment):
    print_test_header("Testing a killed connection")
    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            _signal(await _session_sshd_pid(client), "KILL")
            time.sleep(1)

            # An operation tool: one clear CONNECTION_LOST error with the next steps
            # (ssh_file_stat returns its errors in the response rather than raising)
            stat = _json(await client.call_tool("ssh_file_stat", {"path": TEST_WORKSPACE}))
            assert stat.get('error_type') == 'connection_lost', stat
            assert "CONNECTION_LOST" in stat['error'] and "ssh_conn_connect" in stat['error'], stat
            assert 'exists' not in stat, "a lost connection must not look like 'file does not exist'"
            assert stat['path'] == TEST_WORKSPACE
            assert await _is_connected(client) is False

            # Reconnect works, and a command runs normally again
            assert await make_connection(client), "reconnect failed"
            run = _json(await client.call_tool("ssh_cmd_run", {"command": "echo back"}))
            assert run['status'] == 'success' and 'back' in run['output'], run
        finally:
            await disconnect_ssh(client)
            print_test_footer()


@pytest.mark.asyncio
@skip_on_windows
async def test_cmd_run_on_killed_connection_says_connection_lost(mcp_test_environment):
    print_test_header("Testing ssh_cmd_run on a killed connection")
    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            _signal(await _session_sshd_pid(client), "KILL")
            time.sleep(1)
            run = _json(await client.call_tool("ssh_cmd_run", {"command": "echo hi"}))
            assert run['status'] == 'error', run
            assert run.get('error_type') == 'connection_lost' or "previous connection was lost" in run['error'], run
            assert "ssh_conn_connect" in run['error'], run
        finally:
            await disconnect_ssh(client)
            print_test_footer()
