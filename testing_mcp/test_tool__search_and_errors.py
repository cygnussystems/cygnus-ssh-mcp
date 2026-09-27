"""Regression tests for the 2026-09-26 OpenCode retest (planning/2026-09-26-retest-fix-plan.md, WS1):

- a content search with no match returns an empty list on every platform (Linux used to raise
  "Command failed with exit code 1": GNU xargs reports grep's exit 1 as 123)
- a search pattern starting with '-' isn't taken as a grep option
- searching a directory that doesn't exist gives an actionable error
- an unknown command handle's error explains that reconnecting clears handles
"""
import pytest
import json
import logging
import time
from conftest import (
    print_test_header, print_test_footer, make_connection, disconnect_ssh,
    mcp_test_environment, extract_result_text, remote_temp_path, cleanup_command,
    skip_on_windows, windows_only, IS_WINDOWS, TEST_WORKSPACE, PATH_SEP
)

from cygnus_ssh_mcp.server import mcp
from fastmcp import Client

logger = logging.getLogger(__name__)


def _search_results(result):
    # Newer fastmcp wraps a list result as {"result": [...]}; older versions return
    # one content item per list entry
    items = result.content if hasattr(result, 'content') else result
    parsed = [json.loads(item.text) for item in items]
    if len(parsed) == 1 and isinstance(parsed[0], dict) and 'result' in parsed[0]:
        return parsed[0]['result']
    if len(parsed) == 1 and isinstance(parsed[0], (list, dict)):
        return parsed[0]  # a list of matches, or an 'incomplete' / 'in_progress' dict
    return parsed


async def _make_dir_with_file(client, content):
    work_dir = remote_temp_path("search_test")
    sep = "\\" if IS_WINDOWS else "/"
    await client.call_tool("ssh_dir_mkdir", {"path": work_dir})
    await client.call_tool("ssh_file_write", {"file_path": f"{work_dir}{sep}payload.txt", "content": content})
    return work_dir


@pytest.mark.asyncio
async def test_search_no_match_returns_empty_list(mcp_test_environment):
    """No match is a normal empty result, not an error - on every platform."""
    print_test_header("Testing content search with no match")

    async with Client(mcp) as client:
        work_dir = None
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            work_dir = await _make_dir_with_file(client, "backup sample\n")

            none = _search_results(await client.call_tool("ssh_dir_search_files_content", {
                "dir_path": work_dir, "pattern": f"never-matches-{int(time.time())}"}))
            assert none == [], f"expected an empty list, got {none}"

            hits = _search_results(await client.call_tool("ssh_dir_search_files_content", {
                "dir_path": work_dir, "pattern": "backup sample"}))
            assert len(hits) == 1 and hits[0]['line'] == 1, hits
        finally:
            if work_dir:
                await client.call_tool("ssh_cmd_run", {"command": cleanup_command(work_dir)})
            await disconnect_ssh(client)
            print_test_footer()


@pytest.mark.asyncio
@skip_on_windows
async def test_search_pattern_starting_with_dash(mcp_test_environment):
    """A pattern beginning with '-' is searched for, not parsed as a grep option."""
    print_test_header("Testing content search with a leading-dash pattern")

    async with Client(mcp) as client:
        work_dir = None
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            work_dir = await _make_dir_with_file(client, "flags: -v --verbose\n")
            hits = _search_results(await client.call_tool("ssh_dir_search_files_content", {
                "dir_path": work_dir, "pattern": "-v --verbose"}))
            assert len(hits) == 1, hits
        finally:
            if work_dir:
                await client.call_tool("ssh_cmd_run", {"command": cleanup_command(work_dir)})
            await disconnect_ssh(client)
            print_test_footer()


@pytest.mark.asyncio
@skip_on_windows
async def test_search_missing_directory_is_actionable_error(mcp_test_environment):
    """Searching a directory that doesn't exist is a real error, and says which path."""
    print_test_header("Testing content search in a missing directory")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            missing = f"/tmp/no_such_dir_{int(time.time())}"
            with pytest.raises(Exception) as exc_info:
                await client.call_tool("ssh_dir_search_files_content", {
                    "dir_path": missing, "pattern": "anything"})
            assert missing in str(exc_info.value), str(exc_info.value)
        finally:
            await disconnect_ssh(client)
            print_test_footer()


@pytest.mark.asyncio
async def test_unknown_handle_error_mentions_reconnect(mcp_test_environment):
    """The unknown-handle error tells the model why (handles are per connection)."""
    print_test_header("Testing unknown handle error text")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            with pytest.raises(Exception) as exc_info:
                await client.call_tool("ssh_cmd_output", {"handle_id": 987654})
            message = str(exc_info.value)
            assert "987654" in message and "reconnect" in message.lower(), message
        finally:
            await disconnect_ssh(client)
            print_test_footer()


@pytest.mark.asyncio
@skip_on_windows
async def test_search_skips_unreadable_subdirectories(mcp_test_environment):
    """Unreadable entries inside the search root (e.g. systemd's private dirs under /tmp)
    don't make the search fail - and they're reported, not silently ignored: the result
    is 'incomplete' with no matches and the skipped entries listed (or a plain [] if
    everything happened to be readable)."""
    print_test_header("Testing content search with unreadable subdirectories")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            none = _search_results(await client.call_tool("ssh_dir_search_files_content", {
                "dir_path": "/tmp", "pattern": f"never-matches-{int(time.time())}"}))
            if isinstance(none, dict):
                assert none['status'] == 'incomplete' and none['matches'] == [], none
                assert none['skipped_count'] >= 1 and none['skipped'], none
            else:
                assert none == [], none
        finally:
            await disconnect_ssh(client)
            print_test_footer()



# ---- 2026-09-27 round 4: Windows search skipped non-ASCII filenames, and was slow ----

UNICODE_LINE = "caf\u00e9 \u6f22\u5b57 \U0001f33f"


@pytest.mark.asyncio
async def test_search_finds_files_with_non_ascii_names(mcp_test_environment):
    """The tester's control: 'unicode-caf\u00e9.txt' and 'plain.txt' hold the same line.
    Both must be found - for the full Unicode pattern and for plain 'caf' - with the
    file name intact. (Windows used to return only plain.txt, or [] for a tree of such
    files: the names came through PowerShell's OEM console output and got garbled.)"""
    print_test_header("Testing content search with non-ASCII file names")
    work_dir = f"{TEST_WORKSPACE}{PATH_SEP}unicode_search_{int(time.time())}"
    unicode_name = f"{work_dir}{PATH_SEP}unicode-caf\u00e9.txt"

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            await client.call_tool("ssh_dir_mkdir", {"path": work_dir})
            for name in (unicode_name, f"{work_dir}{PATH_SEP}plain.txt"):
                written = json.loads(extract_result_text(await client.call_tool(
                    "ssh_file_write", {"file_path": name, "content": UNICODE_LINE + "\n"})))
                assert written.get('success'), written

            for pattern in (UNICODE_LINE, "caf"):
                hits = _search_results(await client.call_tool("ssh_dir_search_files_content", {
                    "dir_path": work_dir, "pattern": pattern}))
                assert isinstance(hits, list), hits
                files = sorted(h['file'] for h in hits)
                assert len(hits) == 2, f"pattern {pattern!r}: expected 2 files, got {files}"
                assert any(f.endswith("unicode-caf\u00e9.txt") for f in files), files
                assert all(h['content'] == UNICODE_LINE for h in hits), hits
        finally:
            await client.call_tool("ssh_cmd_run", {"command": cleanup_command(work_dir)})
            await disconnect_ssh(client)
            print_test_footer()


@pytest.mark.asyncio
@windows_only
async def test_windows_search_of_300_files_is_fast(mcp_test_environment):
    """300 small files are searched directly, in seconds (was ~126s: one new SFTP
    session per file)."""
    print_test_header("Testing Windows content search speed")
    work_dir = f"{TEST_WORKSPACE}{PATH_SEP}search_speed_{int(time.time())}"

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            make = (f'powershell -NoProfile -Command "New-Item -ItemType Directory -Force -Path \'{work_dir}\' | Out-Null; '
                    f"1..300 | ForEach-Object {{ [IO.File]::WriteAllText(('{work_dir}\\file{{0:D3}}.txt' -f $_), \\\"needle $_\\\") }}\"")
            made = json.loads(extract_result_text(await client.call_tool(
                "ssh_cmd_run", {"command": make, "wait_timeout": 45})))
            assert made['status'] == 'success', made

            start = time.monotonic()
            hits = _search_results(await client.call_tool("ssh_dir_search_files_content", {
                "dir_path": work_dir, "pattern": "needle"}))
            elapsed = time.monotonic() - start
            assert isinstance(hits, list) and len(hits) == 300, (len(hits), hits if isinstance(hits, dict) else '')
            assert elapsed < 30, f"search of 300 files took {elapsed:.1f}s"
        finally:
            await client.call_tool("ssh_cmd_run", {"command": cleanup_command(work_dir), "wait_timeout": 45})
            await disconnect_ssh(client)
            print_test_footer()


@pytest.mark.asyncio
@skip_on_windows
async def test_search_reports_unreadable_files_as_incomplete(mcp_test_environment):
    """A file that can't be read makes the result 'incomplete' and names it - the
    search never claims 'no matches' for files it didn't actually search."""
    print_test_header("Testing incomplete content search")
    work_dir = f"{TEST_WORKSPACE}{PATH_SEP}search_incomplete_{int(time.time())}"

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            await client.call_tool("ssh_dir_mkdir", {"path": work_dir})
            for name in ("readable.txt", "locked.txt"):
                await client.call_tool("ssh_file_write", {"file_path": f"{work_dir}/{name}", "content": "needle\n"})
            await client.call_tool("ssh_cmd_run", {"command": f"chmod 000 {work_dir}/locked.txt"})

            result = _search_results(await client.call_tool("ssh_dir_search_files_content", {
                "dir_path": work_dir, "pattern": "needle"}))
            assert isinstance(result, dict) and result['status'] == 'incomplete', result
            assert [m['file'] for m in result['matches']] == [f"{work_dir}/readable.txt"], result
            assert result['skipped_count'] == 1 and result['skipped'][0]['path'].endswith("locked.txt"), result
            assert 'note' in result
        finally:
            await client.call_tool("ssh_cmd_run", {"command": f"chmod 600 {work_dir}/locked.txt; rm -rf {work_dir}"})
            await disconnect_ssh(client)
            print_test_footer()
