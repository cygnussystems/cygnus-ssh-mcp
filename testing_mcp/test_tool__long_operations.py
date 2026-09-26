"""Regression tests for long-running tool operations (2026-09-26 OpenCode retest W2/W3,
WS4 of planning/2026-09-26-retest-fix-plan.md). Run on all platforms.

Before: every tool except ssh_cmd_run ran on the event loop with no per-call limit, so an
archive/transfer/search that took longer than the client's ~60s limit lost its result, and
the whole server stopped answering (even ssh_conn_is_connected) until it finished.

Most tests shrink the cap to 2s and slow the underlying client method down, so they test
the server's mechanism deterministically. test_real_archive_roundtrip_through_handoff uses a
real archive instead. The stdio tests (real client timeout, separate server process) are in
test_tool__long_operations_stdio.py.
"""
import pytest
import json
import asyncio
import logging
import time
from conftest import (
    print_test_header, print_test_footer, make_connection, disconnect_ssh,
    mcp_test_environment, extract_result_text, TEST_WORKSPACE, PATH_SEP, IS_WINDOWS,
    sleep_then_echo, cleanup_command
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


async def _wait_for_operation(client, handle_id, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = _json(await client.call_tool("ssh_cmd_check_status",
                                              {"handle_id": handle_id, "wait_seconds": 1}))
        if status['status'] != 'running':
            return status
    raise AssertionError(f"operation {handle_id} still running after {timeout}s")


async def _result_or_poll(client, response):
    """A tool's final result, whether it came back directly or via the handoff."""
    if isinstance(response, dict) and response.get('status') == 'in_progress':
        done = await _wait_for_operation(client, response['handle_id'])
        assert done['status'] == 'completed', done
        return done['result']
    return response


# ---------------------------------------------------------------------------
# Core mechanism
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_long_operation_hands_off_and_server_stays_responsive(mcp_test_environment, monkeypatch):
    """A slow tool returns in_progress at the cap; status tools answer at once meanwhile;
    history lists it; check_status returns its full normal result when done."""
    print_test_header("Testing long operation handoff")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            monkeypatch.setattr(server, 'max_foreground_wait', CAP)
            _slow_down(monkeypatch, 'calculate_directory_size')

            start = time.monotonic()
            first = _json(await client.call_tool("ssh_dir_calc_size", {"path": TEST_WORKSPACE}))
            assert time.monotonic() - start < CAP + 3, "didn't hand off at the cap"
            assert first['status'] == 'in_progress', first
            handle_id = first['handle_id']
            assert first['tool'] == 'ssh_dir_calc_size' and f"handle_id={handle_id}" in first['next_step']

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
            assert done['result']['path'] == TEST_WORKSPACE and 'size_bytes' in done['result'], done
        finally:
            await disconnect_ssh(client)
            print_test_footer()


@pytest.mark.asyncio
async def test_second_operation_is_refused_while_one_runs(mcp_test_environment, monkeypatch):
    """One operation at a time: another operation, or ssh_cmd_run, fails fast with 'busy'
    naming the running handle - it isn't queued (a queued call would time out anyway)."""
    print_test_header("Testing one operation at a time")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            monkeypatch.setattr(server, 'max_foreground_wait', CAP)
            _slow_down(monkeypatch, 'calculate_directory_size')
            first = _json(await client.call_tool("ssh_dir_calc_size", {"path": TEST_WORKSPACE}))
            handle_id = first['handle_id']

            t0 = time.monotonic()
            with pytest.raises(Exception) as exc_info:
                await client.call_tool("ssh_file_stat", {"path": TEST_WORKSPACE})
            assert time.monotonic() - t0 < 1.5
            message = str(exc_info.value)
            assert "busy" in message and f"handle_id={handle_id}" in message, message

            run = _json(await client.call_tool("ssh_cmd_run", {"command": "echo hi"}))
            assert run['status'] == 'busy' and f"handle_id={handle_id}" in run['error'], run

            await _wait_for_operation(client, handle_id)
            assert _json(await client.call_tool("ssh_cmd_run", {"command": "echo hi"}))['status'] == 'success'
        finally:
            await disconnect_ssh(client)
            print_test_footer()


@pytest.mark.asyncio
async def test_failed_long_operation_reports_error(mcp_test_environment, monkeypatch):
    """A handed-off operation that fails reports status 'failed' with the real error."""
    print_test_header("Testing failed long operation")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            monkeypatch.setattr(server, 'max_foreground_wait', CAP)
            _slow_down(monkeypatch, 'calculate_directory_size', delay=4, fail=True)
            first = _json(await client.call_tool("ssh_dir_calc_size", {"path": TEST_WORKSPACE}))
            done = await _wait_for_operation(client, first['handle_id'])
            assert done['status'] == 'failed' and "simulated failure" in done['error'], done
        finally:
            await disconnect_ssh(client)
            print_test_footer()


@pytest.mark.asyncio
async def test_list_tool_in_progress_then_list_result(mcp_test_environment, monkeypatch):
    """A list-returning tool (content search) can hand off too; its result is the list."""
    print_test_header("Testing list tool handoff")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            monkeypatch.setattr(server, 'max_foreground_wait', CAP)
            _slow_down(monkeypatch, 'search_file_contents')
            first = _unwrap(await client.call_tool("ssh_dir_search_files_content", {
                "dir_path": TEST_WORKSPACE, "pattern": f"never-matches-{int(time.time())}"}))
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


# ---------------------------------------------------------------------------
# True concurrency (calls issued at the same instant, as parallel-calling harnesses do)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_simultaneous_operations_exactly_one_runs(mcp_test_environment, monkeypatch):
    """Two operations started at the same instant: exactly one runs, the other gets busy."""
    print_test_header("Testing simultaneous operations")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            monkeypatch.setattr(server, 'max_foreground_wait', CAP)
            _slow_down(monkeypatch, 'calculate_directory_size', delay=4)
            results = await asyncio.gather(
                *[client.call_tool("ssh_dir_calc_size", {"path": TEST_WORKSPACE}) for _ in range(4)],
                return_exceptions=True)
            started = [_json(r) for r in results if not isinstance(r, Exception)]
            refused = [r for r in results if isinstance(r, Exception)]
            assert len(started) == 1 and started[0]['status'] == 'in_progress', results
            assert len(refused) == 3 and all("busy" in str(r) for r in refused), refused
            await _wait_for_operation(client, started[0]['handle_id'])
        finally:
            await disconnect_ssh(client)
            print_test_footer()


@pytest.mark.asyncio
async def test_burst_of_status_calls_during_operation(mcp_test_environment, monkeypatch):
    """A burst of parallel status calls while an operation runs all answer promptly."""
    print_test_header("Testing parallel status calls during an operation")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            monkeypatch.setattr(server, 'max_foreground_wait', CAP)
            _slow_down(monkeypatch, 'calculate_directory_size', delay=8)
            first = _json(await client.call_tool("ssh_dir_calc_size", {"path": TEST_WORKSPACE}))
            calls = []
            for _ in range(5):
                calls += [client.call_tool("ssh_conn_is_connected", {}),
                          client.call_tool("ssh_cmd_history", {"limit": 3}),
                          client.call_tool("ssh_cmd_check_status", {"handle_id": first['handle_id'],
                                                                    "wait_seconds": 0.1}),
                          client.call_tool("ssh_task_status", {"pid": 1})]
            t0 = time.monotonic()
            await asyncio.gather(*calls)
            assert time.monotonic() - t0 < 5, f"20 status calls took {time.monotonic() - t0:.1f}s"
            await _wait_for_operation(client, first['handle_id'])
        finally:
            await disconnect_ssh(client)
            print_test_footer()


# ---------------------------------------------------------------------------
# Interplay with ssh_cmd_run and connections
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_operation_refused_while_cmd_run_waits(mcp_test_environment):
    """While ssh_cmd_run is waiting in the foreground, an operation gets busy."""
    print_test_header("Testing operation during a foreground ssh_cmd_run")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            run_task = asyncio.create_task(client.call_tool(
                "ssh_cmd_run", {"command": sleep_then_echo(5, "fg"), "wait_timeout": 20}))
            await asyncio.sleep(1.5)
            with pytest.raises(Exception) as exc_info:
                await client.call_tool("ssh_file_stat", {"path": TEST_WORKSPACE})
            assert "busy" in str(exc_info.value), str(exc_info.value)
            assert _json(await run_task)['status'] == 'success'
        finally:
            await disconnect_ssh(client)
            print_test_footer()


@pytest.mark.asyncio
async def test_operation_allowed_while_handed_off_command_runs(mcp_test_environment):
    """A command already handed off (wait_timeout) doesn't block operations."""
    print_test_header("Testing operation during a handed-off command")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            run = _json(await client.call_tool("ssh_cmd_run", {
                "command": sleep_then_echo(8, "bg"), "wait_timeout": 1}))
            assert run['status'] == 'wait_timeout', run
            stat = _json(await client.call_tool("ssh_file_stat", {"path": TEST_WORKSPACE}))
            assert stat.get('exists') is not False, stat
            done = _json(await client.call_tool("ssh_cmd_check_status",
                                                {"handle_id": run['id'], "wait_seconds": 12}))
            assert done['status'] == 'completed', done
        finally:
            await disconnect_ssh(client)
            print_test_footer()


@pytest.mark.asyncio
async def test_connect_as_long_operation(mcp_test_environment, monkeypatch):
    """ssh_conn_connect itself can hand off; check_status works before any connection
    exists, and the connection is usable once it completes."""
    print_test_header("Testing a slow ssh_conn_connect")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            monkeypatch.setattr(server, 'max_foreground_wait', CAP)
            host_key = f"{mcp.ssh_client.user}@{mcp.ssh_client.host}"
            await disconnect_ssh(client)

            original_cls = server.SshClient

            class SlowSshClient(original_cls):
                def __init__(self, *args, **kwargs):
                    time.sleep(SLOW)
                    super().__init__(*args, **kwargs)

            monkeypatch.setattr(server, 'SshClient', SlowSshClient)
            first = _json(await client.call_tool("ssh_conn_connect", {"host_name": host_key}))
            assert first['status'] == 'in_progress', first
            done = await _wait_for_operation(client, first['handle_id'])
            assert done['status'] == 'completed' and done['result']['status'] == 'success', done
            assert _json(await client.call_tool("ssh_cmd_run", {"command": "echo ok"}))['status'] == 'success'
        finally:
            await disconnect_ssh(client)
            print_test_footer()


@pytest.mark.asyncio
async def test_cmd_output_on_operation_handle_explains(mcp_test_environment, monkeypatch):
    """ssh_cmd_output on an operation handle points to ssh_cmd_check_status."""
    print_test_header("Testing ssh_cmd_output on an operation handle")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            monkeypatch.setattr(server, 'max_foreground_wait', CAP)
            _slow_down(monkeypatch, 'calculate_directory_size', delay=4)
            first = _json(await client.call_tool("ssh_dir_calc_size", {"path": TEST_WORKSPACE}))
            with pytest.raises(Exception) as exc_info:
                await client.call_tool("ssh_cmd_output", {"handle_id": first['handle_id']})
            assert "ssh_cmd_check_status" in str(exc_info.value), str(exc_info.value)
            await _wait_for_operation(client, first['handle_id'])
        finally:
            await disconnect_ssh(client)
            print_test_footer()


# ---------------------------------------------------------------------------
# A real long operation (no simulated slowness)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_real_archive_roundtrip_through_handoff(mcp_test_environment, monkeypatch):
    """Create and extract a real archive of a few thousand files with a tiny cap, so the
    real operations outlast it; the collected results are complete and correct."""
    print_test_header("Testing a real archive round-trip through the handoff")
    count = 3000
    base = f"{TEST_WORKSPACE}{PATH_SEP}archive_rt_{int(time.time())}"
    src, dest = f"{base}{PATH_SEP}src", f"{base}{PATH_SEP}dest"
    archive = f"{base}{PATH_SEP}archive.{'zip' if IS_WINDOWS else 'tar.gz'}"

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            if IS_WINDOWS:
                make = (f'powershell -NoProfile -Command "New-Item -ItemType Directory -Force -Path \'{src}\' | Out-Null; '
                        f"1..{count} | ForEach-Object {{ [IO.File]::WriteAllText(('{src}\\f{{0:D5}}.txt' -f $_), 'x') }}\"")
                count_cmd = (f'powershell -NoProfile -Command "(Get-ChildItem -LiteralPath \'{dest}\' '
                             f'-Recurse -File).Count"')
            else:
                make = f"mkdir -p {src} && cd {src} && seq 1 {count} | sed 's/^/f/' | xargs touch"
                count_cmd = f"find {dest} -type f | wc -l"
            made = _json(await client.call_tool("ssh_cmd_run", {"command": make, "wait_timeout": 45}))
            assert made['status'] == 'success', made

            monkeypatch.setattr(server, 'max_foreground_wait', 0.2)
            created = await _result_or_poll(client, _json(await client.call_tool(
                "ssh_archive_create", {"source_path": src, "archive_path": archive})))
            assert created.get('size_bytes', 0) > 0, created

            extracted = await _result_or_poll(client, _json(await client.call_tool(
                "ssh_archive_extract", {"archive_path": created.get('archive_created', archive),
                                        "destination_path": dest})))
            logger.info(f"extract result: {extracted}")
            monkeypatch.setattr(server, 'max_foreground_wait', 50.0)

            counted = _json(await client.call_tool("ssh_cmd_run", {"command": count_cmd}))
            assert int(counted['output'].strip()) == count, counted
        finally:
            monkeypatch.setattr(server, 'max_foreground_wait', 50.0)
            await client.call_tool("ssh_cmd_run", {"command": cleanup_command(base), "wait_timeout": 45})
            await disconnect_ssh(client)
            print_test_footer()
