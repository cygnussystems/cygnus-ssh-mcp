"""Regression tests for issues/2026-09-25-*.md:

- ssh_cmd_run must not block other tool calls while it waits (it used to block the
  event loop, so even ssh_conn_is_connected hung until the command finished)
- ssh_cmd_run caps a single call's wait so the id/pid handoff arrives before a
  client-side request timeout (~60s in many MCP clients)
- ssh_task_launch's stderr defaults to stdout's file, and the returned log paths exist
- ssh_cmd_history timestamps are valid ISO 8601 (no '+00:00Z')
- ssh_conn_connect reports the Linux distro, the real network interfaces, and a clean
  load_avg on macOS
"""
import pytest
import json
import asyncio
import logging
import re
import time
from datetime import datetime
from conftest import (
    print_test_header, print_test_footer, make_connection, disconnect_ssh,
    mcp_test_environment, extract_result_text, echo_command, sleep_then_echo,
    remote_temp_path, cleanup_file_command, read_file_command,
    skip_on_windows, linux_only, windows_only
)

from cygnus_ssh_mcp import server
from cygnus_ssh_mcp.server import mcp
from fastmcp import Client

logger = logging.getLogger(__name__)


def _json(result):
    return json.loads(extract_result_text(result))


def _history_entries(result):
    # Newer fastmcp wraps a list result as {"result": [...]}; older versions return
    # one content item per list entry instead
    items = result.content if hasattr(result, 'content') else result
    parsed = [json.loads(item.text) for item in items]
    if len(parsed) == 1 and isinstance(parsed[0], dict) and 'result' in parsed[0]:
        return parsed[0]['result']
    if len(parsed) == 1 and isinstance(parsed[0], list):
        return parsed[0]
    return parsed


@pytest.mark.asyncio
async def test_ssh_cmd_run_does_not_block_other_tools(mcp_test_environment, monkeypatch):
    """While ssh_cmd_run waits, other tools answer immediately, and the wait is capped."""
    print_test_header("Testing ssh_cmd_run wait cap and concurrent tool calls")
    monkeypatch.setattr(server, 'max_foreground_wait', 3.0)

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            # Asks for a 600s wait - the cap must hand off after ~3s instead
            run_task = asyncio.create_task(client.call_tool("ssh_cmd_run", {
                "command": sleep_then_echo(8, "cap-test-done"),
                "io_timeout": 300.0,
                "wait_timeout": 600.0,
            }))
            await asyncio.sleep(1.0)

            start = time.monotonic()
            connected = _json(await client.call_tool("ssh_conn_is_connected", {}))
            history = _history_entries(await client.call_tool(
                "ssh_cmd_history", {"limit": 1, "reverse": True, "include_internal": False}))
            elapsed = time.monotonic() - start
            assert connected is True
            assert elapsed < 1.5, f"Tools blocked for {elapsed:.1f}s behind an in-flight ssh_cmd_run"
            assert history and history[0]['end_time'] is None, \
                f"In-flight command should be visible in history while running: {history}"

            run_json = _json(await run_task)
            assert run_json['status'] == 'wait_timeout', run_json
            assert run_json['wait_capped'] is True
            assert run_json['requested_wait_timeout'] == 600.0
            assert run_json['timeout_seconds'] == 3.0
            assert run_json['id'] == history[0]['id'], "History entry should match the returned handle"

            # The command was not killed - it completes in the background
            await asyncio.sleep(8.0)
            status = _json(await client.call_tool(
                "ssh_cmd_check_status", {"handle_id": run_json['id']}))
            assert status['status'] == 'completed', status
            assert status['exit_code'] == 0
        finally:
            await disconnect_ssh(client)
            print_test_footer()


@pytest.mark.asyncio
async def test_ssh_cmd_run_wait_under_cap_not_flagged(mcp_test_environment, monkeypatch):
    """A wait_timeout below the cap behaves exactly as before - no wait_capped flag."""
    print_test_header("Testing ssh_cmd_run wait_timeout below the cap")
    monkeypatch.setattr(server, 'max_foreground_wait', 30.0)

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            run_json = _json(await client.call_tool("ssh_cmd_run", {
                "command": sleep_then_echo(5, "under-cap"),
                "wait_timeout": 2.0,
            }))
            assert run_json['status'] == 'wait_timeout', run_json
            assert 'wait_capped' not in run_json
            await asyncio.sleep(5.0)
        finally:
            await disconnect_ssh(client)
            print_test_footer()


@pytest.mark.asyncio
@skip_on_windows
async def test_ssh_task_launch_stderr_defaults_to_stdout_log(mcp_test_environment):
    """With only stdout_log given, stderr goes to the same file and the returned paths are real."""
    print_test_header("Testing ssh_task_launch stderr default")
    log_path = remote_temp_path("task_stderr_default") + ".log"

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            launch = _json(await client.call_tool("ssh_task_launch", {
                "command": "echo OUT_LINE; echo ERR_LINE >&2",
                "stdout_log": log_path,
            }))
            assert launch['stdout_log'] == log_path
            assert launch['stderr_log'] == log_path, \
                f"stderr_log should be the file stderr really goes to: {launch}"

            await asyncio.sleep(2.0)
            content = _json(await client.call_tool("ssh_cmd_run", {"command": read_file_command(log_path)}))
            assert "OUT_LINE" in content['output']
            assert "ERR_LINE" in content['output'], f"stderr was discarded: {content['output']!r}"
        finally:
            await client.call_tool("ssh_cmd_run", {"command": cleanup_file_command(log_path)})
            await disconnect_ssh(client)
            print_test_footer()


@pytest.mark.asyncio
async def test_ssh_cmd_history_timestamps_are_iso8601(mcp_test_environment):
    """History timestamps parse as ISO 8601 (used to be '+00:00Z')."""
    print_test_header("Testing ssh_cmd_history timestamp format")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            await client.call_tool("ssh_cmd_run", {"command": echo_command("ts-test")})
            entry = _history_entries(await client.call_tool(
                "ssh_cmd_history", {"limit": 1, "reverse": True}))[0]
            for key in ('start_time', 'end_time'):
                assert not entry[key].endswith('Z'), f"{key} has offset and Z: {entry[key]}"
                assert datetime.fromisoformat(entry[key]).tzinfo is not None
        finally:
            await disconnect_ssh(client)
            print_test_footer()


@pytest.mark.asyncio
@linux_only
async def test_ssh_conn_connect_reports_distro_and_interfaces(mcp_test_environment):
    """Linux connect reports the real distro and at least one non-loopback interface."""
    print_test_header("Testing ssh_conn_connect os_version and interfaces")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            info = _json(await client.call_tool("ssh_conn_host_info", {}))
            logger.info(f"host_info: {info}")

            # ssh_conn_status has no os_version at all - check the field detection sets
            os_version = info['connection'].get('os_version')
            assert os_version and os_version != 'unknown_linux', \
                f"Distro detection failed: {info['connection']}"
            interfaces = info.get('interfaces') or info.get('system', {}).get('interfaces')
            assert interfaces, f"No interfaces reported: {info}"
            assert any(i['name'] != 'lo' and i['ip_addresses'] for i in interfaces), interfaces
            assert 'raw_output' not in info and 'raw_output' not in info.get('system', {})
        finally:
            await disconnect_ssh(client)
            print_test_footer()


@pytest.mark.asyncio
@skip_on_windows
async def test_ssh_conn_connect_load_avg_is_three_numbers(mcp_test_environment):
    """load_avg is plain '1.23 4.56 7.89' (macOS used to give 'LOAD:{ ... } LOAD:{ ... }')."""
    print_test_header("Testing ssh_conn_connect load_avg format")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            info = _json(await client.call_tool("ssh_conn_host_info", {}))
            load_avg = info.get('load_avg') or info.get('system', {}).get('load_avg')
            assert re.fullmatch(r"\d+(\.\d+)? \d+(\.\d+)? \d+(\.\d+)?", load_avg or ""), \
                f"Unexpected load_avg format: {load_avg!r}"
        finally:
            await disconnect_ssh(client)
            print_test_footer()


@pytest.mark.asyncio
@skip_on_windows
async def test_ssh_cmd_check_status_output_lines_is_total(mcp_test_environment):
    """output_lines counts all stdout lines, not just the last 50 (it used to cap at 50)."""
    print_test_header("Testing ssh_cmd_check_status output_lines")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            run_json = _json(await client.call_tool("ssh_cmd_run", {"command": "seq 1 120"}))
            assert run_json['status'] == 'success', run_json
            status = _json(await client.call_tool(
                "ssh_cmd_check_status", {"handle_id": run_json['id'], "wait_seconds": 0.1}))
            assert status['output_lines'] == 120, status
        finally:
            await disconnect_ssh(client)
            print_test_footer()


@pytest.mark.asyncio
@windows_only
async def test_ssh_conn_connect_reports_windows_version(mcp_test_environment):
    """Windows connect reports a real version (bare 'ver' failed under a PowerShell default shell)."""
    print_test_header("Testing ssh_conn_connect Windows os_version")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            connection = _json(await client.call_tool("ssh_conn_host_info", {}))['connection']
            assert connection.get('os_version') and connection['os_version'] != 'unknown_windows', \
                f"Windows version detection failed: {connection}"
        finally:
            await disconnect_ssh(client)
            print_test_footer()
