"""Line edits keep a file's own line endings. ssh_file_replace_line / insert_lines_after_match /
delete_line_by_content worked on LF-normalized text and wrote it back as LF, silently
converting every line of a CRLF file (found by the tester on Windows, 2026-09-28: 41 -> 39
bytes after replacing one line). Checked by exact file size, which doesn't depend on how any
tool displays line endings."""
import json
import time

import pytest
from conftest import (
    print_test_header, print_test_footer, make_connection, disconnect_ssh,
    mcp_test_environment, extract_result_text, TEST_WORKSPACE, PATH_SEP
)

from cygnus_ssh_mcp.server import mcp
from fastmcp import Client


def _json(result):
    return json.loads(extract_result_text(result))


async def _size(client, path):
    stat = _json(await client.call_tool("ssh_file_stat", {"path": path}))
    assert stat['exists'] is True, stat
    return stat['size']


@pytest.mark.asyncio
async def test_line_edits_keep_crlf_and_lf(mcp_test_environment):
    print_test_header("Testing that line edits keep the file's line endings")
    stamp = int(time.time())
    crlf = f"{TEST_WORKSPACE}{PATH_SEP}crlf_{stamp}.txt"
    lf = f"{TEST_WORKSPACE}{PATH_SEP}lf_{stamp}.txt"
    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            for path, eol in ((crlf, "\r\n"), (lf, "\n")):
                content = f"a=1{eol}b=2{eol}c=3{eol}"
                r = _json(await client.call_tool("ssh_file_write", {"file_path": path, "content": content}))
                assert r['success'], r
                assert await _size(client, path) == len(content.encode())

                r = _json(await client.call_tool("ssh_file_replace_line", {
                    "file_path": path, "match_line": "b=2", "new_line": "b=X"}))
                assert r['success'] and 'No changes' not in r.get('message', ''), r
                assert await _size(client, path) == len(content.encode()), f"line endings changed ({eol!r} file)"

                r = _json(await client.call_tool("ssh_file_insert_lines_after_match", {
                    "file_path": path, "match_line": "a=1", "lines_to_insert": ["i=1"]}))
                assert r['success'], r
                r = _json(await client.call_tool("ssh_file_delete_line_by_content", {
                    "file_path": path, "match_line": "c=3"}))
                assert r['success'], r
                expected = f"a=1{eol}i=1{eol}b=X{eol}"
                assert await _size(client, path) == len(expected.encode()), f"line endings changed ({eol!r} file)"
                read = _json(await client.call_tool("ssh_file_read", {"file_path": path}))
                assert read['content'].replace("\r\n", "\n") == expected.replace("\r\n", "\n"), read
        finally:
            for path in (crlf, lf):
                await client.call_tool("ssh_cmd_run", {"command": (f'del "{path}"' if PATH_SEP == '\\' else f"rm -f {path}")})
            await disconnect_ssh(client)
            print_test_footer()
