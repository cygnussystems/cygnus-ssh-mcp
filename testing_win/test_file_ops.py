"""Tests for Windows file operations."""
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
async def test_windows_file_write_read(mcp_test_environment):
    """Test writing and reading files on Windows."""
    print_test_header("test_windows_file_write_read")

    async with Client(mcp) as client:
        connected = await make_connection(client)
        assert connected, "Failed to connect"

        test_file = remote_temp_path("test_file.txt")
        test_content = "Hello Windows!\nLine 2\nLine 3 with special chars: @#$%"

        # Write file
        write_result = await client.call_tool("ssh_file_write", {
            "file_path": test_file,
            "content": test_content
        })
        write_text = extract_result_text(write_result)
        write_json = json.loads(write_text)
        assert write_json.get('success'), f"Write failed: {write_json}"
        print(f"Wrote {write_json.get('bytes_written')} bytes to {test_file}")

        # Read file back using type command
        read_result = await client.call_tool("ssh_cmd_run", {"command": f"type \"{test_file}\""})
        read_text = extract_result_text(read_result)
        read_json = json.loads(read_text)
        assert read_json.get('exit_code') == 0, f"Read failed: {read_json}"
        assert "Hello Windows!" in read_json.get('output', ''), "Content mismatch"
        print("File content verified")

        # Clean up
        await client.call_tool("ssh_cmd_run", {"command": f"del \"{test_file}\""})

    print_test_footer()


@pytest.mark.asyncio
async def test_windows_file_find_pattern(mcp_test_environment):
    """Test finding patterns in files on Windows."""
    print_test_header("test_windows_file_find_pattern")

    async with Client(mcp) as client:
        connected = await make_connection(client)
        assert connected, "Failed to connect"

        test_file = remote_temp_path("pattern_test.txt")
        test_content = "Line 1: Hello\nLine 2: World\nLine 3: Hello World\nLine 4: Goodbye"

        # Write file
        await client.call_tool("ssh_file_write", {
            "file_path": test_file,
            "content": test_content
        })

        # Find pattern
        find_result = await client.call_tool("ssh_file_find_lines_with_pattern", {
            "file_path": test_file,
            "pattern": "Hello"
        })
        find_text = extract_result_text(find_result)
        find_json = json.loads(find_text)

        assert find_json.get('total_matches') == 2, f"Expected 2 matches, got {find_json.get('total_matches')}"
        print(f"Found {find_json.get('total_matches')} matches for 'Hello'")

        # Verify line numbers
        matches = find_json.get('matches', [])
        line_numbers = [m.get('line_number') for m in matches]
        assert 1 in line_numbers, "Line 1 should match"
        assert 3 in line_numbers, "Line 3 should match"

        # Clean up
        await client.call_tool("ssh_cmd_run", {"command": f"del \"{test_file}\""})

    print_test_footer()


@pytest.mark.asyncio
async def test_windows_file_stat(mcp_test_environment):
    """Test getting file stats on Windows."""
    print_test_header("test_windows_file_stat")

    async with Client(mcp) as client:
        connected = await make_connection(client)
        assert connected, "Failed to connect"

        test_file = remote_temp_path("stat_test.txt")
        test_content = "Test content for stat"

        # Write file
        await client.call_tool("ssh_file_write", {
            "file_path": test_file,
            "content": test_content
        })

        # Get file stat
        stat_result = await client.call_tool("ssh_file_stat", {"path": test_file})
        stat_text = extract_result_text(stat_result)
        stat_json = json.loads(stat_text)

        assert stat_json.get('exists'), f"File should exist: {stat_json}"
        assert stat_json.get('type') == 'file', f"Should be a file: {stat_json}"
        assert stat_json.get('size') == len(test_content), f"Size mismatch: {stat_json.get('size')} vs {len(test_content)}"
        print(f"File stat: size={stat_json.get('size')}, type={stat_json.get('type')}")

        # Clean up
        await client.call_tool("ssh_cmd_run", {"command": f"del \"{test_file}\""})

    print_test_footer()


@pytest.mark.asyncio
async def test_windows_file_copy(mcp_test_environment):
    """Test copying files on Windows."""
    print_test_header("test_windows_file_copy")

    async with Client(mcp) as client:
        connected = await make_connection(client)
        assert connected, "Failed to connect"

        source_file = remote_temp_path("copy_source.txt")
        dest_file = remote_temp_path("copy_dest.txt")
        test_content = "Content to copy"

        # Write source file
        await client.call_tool("ssh_file_write", {
            "file_path": source_file,
            "content": test_content
        })

        # Copy file
        copy_result = await client.call_tool("ssh_file_copy", {
            "source_path": source_file,
            "destination_path": dest_file
        })
        copy_text = extract_result_text(copy_result)
        copy_json = json.loads(copy_text)
        assert copy_json.get('success'), f"Copy failed: {copy_json}"
        print(f"Copied to {copy_json.get('copied_to')}")

        # Verify destination exists
        stat_result = await client.call_tool("ssh_file_stat", {"path": dest_file})
        stat_text = extract_result_text(stat_result)
        stat_json = json.loads(stat_text)
        assert stat_json.get('exists'), "Destination file should exist"

        # Clean up
        await client.call_tool("ssh_cmd_run", {"command": f"del \"{source_file}\" \"{dest_file}\""})

    print_test_footer()


@pytest.mark.asyncio
async def test_windows_file_move(mcp_test_environment):
    """Test moving files on Windows."""
    print_test_header("test_windows_file_move")

    async with Client(mcp) as client:
        connected = await make_connection(client)
        assert connected, "Failed to connect"

        source_file = remote_temp_path("move_source.txt")
        dest_file = remote_temp_path("move_dest.txt")
        test_content = "Content to move"

        # Write source file
        await client.call_tool("ssh_file_write", {
            "file_path": source_file,
            "content": test_content
        })

        # Move file
        move_result = await client.call_tool("ssh_file_move", {
            "source": source_file,
            "destination": dest_file
        })
        move_text = extract_result_text(move_result)
        move_json = json.loads(move_text)
        assert move_json.get('success'), f"Move failed: {move_json}"
        print(f"Moved file: {move_json.get('message')}")

        # Verify source no longer exists
        stat_result = await client.call_tool("ssh_file_stat", {"path": source_file})
        stat_text = extract_result_text(stat_result)
        stat_json = json.loads(stat_text)
        assert not stat_json.get('exists'), "Source file should not exist after move"

        # Verify destination exists
        stat_result = await client.call_tool("ssh_file_stat", {"path": dest_file})
        stat_text = extract_result_text(stat_result)
        stat_json = json.loads(stat_text)
        assert stat_json.get('exists'), "Destination file should exist after move"

        # Clean up
        await client.call_tool("ssh_cmd_run", {"command": f"del \"{dest_file}\""})

    print_test_footer()
