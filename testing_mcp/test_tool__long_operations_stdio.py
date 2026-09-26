"""Long operations through a REAL client/server boundary (gap #1 of the WS4 test review).

The server runs as a separate stdio process (via slow_server.py), exactly as OpenCode,
Claude Code etc. run it, and the MCP client uses a real per-request timeout - the thing
that caused the original bugs. Checks:

- the in_progress handoff arrives BEFORE the client's timeout, and the result is then
  retrievable with ssh_cmd_check_status
- if the client gives up on a request (times out) before any handoff, the operation keeps
  running, the server stays responsive, and the result can still be found (ssh_cmd_history)
  and collected (ssh_cmd_check_status)
"""
import os
import sys
import json
import time
import shutil
import tempfile
import logging
from pathlib import Path

import pytest
from conftest import (
    print_test_header, print_test_footer, mcp_test_environment,
    SSH_TEST_HOST, SSH_TEST_PORT, SSH_TEST_USER, SSH_TEST_PASSWORD, TEST_WORKSPACE
)
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

logger = logging.getLogger(__name__)

SLOW_SERVER = str(Path(__file__).resolve().parent / "slow_server.py")


def _json(result):
    return json.loads(result.content[0].text)


def _stdio_client(config_path, max_wait, slow_seconds):
    env = dict(os.environ)
    env["MCP_SSH_TEST_SLOW_SECONDS"] = str(slow_seconds)
    transport = StdioTransport(
        command=sys.executable,
        args=[SLOW_SERVER, "--config", str(config_path), "--max-wait", str(max_wait)],
        env=env,
    )
    return Client(transport)


async def _connect(client):
    await client.call_tool("ssh_conn_add_host", {
        "user": SSH_TEST_USER, "host": SSH_TEST_HOST, "password": SSH_TEST_PASSWORD,
        "port": SSH_TEST_PORT, "sudo_password": SSH_TEST_PASSWORD,
    }, timeout=30)
    connected = _json(await client.call_tool(
        "ssh_conn_connect", {"host_name": f"{SSH_TEST_USER}@{SSH_TEST_HOST}"}, timeout=60))
    assert connected.get('status') == 'success', connected


async def _poll(client, handle_id, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = _json(await client.call_tool(
            "ssh_cmd_check_status", {"handle_id": handle_id, "wait_seconds": 2}, timeout=10))
        if status['status'] != 'running':
            return status
    raise AssertionError(f"operation {handle_id} still running after {timeout}s")


@pytest.fixture
def temp_config():
    """A throwaway host config for the subprocess server (never the user's real one)."""
    tmp = tempfile.mkdtemp(prefix="mcp_ssh_stdio_test_")
    yield Path(tmp) / "hosts.toml"
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_handoff_arrives_before_client_timeout(mcp_test_environment, temp_config):
    """Server cap 3s, operation 10s, client timeout 8s: the client gets in_progress (not a
    timeout), other calls answer meanwhile, and the result is collected afterwards."""
    print_test_header("Testing handoff before a real client timeout (stdio)")

    async with _stdio_client(temp_config, max_wait=3, slow_seconds=10) as client:
        await _connect(client)

        start = time.monotonic()
        first = _json(await client.call_tool("ssh_dir_calc_size", {"path": TEST_WORKSPACE}, timeout=8))
        assert time.monotonic() - start < 7
        assert first['status'] == 'in_progress', first

        t0 = time.monotonic()
        assert _json(await client.call_tool("ssh_conn_is_connected", {}, timeout=3)) in (True, {'result': True})
        assert time.monotonic() - t0 < 2, "server not responsive while the operation runs"

        done = await _poll(client, first['handle_id'])
        assert done['status'] == 'completed' and 'size_bytes' in done['result'], done
    print_test_footer()


@pytest.mark.asyncio
async def test_abandoned_request_result_is_recoverable(mcp_test_environment, temp_config):
    """Server cap 60s (so no handoff), operation 8s, client timeout 2s: the client gives up,
    but the operation finishes, the server stays responsive, and the result can be found in
    ssh_cmd_history and collected with ssh_cmd_check_status."""
    print_test_header("Testing an abandoned request (stdio)")

    async with _stdio_client(temp_config, max_wait=60, slow_seconds=8) as client:
        await _connect(client)

        with pytest.raises(Exception) as exc_info:
            await client.call_tool("ssh_dir_calc_size", {"path": TEST_WORKSPACE}, timeout=2)
        logger.info(f"client gave up as intended: {exc_info.value!r}")

        t0 = time.monotonic()
        history = _json(await client.call_tool(
            "ssh_cmd_history", {"include_internal": False}, timeout=3))
        assert time.monotonic() - t0 < 2, "server not responsive after the abandoned request"
        entries = history['result'] if isinstance(history, dict) else history
        ops = [e for e in entries if e.get('origin') == 'operation' and e.get('parent_tool') == 'ssh_dir_calc_size']
        assert ops, f"abandoned operation not discoverable in history: {entries}"

        done = await _poll(client, ops[-1]['id'])
        assert done['status'] == 'completed' and 'size_bytes' in done['result'], done
    print_test_footer()
