import pytest
import json
from conftest import (
    print_test_header,
    print_test_footer,
    make_connection,
    disconnect_ssh,
    remote_temp_path,
    extract_result_text,
    cleanup_command,
    cleanup_remote_path,
    linux_only,
    TEST_WORKSPACE,
    PATH_SEP,
    IS_WINDOWS
)

from cygnus_ssh_mcp.server import mcp
from fastmcp import Client


@pytest.mark.asyncio
async def test_ssh_search_files(mcp_test_environment):
    """Test searching for files in directories."""
    print_test_header("Testing 'ssh_dir_search_glob' tool")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Connection failed"
            test_dir = remote_temp_path("ssh_test_search")

            # Setup test files using MCP tools (cross-platform)
            await client.call_tool("ssh_dir_mkdir", {"path": test_dir})
            await client.call_tool("ssh_dir_mkdir", {"path": f"{test_dir}{PATH_SEP}dir1"})
            await client.call_tool("ssh_dir_mkdir", {"path": f"{test_dir}{PATH_SEP}dir2"})
            await client.call_tool("ssh_dir_mkdir", {"path": f"{test_dir}{PATH_SEP}dir3"})

            await client.call_tool("ssh_file_write", {"file_path": f"{test_dir}{PATH_SEP}file1.txt", "content": "test"})
            await client.call_tool("ssh_file_write", {"file_path": f"{test_dir}{PATH_SEP}file2.log", "content": "test"})
            await client.call_tool("ssh_file_write", {"file_path": f"{test_dir}{PATH_SEP}dir1{PATH_SEP}file3.txt", "content": "test"})
            await client.call_tool("ssh_file_write", {"file_path": f"{test_dir}{PATH_SEP}dir2{PATH_SEP}file4.log", "content": "test"})

            # Test .txt files search
            result = await client.call_tool("ssh_dir_search_glob", {
                "path": test_dir,
                "pattern": "*.txt",
                "max_depth": None,
                "include_dirs": False
            })
            files = json.loads(extract_result_text(result))
            paths = [f['path'] for f in files]
            # Check that we found both .txt files
            txt_count = sum(1 for p in paths if p.endswith('.txt'))
            assert txt_count >= 2, f"Expected at least 2 .txt files, got {txt_count}: {paths}"

            # Test .log files search
            result = await client.call_tool("ssh_dir_search_glob", {
                "path": test_dir,
                "pattern": "*.log",
                "max_depth": None,
                "include_dirs": False
            })
            log_files = json.loads(extract_result_text(result))
            assert len(log_files) >= 2, f"Expected at least 2 .log files, got {len(log_files)}"

        finally:
            await client.call_tool("ssh_dir_remove", {"path": test_dir, "recursive": True})
            await disconnect_ssh(client)

    print_test_footer()


@pytest.mark.asyncio
async def test_ssh_directory_size(mcp_test_environment):
    """Test calculating directory size (cross-platform)."""
    print_test_header("Testing 'ssh_dir_calc_size' tool")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Connection failed"
            test_dir = remote_temp_path("ssh_test_size")

            # Create test directory using MCP tools (cross-platform)
            await client.call_tool("ssh_dir_mkdir", {"path": test_dir})

            # Create files with known sizes using ssh_file_write
            content_1kb = "x" * 1024  # 1KB of data
            content_10kb = content_1kb * 10  # 10KB
            content_20kb = content_1kb * 20  # 20KB

            await client.call_tool("ssh_file_write", {
                "file_path": f"{test_dir}{PATH_SEP}file1.txt",
                "content": content_10kb
            })
            await client.call_tool("ssh_file_write", {
                "file_path": f"{test_dir}{PATH_SEP}file2.txt",
                "content": content_20kb
            })

            result = await client.call_tool("ssh_dir_calc_size", {"path": test_dir})
            size_data = json.loads(extract_result_text(result))

            assert 'size_bytes' in size_data and 'size_human' in size_data
            # Should be at least 30KB (30 * 1024 = 30720 bytes)
            assert size_data['size_bytes'] >= 30000, f"Expected at least 30KB, got {size_data['size_bytes']} bytes"

        finally:
            await client.call_tool("ssh_dir_remove", {"path": test_dir, "recursive": True})
            await disconnect_ssh(client)

    print_test_footer()


@pytest.mark.asyncio
async def test_ssh_list_directory(mcp_test_environment):
    """Test recursive directory listing."""
    print_test_header("Testing 'ssh_dir_list_advanced' tool")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Connection failed"
            test_dir = remote_temp_path("ssh_test_list")

            # Setup using MCP tools (cross-platform)
            await client.call_tool("ssh_dir_mkdir", {"path": test_dir})
            await client.call_tool("ssh_dir_mkdir", {"path": f"{test_dir}{PATH_SEP}dir1"})
            await client.call_tool("ssh_dir_mkdir", {"path": f"{test_dir}{PATH_SEP}dir1{PATH_SEP}subdir1"})
            await client.call_tool("ssh_dir_mkdir", {"path": f"{test_dir}{PATH_SEP}dir2"})

            await client.call_tool("ssh_file_write", {"file_path": f"{test_dir}{PATH_SEP}file1.txt", "content": "test"})
            await client.call_tool("ssh_file_write", {"file_path": f"{test_dir}{PATH_SEP}dir1{PATH_SEP}file2.txt", "content": "test"})
            await client.call_tool("ssh_file_write", {"file_path": f"{test_dir}{PATH_SEP}dir1{PATH_SEP}subdir1{PATH_SEP}file3.txt", "content": "test"})
            await client.call_tool("ssh_file_write", {"file_path": f"{test_dir}{PATH_SEP}dir2{PATH_SEP}file4.txt", "content": "test"})

            # Test full recursive list
            result = await client.call_tool("ssh_dir_list_advanced", {"path": test_dir})
            entries = json.loads(extract_result_text(result))
            paths = [e['path'] for e in entries]

            # Check we found the expected entries (use path-agnostic checks)
            found_subdir1_file = any('subdir1' in p and 'file3' in p for p in paths)
            found_dir1 = any(p.endswith('dir1') or p.endswith('dir1\\') or p.endswith('dir1/') for p in paths)
            found_dir2 = any(p.endswith('dir2') or p.endswith('dir2\\') or p.endswith('dir2/') for p in paths)

            assert found_subdir1_file, f"Expected to find file3.txt in subdir1, got: {paths}"
            assert found_dir1, f"Expected to find dir1, got: {paths}"
            assert found_dir2, f"Expected to find dir2, got: {paths}"

            # Test depth-limited list
            result = await client.call_tool("ssh_dir_list_advanced", {
                "path": test_dir,
                "max_depth": 1
            })
            shallow_entries = json.loads(extract_result_text(result))
            # At depth 1, should not include subdir1 file contents
            # Windows and Linux have slightly different depth semantics
            # Windows includes depth 0 + depth 1 items, Linux may vary
            # Key check: file3.txt (in subdir1) should NOT be in the list
            shallow_paths = [e['path'] for e in shallow_entries]
            assert not any('file3' in p for p in shallow_paths), \
                f"file3.txt should not appear at depth 1, but got: {shallow_paths}"

        finally:
            await client.call_tool("ssh_dir_remove", {"path": test_dir, "recursive": True})
            await disconnect_ssh(client)

    print_test_footer()


@pytest.mark.asyncio
async def test_ssh_dir_mkdir_sudo(mcp_test_environment):
    """Test ssh_dir_mkdir with sudo=True (cross-platform)."""
    print_test_header("Testing 'ssh_dir_mkdir' with sudo")
    test_dir_base = "test_mcp_sudo_dir"
    test_dir = remote_temp_path(test_dir_base)

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            # Ensure directory does not exist (cleanup from previous failed run if any)
            await cleanup_remote_path(client, test_dir)

            # Test create directory with sudo (on Windows: ignored, on Linux: runs as root)
            mkdir_result = await client.call_tool("ssh_dir_mkdir", {
                "path": test_dir,
                "mode": 0o755,
                "use_sudo": True
            })
            mkdir_json = json.loads(extract_result_text(mkdir_result))
            assert mkdir_json['status'] == 'success', f"ssh_dir_mkdir with sudo failed: {mkdir_json.get('message', '')}"

            # Verify directory exists using cross-platform ssh_file_stat
            stat_result = await client.call_tool("ssh_file_stat", {"path": test_dir})
            stat_json = json.loads(extract_result_text(stat_result))
            assert stat_json.get('exists') == True, "Directory was not created"
            assert stat_json.get('type') == 'directory', f"Expected directory, got {stat_json.get('type')}"

            # On Linux only: verify root ownership (stat -c doesn't exist on Windows)
            if not IS_WINDOWS:
                owner_result = await client.call_tool("ssh_cmd_run", {
                    "command": f"stat -c '%U' {test_dir}",
                    "io_timeout": 5.0
                })
                owner_json = json.loads(extract_result_text(owner_result))
                if owner_json['status'] == 'success':
                    owner = owner_json['output'].strip()
                    assert owner == "root", f"Directory owner should be root when created with sudo, but was '{owner}'"

        finally:
            await cleanup_remote_path(client, test_dir)
            await disconnect_ssh(client)
    print_test_footer()


@pytest.mark.asyncio
async def test_ssh_dir_remove_recursive_with_content(mcp_test_environment):
    """Test ssh_dir_remove with recursive=True on a directory with content."""
    print_test_header("Testing 'ssh_dir_remove' with recursive=True and content")

    parent_dir_base = "test_mcp_parent_rec_remove"
    parent_dir = remote_temp_path(parent_dir_base)
    inner_file = f"{parent_dir}{PATH_SEP}somefile.txt"

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            # Setup: Create directory and a file inside it using MCP tools
            await client.call_tool("ssh_dir_mkdir", {"path": parent_dir, "mode": 0o755})
            await client.call_tool("ssh_file_write", {"file_path": inner_file, "content": "hello"})

            # Test remove directory recursively
            rmdir_result = await client.call_tool("ssh_dir_remove", {
                "path": parent_dir,
                "recursive": True
            })
            rmdir_json = json.loads(extract_result_text(rmdir_result))
            assert rmdir_json['status'] == 'success', f"ssh_dir_remove recursive failed: {rmdir_json.get('message', '')}"

            # Verify directory no longer exists
            stat_result = await client.call_tool("ssh_file_stat", {"path": parent_dir})
            stat_json = json.loads(extract_result_text(stat_result))
            assert stat_json.get('exists') == False, f"Directory '{parent_dir}' should have been removed."

        finally:
            # Ensure cleanup if test failed before removal
            stat_check_result = await client.call_tool("ssh_file_stat", {"path": parent_dir})
            try:
                stat_check_json = json.loads(extract_result_text(stat_check_result))
                if stat_check_json.get('exists') == True:
                    await client.call_tool("ssh_dir_remove", {
                        "path": parent_dir,
                        "recursive": True
                    })
            except (json.JSONDecodeError, IndexError, AttributeError) as e:
                print(f"Warning: Could not parse stat check result during cleanup: {e}")

            await disconnect_ssh(client)
    print_test_footer()


@pytest.mark.asyncio
async def test_ssh_dir_remove_non_empty_no_recursive(mcp_test_environment):
    """Test ssh_dir_remove with recursive=False on a non-empty directory (should fail)."""
    print_test_header("Testing 'ssh_dir_remove' with recursive=False on non-empty directory")

    parent_dir_base = "test_mcp_parent_nonrec_remove"
    parent_dir = remote_temp_path(parent_dir_base)
    inner_file = f"{parent_dir}{PATH_SEP}somefile.txt"

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            # Setup: Create directory and a file inside it using MCP tools
            await client.call_tool("ssh_dir_mkdir", {"path": parent_dir, "mode": 0o755})
            await client.call_tool("ssh_file_write", {"file_path": inner_file, "content": "hello"})

            # Test remove directory non-recursively (expect failure)
            with pytest.raises(Exception) as excinfo:
                await client.call_tool("ssh_dir_remove", {
                    "path": parent_dir,
                    "recursive": False
                })

            # Check if the exception message contains relevant info
            error_msg = str(excinfo.value).lower()
            assert "not empty" in error_msg or "failed" in error_msg or "directory" in error_msg, \
                f"Expected error about non-empty directory, got: {excinfo.value}"

            # Verify directory still exists
            stat_result = await client.call_tool("ssh_file_stat", {"path": parent_dir})
            stat_json = json.loads(extract_result_text(stat_result))
            assert stat_json.get('exists') == True, f"Directory '{parent_dir}' should still exist."

        finally:
            # Cleanup (recursively)
            await client.call_tool("ssh_dir_remove", {
                "path": parent_dir,
                "recursive": True,
                "use_sudo": False
            })
            await disconnect_ssh(client)
    print_test_footer()
