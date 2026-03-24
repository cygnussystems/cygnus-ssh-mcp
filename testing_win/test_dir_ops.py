"""Tests for Windows directory operations."""
import pytest
import json
import logging
from fastmcp import Client
from cygnus_ssh_mcp.server import mcp

from conftest import (
    make_connection, extract_result_text, print_test_header, print_test_footer,
    remote_temp_path, mcp_test_environment
)

logger = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_windows_dir_create_remove(mcp_test_environment):
    """Test creating and removing directories on Windows."""
    print_test_header("test_windows_dir_create_remove")

    async with Client(mcp) as client:
        connected = await make_connection(client)
        assert connected, "Failed to connect"

        test_dir = remote_temp_path("test_dir")

        # Create directory
        mkdir_result = await client.call_tool("ssh_dir_mkdir", {"path": test_dir})
        mkdir_text = extract_result_text(mkdir_result)
        mkdir_json = json.loads(mkdir_text)
        assert mkdir_json.get('status') == 'success', f"mkdir failed: {mkdir_json}"
        print(f"Created directory: {test_dir}")

        # Verify it exists
        stat_result = await client.call_tool("ssh_file_stat", {"path": test_dir})
        stat_text = extract_result_text(stat_result)
        stat_json = json.loads(stat_text)
        assert stat_json.get('exists'), "Directory should exist"
        assert stat_json.get('type') == 'directory', f"Should be directory: {stat_json.get('type')}"

        # Remove directory
        rmdir_result = await client.call_tool("ssh_dir_remove", {"path": test_dir})
        rmdir_text = extract_result_text(rmdir_result)
        rmdir_json = json.loads(rmdir_text)
        assert rmdir_json.get('status') == 'success', f"rmdir failed: {rmdir_json}"
        print("Removed directory")

        # Verify it's gone
        stat_result = await client.call_tool("ssh_file_stat", {"path": test_dir})
        stat_text = extract_result_text(stat_result)
        stat_json = json.loads(stat_text)
        assert not stat_json.get('exists'), "Directory should not exist after removal"

    print_test_footer()


@pytest.mark.asyncio
async def test_windows_dir_list(mcp_test_environment):
    """Test listing directory contents on Windows."""
    print_test_header("test_windows_dir_list")

    async with Client(mcp) as client:
        connected = await make_connection(client)
        assert connected, "Failed to connect"

        test_dir = remote_temp_path("list_test")

        # Create directory with files
        await client.call_tool("ssh_dir_mkdir", {"path": test_dir})
        await client.call_tool("ssh_file_write", {"file_path": f"{test_dir}\\file1.txt", "content": "test1"})
        await client.call_tool("ssh_file_write", {"file_path": f"{test_dir}\\file2.txt", "content": "test2"})
        await client.call_tool("ssh_file_write", {"file_path": f"{test_dir}\\data.log", "content": "log"})

        # List directory
        list_result = await client.call_tool("ssh_dir_list_files_basic", {"path": test_dir})
        list_text = extract_result_text(list_result)
        list_json = json.loads(list_text)

        # The result can be a dict with 'result' key or just a list
        files = list_json.get('result', []) if isinstance(list_json, dict) else list_json
        assert len(files) == 3, f"Expected 3 files, got {len(files)}"
        assert 'file1.txt' in files, "file1.txt should be in list"
        assert 'file2.txt' in files, "file2.txt should be in list"
        assert 'data.log' in files, "data.log should be in list"
        print(f"Listed {len(files)} files: {files}")

        # Clean up
        await client.call_tool("ssh_dir_delete", {"path": test_dir, "dry_run": False})

    print_test_footer()


@pytest.mark.asyncio
async def test_windows_dir_delete_recursive(mcp_test_environment):
    """Test recursive directory deletion on Windows."""
    print_test_header("test_windows_dir_delete_recursive")

    async with Client(mcp) as client:
        connected = await make_connection(client)
        assert connected, "Failed to connect"

        test_dir = remote_temp_path("delete_test")
        sub_dir = f"{test_dir}\\subdir"

        # Create nested structure
        await client.call_tool("ssh_dir_mkdir", {"path": sub_dir})
        await client.call_tool("ssh_file_write", {"file_path": f"{test_dir}\\root.txt", "content": "root"})
        await client.call_tool("ssh_file_write", {"file_path": f"{sub_dir}\\nested.txt", "content": "nested"})

        # Dry run first
        dry_result = await client.call_tool("ssh_dir_delete", {"path": test_dir, "dry_run": True})
        dry_text = extract_result_text(dry_result)
        dry_json = json.loads(dry_text)
        assert dry_json.get('dry_run'), "Should be dry run"
        items = dry_json.get('deleted_items', [])
        assert len(items) >= 3, f"Expected at least 3 items, got {len(items)}"
        print(f"Dry run would delete {len(items)} items")

        # Actual delete
        del_result = await client.call_tool("ssh_dir_delete", {"path": test_dir, "dry_run": False})
        del_text = extract_result_text(del_result)
        del_json = json.loads(del_text)
        assert del_json.get('status') == 'success', f"Delete failed: {del_json}"
        print(f"Deleted {len(del_json.get('deleted_items', []))} items")

        # Verify gone
        stat_result = await client.call_tool("ssh_file_stat", {"path": test_dir})
        stat_text = extract_result_text(stat_result)
        stat_json = json.loads(stat_text)
        assert not stat_json.get('exists'), "Directory should be deleted"

    print_test_footer()


@pytest.mark.asyncio
async def test_windows_dir_search_glob(mcp_test_environment):
    """Test searching for files by pattern on Windows."""
    print_test_header("test_windows_dir_search_glob")

    async with Client(mcp) as client:
        connected = await make_connection(client)
        assert connected, "Failed to connect"

        test_dir = remote_temp_path("glob_test")

        # Create files with different extensions
        await client.call_tool("ssh_dir_mkdir", {"path": test_dir})
        await client.call_tool("ssh_file_write", {"file_path": f"{test_dir}\\file1.txt", "content": "txt1"})
        await client.call_tool("ssh_file_write", {"file_path": f"{test_dir}\\file2.txt", "content": "txt2"})
        await client.call_tool("ssh_file_write", {"file_path": f"{test_dir}\\data.log", "content": "log1"})
        await client.call_tool("ssh_file_write", {"file_path": f"{test_dir}\\app.log", "content": "log2"})

        # Search for .txt files
        search_result = await client.call_tool("ssh_dir_search_glob", {
            "path": test_dir,
            "pattern": "*.txt"
        })
        search_text = extract_result_text(search_result)
        search_json = json.loads(search_text)

        # Result can be a list or dict with 'result' key
        results = search_json.get('result', []) if isinstance(search_json, dict) else search_json
        assert len(results) == 2, f"Expected 2 .txt files, got {len(results)}"
        print(f"Found {len(results)} .txt files")

        # Search for .log files
        search_result = await client.call_tool("ssh_dir_search_glob", {
            "path": test_dir,
            "pattern": "*.log"
        })
        search_text = extract_result_text(search_result)
        search_json = json.loads(search_text)

        # Result can be a list or dict with 'result' key
        results = search_json.get('result', []) if isinstance(search_json, dict) else search_json
        assert len(results) == 2, f"Expected 2 .log files, got {len(results)}"
        print(f"Found {len(results)} .log files")

        # Clean up
        await client.call_tool("ssh_dir_delete", {"path": test_dir, "dry_run": False})

    print_test_footer()


@pytest.mark.asyncio
async def test_windows_dir_copy(mcp_test_environment):
    """Test copying directories on Windows."""
    print_test_header("test_windows_dir_copy")

    async with Client(mcp) as client:
        connected = await make_connection(client)
        assert connected, "Failed to connect"

        source_dir = remote_temp_path("copy_source")
        dest_dir = remote_temp_path("copy_dest")

        # Create source with files
        await client.call_tool("ssh_dir_mkdir", {"path": source_dir})
        await client.call_tool("ssh_file_write", {"file_path": f"{source_dir}\\file1.txt", "content": "content1"})
        await client.call_tool("ssh_file_write", {"file_path": f"{source_dir}\\file2.txt", "content": "content2"})

        # Copy directory
        copy_result = await client.call_tool("ssh_dir_copy", {
            "source_path": source_dir,
            "destination_path": dest_dir
        })
        copy_text = extract_result_text(copy_result)
        copy_json = json.loads(copy_text)
        assert copy_json.get('status') == 'success', f"Copy failed: {copy_json}"
        print(f"Copied {copy_json.get('files_copied')} files, {copy_json.get('bytes_copied')} bytes")

        # Verify destination exists with files
        stat_result = await client.call_tool("ssh_file_stat", {"path": dest_dir})
        stat_text = extract_result_text(stat_result)
        stat_json = json.loads(stat_text)
        assert stat_json.get('exists'), "Destination should exist"

        # Clean up
        await client.call_tool("ssh_dir_delete", {"path": source_dir, "dry_run": False})
        await client.call_tool("ssh_dir_delete", {"path": dest_dir, "dry_run": False})

    print_test_footer()


@pytest.mark.asyncio
async def test_windows_dir_search_content(mcp_test_environment):
    """Test searching file contents in directory on Windows."""
    print_test_header("test_windows_dir_search_content")

    async with Client(mcp) as client:
        connected = await make_connection(client)
        assert connected, "Failed to connect"

        test_dir = remote_temp_path("content_search")

        # Create files with searchable content
        await client.call_tool("ssh_dir_mkdir", {"path": test_dir})
        await client.call_tool("ssh_file_write", {"file_path": f"{test_dir}\\file1.txt", "content": "Hello World\nGoodbye World"})
        await client.call_tool("ssh_file_write", {"file_path": f"{test_dir}\\file2.txt", "content": "Hello Universe\nHello Galaxy"})
        await client.call_tool("ssh_file_write", {"file_path": f"{test_dir}\\file3.txt", "content": "No match here"})

        # Search for "Hello"
        search_result = await client.call_tool("ssh_dir_search_files_content", {
            "dir_path": test_dir,
            "pattern": "Hello"
        })
        search_text = extract_result_text(search_result)
        search_json = json.loads(search_text)

        # Result can be a list or dict with 'result' key
        results = search_json.get('result', []) if isinstance(search_json, dict) else search_json
        assert len(results) == 3, f"Expected 3 matches for 'Hello', got {len(results)}"
        print(f"Found {len(results)} lines containing 'Hello'")

        # Clean up
        await client.call_tool("ssh_dir_delete", {"path": test_dir, "dry_run": False})

    print_test_footer()
