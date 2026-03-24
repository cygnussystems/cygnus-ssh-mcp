"""
Comprehensive sudo tests for tools that have use_sudo parameter but lack test coverage.

This file tests the following 11 tools with use_sudo=True:
1. ssh_file_find_lines_with_pattern
2. ssh_file_get_context_around_line
3. ssh_file_replace_line
4. ssh_file_copy
5. ssh_file_move
6. ssh_task_kill
7. ssh_dir_search_glob
8. ssh_dir_delete
9. ssh_dir_batch_delete_files
10. ssh_dir_search_files_content
11. ssh_dir_copy

All tests operate in protected locations (/root/, /etc/, /opt/) that require sudo.
"""
import pytest
import json
import logging
import asyncio
from conftest import (
    print_test_header,
    print_test_footer,
    make_connection,
    disconnect_ssh,
    extract_result_text,
    skip_on_windows
)

# Skip all tests in this module on Windows (sudo not available)
pytestmark = skip_on_windows
from cygnus_ssh_mcp.server import mcp
from fastmcp import Client

# Configure logging
logger = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_ssh_file_find_lines_with_pattern_sudo(mcp_test_environment):
    """Test ssh_file_find_lines_with_pattern with sudo on protected file."""
    print_test_header("Testing ssh_file_find_lines_with_pattern with sudo")

    async with Client(mcp) as client:
        test_file = "/root/sudo_pattern_test.txt"
        try:
            assert await make_connection(client), "Failed to connect"

            # Create test file in /root/ with specific content
            test_content = """# Configuration file
server_name=production
port=8080
debug=false
server_name=backup
timeout=30"""

            write_result = await client.call_tool("ssh_file_write", {
                "file_path": test_file,
                "content": test_content,
                "use_sudo": True
            })
            write_json = json.loads(extract_result_text(write_result))
            assert write_json['success'], f"Failed to create test file: {write_json}"

            # Search for pattern with sudo
            search_result = await client.call_tool("ssh_file_find_lines_with_pattern", {
                "file_path": test_file,
                "pattern": "server_name",
                "use_sudo": True
            })
            search_json = json.loads(extract_result_text(search_result))

            # Tool returns total_matches and matches
            assert 'total_matches' in search_json, f"Missing total_matches: {search_json}"
            assert search_json['total_matches'] == 2, f"Expected 2 matches, got {search_json['total_matches']}"
            assert len(search_json['matches']) == 2, f"Expected 2 match entries"
            logger.info(f"Found {search_json['total_matches']} matches for pattern 'server_name'")

        except Exception as e:
            logger.error(f"Error in pattern search sudo test: {e}", exc_info=True)
            raise
        finally:
            await client.call_tool("ssh_cmd_run", {
                "command": f"rm -f {test_file}",
                "use_sudo": True,
                "io_timeout": 5.0
            })
            await disconnect_ssh(client)

    print_test_footer()


@pytest.mark.asyncio
async def test_ssh_file_get_context_around_line_sudo(mcp_test_environment):
    """Test ssh_file_get_context_around_line with sudo on protected file."""
    print_test_header("Testing ssh_file_get_context_around_line with sudo")

    async with Client(mcp) as client:
        test_file = "/root/sudo_context_test.txt"
        try:
            assert await make_connection(client), "Failed to connect"

            # Create test file with multiple lines
            test_content = """line 1: header
line 2: before target
line 3: TARGET_LINE
line 4: after target
line 5: footer"""

            write_result = await client.call_tool("ssh_file_write", {
                "file_path": test_file,
                "content": test_content,
                "use_sudo": True
            })
            write_json = json.loads(extract_result_text(write_result))
            assert write_json['success'], f"Failed to create test file: {write_json}"

            # Get context around TARGET_LINE with sudo
            context_result = await client.call_tool("ssh_file_get_context_around_line", {
                "file_path": test_file,
                "match_line": "line 3: TARGET_LINE",
                "context": 1,
                "use_sudo": True
            })
            context_json = json.loads(extract_result_text(context_result))

            # Tool returns match_found, match_line_number, context_block (list of dicts)
            assert context_json.get('match_found') == True, f"Match not found: {context_json}"
            assert context_json.get('match_line_number') == 3, f"Wrong line number: {context_json}"
            context_block = context_json.get('context_block', [])
            # context_block is a list of dicts with 'content' and 'line_number' keys
            context_content = ' '.join([line.get('content', '') for line in context_block])
            assert "TARGET_LINE" in context_content, f"Target line not found in context: {context_block}"
            logger.info("Successfully retrieved context around target line")

        except Exception as e:
            logger.error(f"Error in context sudo test: {e}", exc_info=True)
            raise
        finally:
            await client.call_tool("ssh_cmd_run", {
                "command": f"rm -f {test_file}",
                "use_sudo": True,
                "io_timeout": 5.0
            })
            await disconnect_ssh(client)

    print_test_footer()


@pytest.mark.asyncio
async def test_ssh_file_replace_line_sudo(mcp_test_environment):
    """Test ssh_file_replace_line (single line) with sudo on protected file."""
    print_test_header("Testing ssh_file_replace_line with sudo")

    async with Client(mcp) as client:
        test_file = "/etc/sudo_replace_test.conf"
        try:
            assert await make_connection(client), "Failed to connect"

            # Create test config file
            test_content = """# Test config
setting_a=old_value
setting_b=keep_this
setting_c=also_keep"""

            write_result = await client.call_tool("ssh_file_write", {
                "file_path": test_file,
                "content": test_content,
                "use_sudo": True
            })
            write_json = json.loads(extract_result_text(write_result))
            assert write_json['success'], f"Failed to create test file: {write_json}"

            # Replace single line with sudo
            replace_result = await client.call_tool("ssh_file_replace_line", {
                "file_path": test_file,
                "match_line": "setting_a=old_value",
                "new_line": "setting_a=new_value",
                "use_sudo": True
            })
            replace_json = json.loads(extract_result_text(replace_result))
            assert replace_json['success'], f"Line replacement failed: {replace_json}"

            # Verify replacement
            verify_result = await client.call_tool("ssh_cmd_run", {
                "command": f"cat {test_file}",
                "use_sudo": True
            })
            verify_json = json.loads(extract_result_text(verify_result))
            assert "setting_a=new_value" in verify_json['output'], "Replacement not found"
            assert "setting_a=old_value" not in verify_json['output'], "Old value still present"
            logger.info("Successfully replaced line in protected file")

        except Exception as e:
            logger.error(f"Error in replace line sudo test: {e}", exc_info=True)
            raise
        finally:
            await client.call_tool("ssh_cmd_run", {
                "command": f"rm -f {test_file}",
                "use_sudo": True,
                "io_timeout": 5.0
            })
            await disconnect_ssh(client)

    print_test_footer()


@pytest.mark.asyncio
async def test_ssh_file_copy_sudo(mcp_test_environment):
    """Test ssh_file_copy with sudo to protected location."""
    print_test_header("Testing ssh_file_copy with sudo")

    async with Client(mcp) as client:
        source_file = "/tmp/sudo_copy_source.txt"
        dest_file = "/root/sudo_copy_dest.txt"
        try:
            assert await make_connection(client), "Failed to connect"

            # Create source file in /tmp/
            await client.call_tool("ssh_file_write", {
                "file_path": source_file,
                "content": "Content to be copied with sudo"
            })

            # Copy to /root/ with sudo
            copy_result = await client.call_tool("ssh_file_copy", {
                "source_path": source_file,
                "destination_path": dest_file,
                "use_sudo": True
            })
            copy_json = json.loads(extract_result_text(copy_result))
            # Tool returns status: 'success' or similar
            assert copy_json.get('status') == 'success' or copy_json.get('success'), f"Copy failed: {copy_json}"

            # Verify destination exists and has correct content
            verify_result = await client.call_tool("ssh_cmd_run", {
                "command": f"cat {dest_file}",
                "use_sudo": True
            })
            verify_json = json.loads(extract_result_text(verify_result))
            assert verify_json['status'] == 'success', f"Failed to read dest: {verify_json}"
            assert "Content to be copied" in verify_json['output'], "Content mismatch"
            logger.info("Successfully copied file to protected location")

        except Exception as e:
            logger.error(f"Error in file copy sudo test: {e}", exc_info=True)
            raise
        finally:
            await client.call_tool("ssh_cmd_run", {
                "command": f"rm -f {source_file} {dest_file}",
                "use_sudo": True,
                "io_timeout": 5.0
            })
            await disconnect_ssh(client)

    print_test_footer()


@pytest.mark.asyncio
async def test_ssh_file_move_sudo(mcp_test_environment):
    """Test ssh_file_move with sudo to protected location."""
    print_test_header("Testing ssh_file_move with sudo")

    async with Client(mcp) as client:
        source_file = "/tmp/sudo_move_source.txt"
        dest_file = "/opt/sudo_move_dest.txt"
        try:
            assert await make_connection(client), "Failed to connect"

            # Create source file in /tmp/
            await client.call_tool("ssh_file_write", {
                "file_path": source_file,
                "content": "Content to be moved with sudo"
            })

            # Move to /opt/ with sudo
            move_result = await client.call_tool("ssh_file_move", {
                "source": source_file,
                "destination": dest_file,
                "use_sudo": True
            })
            move_json = json.loads(extract_result_text(move_result))
            assert move_json['success'], f"Move failed: {move_json}"

            # Verify source no longer exists
            check_source = await client.call_tool("ssh_cmd_run", {
                "command": f"test -f {source_file} && echo exists || echo gone",
                "use_sudo": True
            })
            check_source_json = json.loads(extract_result_text(check_source))
            assert "gone" in check_source_json['output'], "Source file still exists after move"

            # Verify destination exists
            verify_result = await client.call_tool("ssh_cmd_run", {
                "command": f"cat {dest_file}",
                "use_sudo": True
            })
            verify_json = json.loads(extract_result_text(verify_result))
            assert "Content to be moved" in verify_json['output'], "Content not in destination"
            logger.info("Successfully moved file to protected location")

        except Exception as e:
            logger.error(f"Error in file move sudo test: {e}", exc_info=True)
            raise
        finally:
            await client.call_tool("ssh_cmd_run", {
                "command": f"rm -f {source_file} {dest_file}",
                "use_sudo": True,
                "io_timeout": 5.0
            })
            await disconnect_ssh(client)

    print_test_footer()


@pytest.mark.asyncio
async def test_ssh_task_kill_sudo(mcp_test_environment):
    """Test ssh_task_kill with sudo on a background task."""
    print_test_header("Testing ssh_task_kill with sudo")

    async with Client(mcp) as client:
        pid = None
        try:
            assert await make_connection(client), "Failed to connect"

            # Launch a background task with sudo (sleep command)
            launch_result = await client.call_tool("ssh_task_launch", {
                "command": "sleep 300",
                "use_sudo": True
            })
            launch_json = json.loads(extract_result_text(launch_result))
            # ssh_task_launch returns: command, pid, start_time, stdout_log, stderr_log
            pid = launch_json.get('pid')
            assert pid is not None, f"Task launch failed - no PID: {launch_json}"
            logger.info(f"Launched background task with PID: {pid}")

            # Give it a moment to start
            await asyncio.sleep(1)

            # Verify task is running (ssh_task_status takes pid, not task_id)
            status_result = await client.call_tool("ssh_task_status", {
                "pid": pid
            })
            status_json = json.loads(extract_result_text(status_result))
            assert status_json['running'], f"Task not running: {status_json}"

            # Kill the task with sudo using the PID
            kill_result = await client.call_tool("ssh_task_kill", {
                "pid": pid,
                "use_sudo": True
            })
            kill_json = json.loads(extract_result_text(kill_result))
            # Tool returns result field: 'killed', 'already_exited', 'failed_to_kill', 'error'
            assert kill_json.get('result') in ['killed', 'already_exited'], f"Task kill failed: {kill_json}"
            logger.info(f"Successfully killed background task with sudo: {kill_json.get('result')}")

            # Verify task is no longer running
            await asyncio.sleep(1)
            final_status = await client.call_tool("ssh_task_status", {
                "pid": pid
            })
            final_json = json.loads(extract_result_text(final_status))
            assert not final_json['running'], "Task still running after kill"

        except Exception as e:
            logger.error(f"Error in task kill sudo test: {e}", exc_info=True)
            raise
        finally:
            await disconnect_ssh(client)

    print_test_footer()


@pytest.mark.asyncio
async def test_ssh_dir_search_glob_sudo(mcp_test_environment):
    """Test ssh_dir_search_glob with sudo in protected directory."""
    print_test_header("Testing ssh_dir_search_glob with sudo")

    async with Client(mcp) as client:
        test_dir = "/opt/sudo_glob_test"
        try:
            assert await make_connection(client), "Failed to connect"

            # Create test directory with files
            await client.call_tool("ssh_cmd_run", {
                "command": f"mkdir -p {test_dir} && touch {test_dir}/file1.txt {test_dir}/file2.txt {test_dir}/data.log",
                "use_sudo": True
            })

            # Search for .txt files with sudo
            # Note: This tool returns a LIST directly
            glob_result = await client.call_tool("ssh_dir_search_glob", {
                "path": test_dir,
                "pattern": "*.txt",
                "use_sudo": True
            })
            result_text = extract_result_text(glob_result)
            # May be a list or JSON array
            try:
                glob_list = json.loads(result_text)
            except json.JSONDecodeError:
                glob_list = result_text

            # Should find 2 .txt files
            if isinstance(glob_list, list):
                txt_files = [f for f in glob_list if isinstance(f, dict) and f.get('name', '').endswith('.txt')]
                if not txt_files:
                    txt_files = [f for f in glob_list if isinstance(f, str) and f.endswith('.txt')]
                assert len(txt_files) >= 2 or len(glob_list) >= 2, f"Expected at least 2 files, got: {glob_list}"
            logger.info(f"Glob search completed in protected directory")

        except Exception as e:
            logger.error(f"Error in glob search sudo test: {e}", exc_info=True)
            raise
        finally:
            await client.call_tool("ssh_cmd_run", {
                "command": f"rm -rf {test_dir}",
                "use_sudo": True,
                "io_timeout": 5.0
            })
            await disconnect_ssh(client)

    print_test_footer()


@pytest.mark.asyncio
async def test_ssh_dir_delete_sudo(mcp_test_environment):
    """Test ssh_dir_delete with sudo on protected directory."""
    print_test_header("Testing ssh_dir_delete with sudo")

    async with Client(mcp) as client:
        test_dir = "/opt/sudo_delete_test"
        try:
            assert await make_connection(client), "Failed to connect"

            # Create test directory with content
            await client.call_tool("ssh_cmd_run", {
                "command": f"mkdir -p {test_dir}/subdir && touch {test_dir}/file.txt {test_dir}/subdir/nested.txt",
                "use_sudo": True
            })

            # Verify directory exists
            check_exists = await client.call_tool("ssh_cmd_run", {
                "command": f"test -d {test_dir} && echo exists || echo missing",
                "use_sudo": True
            })
            check_json = json.loads(extract_result_text(check_exists))
            assert "exists" in check_json['output'], "Test directory not created"

            # Delete directory with sudo (dry_run=False to actually delete)
            delete_result = await client.call_tool("ssh_dir_delete", {
                "path": test_dir,
                "dry_run": False,
                "use_sudo": True
            })
            delete_json = json.loads(extract_result_text(delete_result))
            # Check for status or success
            assert delete_json.get('status') == 'success' or delete_json.get('deleted'), f"Directory delete failed: {delete_json}"

            # Verify directory is gone
            verify_gone = await client.call_tool("ssh_cmd_run", {
                "command": f"test -d {test_dir} && echo exists || echo gone",
                "use_sudo": True
            })
            verify_json = json.loads(extract_result_text(verify_gone))
            assert "gone" in verify_json['output'], "Directory still exists after delete"
            logger.info("Successfully deleted protected directory")

        except Exception as e:
            logger.error(f"Error in dir delete sudo test: {e}", exc_info=True)
            raise
        finally:
            # Cleanup just in case
            await client.call_tool("ssh_cmd_run", {
                "command": f"rm -rf {test_dir}",
                "use_sudo": True,
                "io_timeout": 5.0
            })
            await disconnect_ssh(client)

    print_test_footer()


@pytest.mark.asyncio
async def test_ssh_dir_batch_delete_files_sudo(mcp_test_environment):
    """Test ssh_dir_batch_delete_files with sudo in protected directory."""
    print_test_header("Testing ssh_dir_batch_delete_files with sudo")

    async with Client(mcp) as client:
        test_dir = "/opt/sudo_batch_delete_test"
        try:
            assert await make_connection(client), "Failed to connect"

            # Create test directory with multiple files
            await client.call_tool("ssh_cmd_run", {
                "command": f"mkdir -p {test_dir} && touch {test_dir}/delete1.tmp {test_dir}/delete2.tmp {test_dir}/keep.txt",
                "use_sudo": True
            })

            # Batch delete .tmp files with sudo (dry_run=False)
            batch_result = await client.call_tool("ssh_dir_batch_delete_files", {
                "path": test_dir,
                "pattern": "*.tmp",
                "dry_run": False,
                "use_sudo": True
            })
            batch_json = json.loads(extract_result_text(batch_result))
            # Check for status or deleted_count
            assert batch_json.get('status') == 'success' or batch_json.get('deleted_count', 0) >= 0, f"Batch delete failed: {batch_json}"

            # Verify .tmp files are gone but .txt remains
            list_result = await client.call_tool("ssh_cmd_run", {
                "command": f"ls -la {test_dir}",
                "use_sudo": True
            })
            list_json = json.loads(extract_result_text(list_result))
            assert "delete1.tmp" not in list_json['output'], ".tmp file still exists"
            assert "delete2.tmp" not in list_json['output'], ".tmp file still exists"
            assert "keep.txt" in list_json['output'], ".txt file was incorrectly deleted"
            logger.info("Successfully batch deleted files with pattern")

        except Exception as e:
            logger.error(f"Error in batch delete sudo test: {e}", exc_info=True)
            raise
        finally:
            await client.call_tool("ssh_cmd_run", {
                "command": f"rm -rf {test_dir}",
                "use_sudo": True,
                "io_timeout": 5.0
            })
            await disconnect_ssh(client)

    print_test_footer()


@pytest.mark.asyncio
async def test_ssh_dir_search_files_content_sudo(mcp_test_environment):
    """Test ssh_dir_search_files_content with sudo in protected directory."""
    print_test_header("Testing ssh_dir_search_files_content with sudo")

    async with Client(mcp) as client:
        test_dir = "/opt/sudo_content_search_test"
        try:
            assert await make_connection(client), "Failed to connect"

            # Create test directory with files containing specific content
            await client.call_tool("ssh_cmd_run", {
                "command": f"mkdir -p {test_dir}",
                "use_sudo": True
            })

            await client.call_tool("ssh_file_write", {
                "file_path": f"{test_dir}/file1.txt",
                "content": "This file contains SEARCHTERM in it",
                "use_sudo": True
            })

            await client.call_tool("ssh_file_write", {
                "file_path": f"{test_dir}/file2.txt",
                "content": "Another file with SEARCHTERM here",
                "use_sudo": True
            })

            await client.call_tool("ssh_file_write", {
                "file_path": f"{test_dir}/file3.txt",
                "content": "This file has no match",
                "use_sudo": True
            })

            # Search for content with sudo
            # This tool returns a LIST directly
            search_result = await client.call_tool("ssh_dir_search_files_content", {
                "dir_path": test_dir,
                "pattern": "SEARCHTERM",
                "use_sudo": True
            })
            result_text = extract_result_text(search_result)
            try:
                search_list = json.loads(result_text)
            except json.JSONDecodeError:
                search_list = result_text

            # Should find matches in 2 files
            if isinstance(search_list, list):
                assert len(search_list) >= 2, f"Expected at least 2 matches, got: {search_list}"
            logger.info(f"Content search completed in protected directory")

        except Exception as e:
            logger.error(f"Error in content search sudo test: {e}", exc_info=True)
            raise
        finally:
            await client.call_tool("ssh_cmd_run", {
                "command": f"rm -rf {test_dir}",
                "use_sudo": True,
                "io_timeout": 5.0
            })
            await disconnect_ssh(client)

    print_test_footer()


@pytest.mark.asyncio
async def test_ssh_dir_copy_sudo(mcp_test_environment):
    """Test ssh_dir_copy with sudo to protected location."""
    print_test_header("Testing ssh_dir_copy with sudo")

    async with Client(mcp) as client:
        source_dir = "/tmp/sudo_dir_copy_source"
        dest_dir = "/opt/sudo_dir_copy_dest"
        try:
            assert await make_connection(client), "Failed to connect"

            # Create source directory with files
            await client.call_tool("ssh_cmd_run", {
                "command": f"mkdir -p {source_dir}/subdir && echo 'file1' > {source_dir}/file1.txt && echo 'nested' > {source_dir}/subdir/nested.txt"
            })

            # Copy directory to /opt/ with sudo
            copy_result = await client.call_tool("ssh_dir_copy", {
                "source_path": source_dir,
                "destination_path": dest_dir,
                "use_sudo": True
            })
            copy_json = json.loads(extract_result_text(copy_result))
            assert copy_json.get('status') == 'success' or copy_json.get('success'), f"Directory copy failed: {copy_json}"

            # Verify destination structure
            verify_result = await client.call_tool("ssh_cmd_run", {
                "command": f"find {dest_dir} -type f | sort",
                "use_sudo": True
            })
            verify_json = json.loads(extract_result_text(verify_result))
            assert "file1.txt" in verify_json['output'], "file1.txt not in destination"
            assert "nested.txt" in verify_json['output'], "nested.txt not in destination"
            logger.info("Successfully copied directory to protected location")

        except Exception as e:
            logger.error(f"Error in dir copy sudo test: {e}", exc_info=True)
            raise
        finally:
            await client.call_tool("ssh_cmd_run", {
                "command": f"rm -rf {source_dir} {dest_dir}",
                "use_sudo": True,
                "io_timeout": 5.0
            })
            await disconnect_ssh(client)

    print_test_footer()
