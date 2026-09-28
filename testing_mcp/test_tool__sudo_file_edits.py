"""Sudo edits of root-only files (issues/_archive_/2026-09-28-freebsd-sudo-*; the bugs were general,
not FreeBSD-specific):

- ssh_file_replace_line / insert_lines_after_match / delete_line_by_content with use_sudo read
  the file as the NORMAL user; with force=True they then applied the edit to empty text and
  returned success / "No changes needed" without changing anything.
- ssh_file_write with use_sudo chowned every written file to the connected user, silently
  handing a root-only file (and its contents) to that user.
"""
import pytest
import json
import time
import logging
from conftest import (
    print_test_header, print_test_footer, make_connection, disconnect_ssh,
    mcp_test_environment, extract_result_text, skip_on_windows
)

from cygnus_ssh_mcp.server import mcp
from fastmcp import Client

logger = logging.getLogger(__name__)


def _json(result):
    return json.loads(extract_result_text(result))


async def _root_file(client, directory, content="a=1\nprivate=before\nz=9\n"):
    path = f"{directory}/root.conf"
    lines = content.replace("\n", "\\n")
    made = _json(await client.call_tool("ssh_cmd_run", {"use_sudo": True, "command":
        f"rm -rf {directory}; mkdir -p {directory} && chmod 755 {directory} && "
        f"printf '{lines}' > {path} && chown 0:0 {path} && chmod 600 {path}"}))
    assert made['status'] == 'success', made
    return path


async def _content_and_owner(client, path):
    r = _json(await client.call_tool("ssh_cmd_run", {"use_sudo": True, "command":
        f"cat {path}; echo '---'; ls -ln {path} | awk '{{print $1, $3, $4}}'"}))
    content, meta = r['output'].split('---\n')
    return content, meta.split()


@pytest.mark.asyncio
@skip_on_windows
async def test_sudo_line_edits_on_root_only_file(mcp_test_environment):
    print_test_header("Testing sudo line edits on a root-only file")
    directory = f"/tmp/sudo_edit_test_{int(time.time())}"
    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            path = await _root_file(client, directory)

            for params in ({"match_line": "private=before", "new_line": "private=after"},
                           {"match_line": "private=after", "new_line": "private=forced", "force": True}):
                r = _json(await client.call_tool("ssh_file_replace_line",
                                                 {"file_path": path, "use_sudo": True, **params}))
                assert r['success'] is True and 'No changes' not in r.get('message', ''), r
            content, meta = await _content_and_owner(client, path)
            assert content == "a=1\nprivate=forced\nz=9\n", content
            assert meta == ["-rw-------", "0", "0"], meta

            # A line that isn't there must be an error - never "success / no changes needed"
            r = _json(await client.call_tool("ssh_file_replace_line", {
                "file_path": path, "match_line": "not-there", "new_line": "x", "use_sudo": True, "force": True}))
            assert r['success'] is False and "not found" in r['error'], r

            r = _json(await client.call_tool("ssh_file_insert_lines_after_match", {
                "file_path": path, "match_line": "a=1", "lines_to_insert": ["b=2"], "use_sudo": True}))
            assert r['success'] is True, r
            r = _json(await client.call_tool("ssh_file_delete_line_by_content", {
                "file_path": path, "match_line": "z=9", "use_sudo": True}))
            assert r['success'] is True, r
            content, meta = await _content_and_owner(client, path)
            assert content == "a=1\nb=2\nprivate=forced\n" and meta == ["-rw-------", "0", "0"], (content, meta)
        finally:
            await client.call_tool("ssh_cmd_run", {"command": f"rm -rf {directory}", "use_sudo": True})
            await disconnect_ssh(client)
            print_test_footer()


@pytest.mark.asyncio
@skip_on_windows
async def test_sudo_write_keeps_existing_owner_and_mode(mcp_test_environment):
    print_test_header("Testing that a sudo write keeps an existing file's owner and mode")
    directory = f"/tmp/sudo_write_test_{int(time.time())}"
    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            path = await _root_file(client, directory)

            r = _json(await client.call_tool("ssh_file_write", {"file_path": path, "content": "one\n", "use_sudo": True}))
            assert r['success'], r
            content, meta = await _content_and_owner(client, path)
            assert content == "one\n" and meta == ["-rw-------", "0", "0"], \
                f"sudo write changed the owner/mode of a root-only file: {meta}"

            r = _json(await client.call_tool("ssh_file_write", {
                "file_path": path, "content": "two\n", "use_sudo": True, "mode": 0o640}))
            assert r['success'], r
            content, meta = await _content_and_owner(client, path)
            assert content == "two\n" and meta == ["-rw-r-----", "0", "0"], meta

            # A NEW file written with sudo is still owned by the connected user (unchanged)
            new = f"{directory}/new.conf"
            r = _json(await client.call_tool("ssh_file_write", {"file_path": new, "content": "n\n", "use_sudo": True}))
            assert r['success'], r
            check = _json(await client.call_tool("ssh_cmd_run", {"command": f"ls -ln {new} | awk '{{print $3}}'; id -u"}))
            owner, me = check['output'].split()
            assert owner == me, check
        finally:
            await client.call_tool("ssh_cmd_run", {"command": f"rm -rf {directory}", "use_sudo": True})
            await disconnect_ssh(client)
            print_test_footer()
