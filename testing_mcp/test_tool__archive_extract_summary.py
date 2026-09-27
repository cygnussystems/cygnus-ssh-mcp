"""ssh_archive_extract returns a concise result (round-4 issue 6,
issues/2026-09-27-large-archive-results-overwhelm-opencode.md).

It used to return every extracted name inline (~300 KB of JSON for 18,000 files), which
OpenCode couldn't show. Now: counts, the first 50 file paths (relative to the
destination, on every platform), a truncation flag and a note on how to list the rest.
"""
import pytest
import json
import time
import logging
from conftest import (
    print_test_header, print_test_footer, make_connection, disconnect_ssh,
    mcp_test_environment, extract_result_text, TEST_WORKSPACE, PATH_SEP, IS_WINDOWS,
    cleanup_command
)

from cygnus_ssh_mcp.server import mcp
from fastmcp import Client

logger = logging.getLogger(__name__)


async def _json_result(client, tool, params):
    """The tool's result, following an in_progress handoff if one happens."""
    response = json.loads(extract_result_text(await client.call_tool(tool, params)))
    if isinstance(response, dict) and response.get('status') == 'in_progress':
        for _ in range(120):
            status = json.loads(extract_result_text(await client.call_tool(
                "ssh_cmd_check_status", {"handle_id": response['handle_id'], "wait_seconds": 2})))
            if status['status'] != 'running':
                return status.get('result', status)
    return response


def _make_tree_command(src, per_folder):
    if IS_WINDOWS:
        return (f'powershell -NoProfile -Command "foreach ($d in \'d1\',\'d2\') {{ '
                f"New-Item -ItemType Directory -Force -Path ('{src}\\' + $d) | Out-Null; "
                f"1..{per_folder} | ForEach-Object {{ [IO.File]::WriteAllText(('{src}\\' + $d + "
                f"('\\f{{0:D3}}.txt' -f $_)), 'x') }} }}\"")
    return (f"mkdir -p {src}/d1 {src}/d2 && for i in $(seq 1 {per_folder}); do "
            f"echo x > {src}/d1/f$(printf %03d $i).txt; echo y > {src}/d2/f$(printf %03d $i).txt; done")


async def _archive_and_extract(client, base, per_folder):
    src, dest = f"{base}{PATH_SEP}src", f"{base}{PATH_SEP}dest"
    archive = f"{base}{PATH_SEP}a.{'zip' if IS_WINDOWS else 'tar.gz'}"
    made = json.loads(extract_result_text(await client.call_tool(
        "ssh_cmd_run", {"command": _make_tree_command(src, per_folder), "wait_timeout": 45})))
    assert made['status'] == 'success', made
    created = await _json_result(client, "ssh_archive_create", {"source_path": src, "archive_path": archive})
    assert created.get('status') == 'success', created
    raw = extract_result_text(await client.call_tool("ssh_archive_extract", {
        "archive_path": created.get('archive_created', archive), "destination_path": dest}))
    result = json.loads(raw)
    if result.get('status') == 'in_progress':
        result = await _json_result(client, "ssh_cmd_check_status", {"handle_id": result['handle_id']})
    return result, len(raw)


@pytest.mark.asyncio
async def test_large_extraction_result_is_concise(mcp_test_environment):
    print_test_header("Testing a concise ssh_archive_extract result (120 files)")
    base = f"{TEST_WORKSPACE}{PATH_SEP}extract_summary_{int(time.time())}"
    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            result, size = await _archive_and_extract(client, base, per_folder=60)
            assert result['status'] == 'success', result
            assert result['files_extracted'] == 120 and result['directories'] == 2, result
            sample = result['extracted_files']
            assert len(sample) == 50 and result['extracted_files_truncated'] is True, result
            assert all(p.startswith(("d1/", "d2/")) for p in sample), f"paths should be relative to the destination: {sample[:3]}"
            assert "ssh_dir_search_glob" in result['note'], result
            assert size < 8000, f"result is {size} bytes - should be concise"
        finally:
            await client.call_tool("ssh_cmd_run", {"command": cleanup_command(base), "wait_timeout": 45})
            await disconnect_ssh(client)
            print_test_footer()


@pytest.mark.asyncio
async def test_small_extraction_lists_everything(mcp_test_environment):
    print_test_header("Testing a small ssh_archive_extract result (4 files)")
    base = f"{TEST_WORKSPACE}{PATH_SEP}extract_small_{int(time.time())}"
    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            result, _ = await _archive_and_extract(client, base, per_folder=2)
            assert result['files_extracted'] == 4 and result['directories'] == 2, result
            assert sorted(result['extracted_files']) == ["d1/f001.txt", "d1/f002.txt", "d2/f001.txt", "d2/f002.txt"], result
            assert result['extracted_files_truncated'] is False and 'note' not in result, result
        finally:
            await client.call_tool("ssh_cmd_run", {"command": cleanup_command(base), "wait_timeout": 45})
            await disconnect_ssh(client)
            print_test_footer()
