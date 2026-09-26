"""Regression tests for output retention (2026-09-26 OpenCode retest F1, WS3 of
planning/2026-09-26-retest-fix-plan.md):

- ordinary output (e.g. 105 lines) comes back complete - it used to be cut to the last 100
  lines with no indication
- output beyond the inline limit is flagged (output_truncated + counts + output_note) and the
  rest can be paged with ssh_cmd_output(start_line=...)
- output beyond the per-command size limit drops the EARLIEST lines, counted, and asking for
  them gives an error naming the first available line
- stderr is handled the same way, separately
- the total memory ceiling releases the oldest finished command's output first
"""
import pytest
import json
import logging
import time
from conftest import (
    print_test_header, print_test_footer, make_connection, disconnect_ssh,
    mcp_test_environment, extract_result_text, multiline_echo_command, IS_WINDOWS, windows_only
)

from cygnus_ssh_mcp.models import OutputLimits
from cygnus_ssh_mcp.server import mcp
from fastmcp import Client

logger = logging.getLogger(__name__)


def _json(result):
    return json.loads(extract_result_text(result))


def _lines(result):
    items = result.content if hasattr(result, 'content') else result
    parsed = [json.loads(item.text) for item in items]
    if len(parsed) == 1 and isinstance(parsed[0], dict) and 'result' in parsed[0]:
        return parsed[0]['result']
    if len(parsed) == 1 and isinstance(parsed[0], list):
        return parsed[0]
    return parsed


def _stderr_lines_command(count):
    if IS_WINDOWS:
        return (f'powershell -Command "1..{count} | ForEach-Object '
                f'{{ [Console]::Error.WriteLine(\\"Err $_\\") }}"')
    return f"for i in $(seq 1 {count}); do echo \"Err $i\" >&2; done"


@pytest.mark.asyncio
async def test_ordinary_output_is_complete(mcp_test_environment):
    """105 lines come back in full, flagged as not truncated."""
    print_test_header("Testing that ordinary output is complete")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            run = _json(await client.call_tool("ssh_cmd_run", {"command": multiline_echo_command(105)}))
            assert run['status'] == 'success', run
            lines = [l for l in run['output'].splitlines() if l.strip()]
            assert len(lines) == 105 and lines[0] == "Line 1" and lines[-1] == "Line 105", lines[:3]
            assert run['output_truncated'] is False and run['stderr_truncated'] is False
            assert 'output_note' not in run

            status = _json(await client.call_tool("ssh_cmd_check_status",
                                                  {"handle_id": run['id'], "wait_seconds": 0.1}))
            assert status['output_lines'] == 105, status
        finally:
            await disconnect_ssh(client)
            print_test_footer()


@pytest.mark.asyncio
async def test_large_output_is_flagged_and_pageable(mcp_test_environment, monkeypatch):
    """Past the inline limit: flagged, counted, pageable. Past the size limit: earliest
    lines dropped and counted; asking for them names the first available line."""
    print_test_header("Testing large output truncation and paging")
    # Each line is "Line N\n" (<= 10 chars): ~1000 lines = ~9 KB
    monkeypatch.setattr(OutputLimits, 'per_stream', 5000)   # keeps roughly the last 550 lines
    monkeypatch.setattr(OutputLimits, 'inline', 500)        # returns roughly the last 50

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            run = _json(await client.call_tool("ssh_cmd_run", {"command": multiline_echo_command(1000)}))
            assert run['status'] == 'success', run
            assert run['output_truncated'] is True, run
            assert run['output_lines_total'] == 1000, run
            returned, dropped = run['output_lines_returned'], run['output_lines_dropped']
            assert 0 < returned < 1000 and 0 < dropped < 1000 - returned, run
            assert len(run['output']) <= 500
            assert run['output'].rstrip().splitlines()[-1].strip() == "Line 1000"
            note = run['output_note']
            assert f"handle_id={run['id']}" in note and "start_line" in note and "dropped" in note, note

            # Page the first retained lines
            first_available = dropped + 1
            page = _lines(await client.call_tool("ssh_cmd_output", {
                "handle_id": run['id'], "start_line": first_available, "lines": 3}))
            assert [l.strip() for l in page] == [f"Line {first_available + i}" for i in range(3)], page

            # Dropped lines: a clear error naming the first available line
            with pytest.raises(Exception) as exc_info:
                await client.call_tool("ssh_cmd_output", {"handle_id": run['id'], "start_line": 1})
            assert f"first available line is {first_available}" in str(exc_info.value), str(exc_info.value)
        finally:
            await disconnect_ssh(client)
            print_test_footer()


@pytest.mark.asyncio
async def test_stderr_truncation_is_separate(mcp_test_environment, monkeypatch):
    """stderr gets its own truncation flags and paging, independent of stdout."""
    print_test_header("Testing stderr truncation")
    monkeypatch.setattr(OutputLimits, 'inline', 300)

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            run = _json(await client.call_tool("ssh_cmd_run", {"command": _stderr_lines_command(200)}))
            assert run['stderr_truncated'] is True and run['stderr_lines_total'] == 200, run
            assert run['output_truncated'] is False
            page = _lines(await client.call_tool("ssh_cmd_output", {
                "handle_id": run['id'], "stream": "stderr", "start_line": 1, "lines": 2}))
            assert [l.strip() for l in page] == ["Err 1", "Err 2"], page
        finally:
            await disconnect_ssh(client)
            print_test_footer()


@pytest.mark.asyncio
async def test_memory_ceiling_releases_oldest_output(mcp_test_environment, monkeypatch):
    """Over the total ceiling, the oldest finished command's output is released - and
    reading it says so instead of returning something misleading."""
    print_test_header("Testing the output memory ceiling")
    monkeypatch.setattr(OutputLimits, 'total', 3000)

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            first = _json(await client.call_tool("ssh_cmd_run", {"command": multiline_echo_command(300)}))
            for _ in range(2):
                await client.call_tool("ssh_cmd_run", {"command": multiline_echo_command(300)})
            with pytest.raises(Exception) as exc_info:
                await client.call_tool("ssh_cmd_output", {"handle_id": first['id'], "start_line": 1})
            assert "dropped" in str(exc_info.value), str(exc_info.value)
        finally:
            await disconnect_ssh(client)
            print_test_footer()


@pytest.mark.asyncio
@windows_only
async def test_windows_fast_bulk_output_is_not_lost(mcp_test_environment):
    """A command that writes a lot and exits quickly returns ALL its output. The old
    Windows relay (PowerShell event actions) silently lost the tail - a 1000-line command
    came back as 'success' with only lines 1-505."""
    print_test_header("Testing Windows fast bulk output")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            run = _json(await client.call_tool("ssh_cmd_run", {"command": multiline_echo_command(3000)}))
            assert run['status'] == 'success', run
            status = _json(await client.call_tool("ssh_cmd_check_status",
                                                  {"handle_id": run['id'], "wait_seconds": 0.1}))
            assert status['output_lines'] == 3000, status
            last = _lines(await client.call_tool("ssh_cmd_output", {"handle_id": run['id'], "lines": 1}))
            assert last[-1].strip() == "Line 3000", last
        finally:
            await disconnect_ssh(client)
            print_test_footer()


@pytest.mark.asyncio
@windows_only
async def test_windows_child_holding_pipes_does_not_hang(mcp_test_environment):
    """If a command leaves a child process holding the output pipes, ssh_cmd_run still
    returns right after the command itself exits."""
    print_test_header("Testing Windows command with a lingering child process")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            start = time.monotonic()
            run = _json(await client.call_tool("ssh_cmd_run", {
                "command": "start /b ping -n 20 127.0.0.1 >nul & echo parent-done",
                "wait_timeout": 30}))
            elapsed = time.monotonic() - start
            assert run['status'] == 'success' and "parent-done" in run['output'], run
            assert elapsed < 12, f"took {elapsed:.1f}s - waited for the lingering child"
        finally:
            await client.call_tool("ssh_cmd_run", {"command": "taskkill /f /im ping.exe"})
            await disconnect_ssh(client)
            print_test_footer()
