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
    skip_on_windows, IS_WINDOWS
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
    if len(parsed) == 1 and isinstance(parsed[0], list):
        return parsed[0]
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
    are skipped: no match is still an empty list, not an error."""
    print_test_header("Testing content search with unreadable subdirectories")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            none = _search_results(await client.call_tool("ssh_dir_search_files_content", {
                "dir_path": "/tmp", "pattern": f"never-matches-{int(time.time())}"}))
            assert none == [], none
        finally:
            await disconnect_ssh(client)
            print_test_footer()
