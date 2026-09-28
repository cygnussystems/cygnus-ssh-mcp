"""ssh_dir_calc_size and ssh_dir_copy report the SUM OF REGULAR FILE SIZES (round-4
issue 4, issues/_archive_/2026-09-26-linux-dir-size-counts-directory-bytes.md), and ssh_dir_copy
produces an exact copy of the tree.

Found while fixing issue 4: on Linux/macOS, ssh_dir_copy also copied every subdirectory
and every file inside it into the destination ROOT, so nested files were duplicated,
flattened, at the top level (and the 'src/*' variant skipped hidden files).

Linux used 'du -sb', which also counts each directory's own size (4 KB each), so a tree
with many folders came out too big (69,632 extra bytes for 17 folders). macOS/Windows
already summed files only. The tree here has 8 folders and files of known sizes.
"""
import pytest
import json
import time
import logging
from conftest import (
    print_test_header, print_test_footer, make_connection, disconnect_ssh,
    mcp_test_environment, extract_result_text, TEST_WORKSPACE, PATH_SEP, cleanup_command,
    IS_WINDOWS, skip_on_windows
)

from cygnus_ssh_mcp.server import mcp
from fastmcp import Client

logger = logging.getLogger(__name__)

# (relative path, size in bytes) - 8 directories in total (root + 7), 10 files
FILES = [("a.txt", 1), ("b.txt", 1000), ("d1/c.txt", 4096), ("d1/d2/e.txt", 123),
         ("d1/d2/d3/f.txt", 77), ("d4/g.txt", 5000), ("d4/d5/h.txt", 1), ("d6/i.txt", 10),
         ("d6/d7/j.txt", 2048), ("d6/d7/k.txt", 0), (".hidden", 5), ("d1/.hidden2", 6)]
EXPECTED = sum(size for _, size in FILES)


async def _relative_files(client, root):
    """Sorted relative paths (with '/') of all regular files under root."""
    if IS_WINDOWS:
        cmd = (f'powershell -NoProfile -Command "Get-ChildItem -LiteralPath \'{root}\' -Recurse -File -Force | '
               f'ForEach-Object {{ $_.FullName.Substring({len(root) + 1}) }}"')
    else:
        cmd = f"cd '{root}' && find . -type f | sed 's|^./||'"
    run = json.loads(extract_result_text(await client.call_tool("ssh_cmd_run", {"command": cmd})))
    assert run['status'] == 'success', run
    return sorted(line.strip().replace("\\", "/") for line in run['output'].splitlines() if line.strip())


async def _result(client, tool, params):
    """The tool's result, following an in_progress handoff if one happens."""
    response = json.loads(extract_result_text(await client.call_tool(tool, params)))
    if isinstance(response, dict) and response.get('status') == 'in_progress':
        for _ in range(120):
            status = json.loads(extract_result_text(await client.call_tool(
                "ssh_cmd_check_status", {"handle_id": response['handle_id'], "wait_seconds": 2})))
            if status['status'] != 'running':
                return status.get('result', status)
    return response


@pytest.mark.asyncio
async def test_size_and_copy_count_regular_files_only(mcp_test_environment):
    print_test_header("Testing directory size = sum of regular file sizes")
    base = f"{TEST_WORKSPACE}{PATH_SEP}dir_size_{int(time.time())}"
    src, dest = f"{base}{PATH_SEP}src", f"{base}{PATH_SEP}copy"

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            for rel, size in FILES:
                path = src + PATH_SEP + rel.replace("/", PATH_SEP)
                written = json.loads(extract_result_text(await client.call_tool("ssh_file_write", {
                    "file_path": path, "content": "x" * size, "create_dirs": True})))
                assert written.get('success'), written

            size = await _result(client, "ssh_dir_calc_size", {"path": src})
            assert size['size_bytes'] == EXPECTED, f"calc_size {size['size_bytes']} != {EXPECTED}: {size}"

            copied = await _result(client, "ssh_dir_copy", {"source_path": src, "destination_path": dest})
            expected_files = sorted(rel for rel, _ in FILES)
            assert await _relative_files(client, dest) == expected_files, "the copy isn't an exact copy of the tree"
            assert copied.get('bytes_copied') == EXPECTED, f"bytes_copied != {EXPECTED}: {copied}"
            assert copied.get('files_copied') == len(FILES), copied
        finally:
            await client.call_tool("ssh_cmd_run", {"command": cleanup_command(base), "wait_timeout": 45})
            await disconnect_ssh(client)
            print_test_footer()


@pytest.mark.asyncio
@skip_on_windows
async def test_copy_keeps_symlinks_as_symlinks(mcp_test_environment):
    """preserve_symlinks (the default) copies a symlink as a symlink, not its target."""
    print_test_header("Testing ssh_dir_copy with a symlink")
    base = f"{TEST_WORKSPACE}/dir_copy_link_{int(time.time())}"

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            made = json.loads(extract_result_text(await client.call_tool("ssh_cmd_run", {
                "command": f"mkdir -p {base}/src/sub && echo data > {base}/src/sub/target.txt && "
                           f"ln -s sub/target.txt {base}/src/link.txt"})))
            assert made['status'] == 'success', made
            await _result(client, "ssh_dir_copy", {"source_path": f"{base}/src", "destination_path": f"{base}/copy"})
            check = json.loads(extract_result_text(await client.call_tool("ssh_cmd_run", {
                "command": f"[ -L {base}/copy/link.txt ] && readlink {base}/copy/link.txt && cat {base}/copy/link.txt"})))
            assert check['status'] == 'success', check
            assert check['output'].split() == ["sub/target.txt", "data"], check
        finally:
            await client.call_tool("ssh_cmd_run", {"command": f"rm -rf {base}"})
            await disconnect_ssh(client)
            print_test_footer()
