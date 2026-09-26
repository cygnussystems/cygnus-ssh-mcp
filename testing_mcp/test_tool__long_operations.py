"""Regression tests for long-running tool operations (2026-09-26 OpenCode retest W2/W3,
WS4 of planning/2026-09-26-retest-fix-plan.md).

Before: every tool except ssh_cmd_run ran on the event loop with no per-call limit, so an
archive/transfer/search that took longer than the client's ~60s limit lost its result, and
the whole server stopped answering (even ssh_conn_is_connected) until it finished.

To make "an operation that outlasts the cap" deterministic, these tests shrink the cap to
2s and slow the underlying client method down - they test the server's mechanism, not the
target's speed. (The real-world repro - an 18,001-file archive on Windows - is a manual
acceptance check for the tester.)
"""
import pytest
import json
import asyncio
import logging
import time
from conftest import (
    print_test_header, print_test_footer, make_connection, disconnect_ssh,
    mcp_test_environment, extract_result_text, skip_on_windows
)

from cygnus_ssh_mcp import server
from cygnus_ssh_mcp.models import SshError
from cygnus_ssh_mcp.server import mcp
from fastmcp import Client

logger = logging.getLogger(__name__)

CAP = 2.0
SLOW = 6.0


def _json(result):
    return json.loads(extract_result_text(result))


def _unwrap(result):
    items = result.content if hasattr(result, 'content') else result
    parsed = [json.loads(item.text) for item in items]
    if len(parsed) == 1 and isinstance(parsed[0], dict) and set(parsed[0]) == {'result'}:
        return parsed[0]['result']
    return parsed[0] if len(parsed) == 1 else parsed


def _slow_down(monkeypatch, method_name, delay=SLOW, fail=False):
    """Make mcp.ssh_client.<method_name> take `delay` seconds (and optionally fail)."""
    original = getattr(mcp.ssh_client, method_name)

    def slow(*args, **kwargs):
        time.sleep(delay)
        if fail:
            raise SshError("simulated failure after a long operation")
        return original(*args, **kwargs)

    monkeypatch.setattr(mcp.ssh_client, method_name, slow)


async def _wait_for_operation(client, handle_id, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = _json(await client.call_tool("ssh_cmd_check_status",
                                              {"handle_id": handle_id, "wait_seconds": 1}))
        if status['status'] != 'running':
            return status
    raise AssertionError(f"operation {handle_id} still running after {timeout}s")


@pytest.mark.asyncio
@skip_on_windows
async def test_long_operation_hands_off_and_server_stays_responsive(mcp_test_environment, monkeypatch):
    """A slow tool returns in_progress at the cap; status tools answer at once meanwhile;
    history lists it; check_status returns its full normal result when done."""
    print_test_header("Testing long operation handoff")
    monkeypatch.setattr(server, 'max_foreground_wait', CAP)

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            _slow_down(monkeypatch, 'calculate_directory_size')

            start = time.monotonic()
            first = _json(await client.call_tool("ssh_dir_calc_size", {"path": "/tmp"}))
            assert time.monotonic() - start < CAP + 3, "didn't hand off at the cap"
            assert first['status'] == 'in_progress', first
            handle_id = first['handle_id']
            assert first['tool'] == 'ssh_dir_calc_size' and f"handle_id={handle_id}" in first['next_step']

            # The server answers other (non-operation) calls immediately
            for tool, params in (("ssh_conn_is_connected", {}), ("ssh_host_list", {}),
                                 ("ssh_task_status", {"pid": 1})):
                t0 = time.monotonic()
                await client.call_tool(tool, params)
                assert time.monotonic() - t0 < 1.5, f"{tool} was blocked by the running operation"

            history = _unwrap(await client.call_tool("ssh_cmd_history", {"include_internal": False}))
            entry = next(e for e in history if e['id'] == handle_id)
            assert entry['origin'] == 'operation' and entry['end_time'] is None, entry

            done = await _wait_for_operation(client, handle_id)
            assert done['status'] == 'completed', done
            assert done['result']['path'] == '/tmp' and 'size_bytes' in done['result'], done
        finally:
            await disconnect_ssh(client)
            print_test_footer()


@pytest.mark.asyncio
@skip_on_windows
async def test_second_operation_is_refused_while_one_runs(mcp_test_environment, monkeypatch):
    """One operation at a time: another operation, or ssh_cmd_run, fails fast with 'busy'
    naming the running handle - it isn't queued (a queued call would time out anyway)."""
    print_test_header("Testing one operation at a time")
    monkeypatch.setattr(server, 'max_foreground_wait', CAP)

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            _slow_down(monkeypatch, 'calculate_directory_size')
            first = _json(await client.call_tool("ssh_dir_calc_size", {"path": "/tmp"}))
            handle_id = first['handle_id']

            t0 = time.monotonic()
            with pytest.raises(Exception) as exc_info:
                await client.call_tool("ssh_file_stat", {"path": "/tmp"})
            assert time.monotonic() - t0 < 1.5
            message = str(exc_info.value)
            assert "busy" in message and f"handle_id={handle_id}" in message, message

            run = _json(await client.call_tool("ssh_cmd_run", {"command": "echo hi"}))
            assert run['status'] == 'busy' and f"handle_id={handle_id}" in run['error'], run

            await _wait_for_operation(client, handle_id)
            # ...and after it finishes, everything works again
            assert _json(await client.call_tool("ssh_cmd_run", {"command": "echo hi"}))['status'] == 'success'
        finally:
            await disconnect_ssh(client)
            print_test_footer()


@pytest.mark.asyncio
@skip_on_windows
async def test_failed_long_operation_reports_error(mcp_test_environment, monkeypatch):
    """A handed-off operation that fails reports status 'failed' with the real error."""
    print_test_header("Testing failed long operation")
    monkeypatch.setattr(server, 'max_foreground_wait', CAP)

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            _slow_down(monkeypatch, 'calculate_directory_size', delay=4, fail=True)
            first = _json(await client.call_tool("ssh_dir_calc_size", {"path": "/tmp"}))
            done = await _wait_for_operation(client, first['handle_id'])
            assert done['status'] == 'failed' and "simulated failure" in done['error'], done
        finally:
            await disconnect_ssh(client)
            print_test_footer()


@pytest.mark.asyncio
@skip_on_windows
async def test_list_tool_in_progress_then_list_result(mcp_test_environment, monkeypatch):
    """A list-returning tool (content search) can hand off too; its result is the list."""
    print_test_header("Testing list tool handoff")
    monkeypatch.setattr(server, 'max_foreground_wait', CAP)

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            _slow_down(monkeypatch, 'search_file_contents')
            first = _unwrap(await client.call_tool("ssh_dir_search_files_content", {
                "dir_path": "/tmp", "pattern": f"never-matches-{int(time.time())}"}))
            assert first['status'] == 'in_progress', first
            done = await _wait_for_operation(client, first['handle_id'])
            assert done['status'] == 'completed' and done['result'] == [], done
        finally:
            await disconnect_ssh(client)
            print_test_footer()


@pytest.mark.asyncio
async def test_fast_operation_returns_directly(mcp_test_environment):
    """A quick tool returns its normal result directly and leaves no operation behind."""
    print_test_header("Testing fast operation")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            before = set(server._operations)
            stat = _json(await client.call_tool("ssh_conn_status", {}))
            assert stat.get('connected') is True, stat
            assert set(server._operations) == before
        finally:
            await disconnect_ssh(client)
            print_test_footer()
