"""Unicode file names in the path lists Windows tools build with PowerShell
(issues/_archive_/2026-09-28-windows-dir-delete-garbles-unicode-paths.md). PowerShell wrote its output
in the console's OEM code page, so ssh_dir_delete returned "unicode-caf�.txt" and CJK/emoji
came back as "??" - even though SFTP listing/stat saw the right name."""
import json
import time

import pytest
from conftest import (
    print_test_header, print_test_footer, make_connection, disconnect_ssh,
    mcp_test_environment, extract_result_text, TEST_WORKSPACE, windows_only
)

from cygnus_ssh_mcp.server import mcp
from fastmcp import Client

NAME = "unicode-café 漢字 🌿.txt"


def _text(result):
    return extract_result_text(result)


@pytest.mark.asyncio
@windows_only
async def test_windows_path_lists_keep_unicode_names(mcp_test_environment):
    print_test_header("Testing Unicode names in Windows path lists")
    directory = f"{TEST_WORKSPACE}\\unicode_paths_{int(time.time())}"
    file_path = f"{directory}\\sub\\{NAME}"
    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            r = json.loads(_text(await client.call_tool("ssh_file_write", {
                "file_path": file_path, "content": "x\n", "create_dirs": True})))
            assert r['success'], r

            for tool, params, key in (
                    ("ssh_dir_delete", {"path": directory}, 'deleted_items'),                        # dry run
                    ("ssh_dir_batch_delete_files", {"path": directory, "pattern": "*.txt"}, 'deleted_files'),
                    ("ssh_dir_search_glob", {"path": directory, "pattern": "*.txt"}, None),
                    ("ssh_dir_list_advanced", {"path": directory, "max_depth": 3}, None)):
                result = json.loads(_text(await client.call_tool(tool, params)))
                items = result[key] if key else [entry['path'] for entry in result]
                assert file_path in items, f"{tool} garbled the Unicode name: {items}"

            result = json.loads(_text(await client.call_tool("ssh_dir_delete", {"path": directory, "dry_run": False})))
            assert result['status'] == 'success' and file_path in result['deleted_items'], result
        finally:
            await client.call_tool("ssh_cmd_run", {"command": f'if exist "{directory}" rmdir /s /q "{directory}"'})
            await disconnect_ssh(client)
            print_test_footer()
