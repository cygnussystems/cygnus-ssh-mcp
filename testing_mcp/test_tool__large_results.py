"""Tools that parse command output must return ALL results (gap #6 of the WS4 test review).

Until WS3 (2026-09-26), command output was kept in a 100-line buffer, so every tool that
parses it - directory listings, glob search, content search - silently returned at most
~100 entries. These tests use 300 files, well past that.
"""
import pytest
import json
import logging
import time
from conftest import (
    print_test_header, print_test_footer, make_connection, disconnect_ssh,
    mcp_test_environment, TEST_WORKSPACE, PATH_SEP, IS_WINDOWS, cleanup_command
)

from cygnus_ssh_mcp.server import mcp
from fastmcp import Client

logger = logging.getLogger(__name__)

COUNT = 300


def _unwrap(result):
    items = result.content if hasattr(result, 'content') else result
    parsed = [json.loads(item.text) for item in items]
    if len(parsed) == 1 and isinstance(parsed[0], dict) and set(parsed[0]) == {'result'}:
        return parsed[0]['result']
    if len(parsed) == 1:
        return parsed[0]
    return parsed


@pytest.mark.asyncio
async def test_tools_return_more_than_100_results(mcp_test_environment):
    print_test_header(f"Testing tools with {COUNT} results")
    base = f"{TEST_WORKSPACE}{PATH_SEP}large_results_{int(time.time())}"

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            if IS_WINDOWS:
                make = (f'powershell -NoProfile -Command "New-Item -ItemType Directory -Force -Path \'{base}\' | Out-Null; '
                        f"1..{COUNT} | ForEach-Object {{ [IO.File]::WriteAllText(('{base}\\file{{0:D3}}.txt' -f $_), \\\"needle $_\\\") }}\"")
            else:
                make = (f"mkdir -p {base} && cd {base} && "
                        f"for i in $(seq 1 {COUNT}); do printf 'needle %s\\n' $i > file$(printf %03d $i).txt; done")
            made = json.loads((await client.call_tool("ssh_cmd_run", {"command": make, "wait_timeout": 45})).content[0].text)
            assert made['status'] == 'success', made

            listing = _unwrap(await client.call_tool("ssh_dir_list_files_basic", {"path": base}))
            assert len(listing) == COUNT, f"ssh_dir_list_files_basic returned {len(listing)}"

            advanced = _unwrap(await client.call_tool("ssh_dir_list_advanced", {"path": base}))
            files = [e for e in advanced if (e.get('type') or '').lower() in ('file', 'f', '-')] or advanced
            assert len(files) >= COUNT, f"ssh_dir_list_advanced returned {len(files)} files"

            globbed = _unwrap(await client.call_tool("ssh_dir_search_glob", {"path": base, "pattern": "*.txt"}))
            assert len(globbed) == COUNT, f"ssh_dir_search_glob returned {len(globbed)}"

            found = _unwrap(await client.call_tool("ssh_dir_search_files_content",
                                                   {"dir_path": base, "pattern": "needle"}))
            assert len(found) == COUNT, f"ssh_dir_search_files_content returned {len(found)}"
        finally:
            await client.call_tool("ssh_cmd_run", {"command": cleanup_command(base), "wait_timeout": 45})
            await disconnect_ssh(client)
            print_test_footer()
