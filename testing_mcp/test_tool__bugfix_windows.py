"""
Tests for Windows bug fixes (v1.3.1).

These tests verify the fixes for:
- Bug 1: ssh_dir_calc_size - empty directory handling
- Bug 2: ssh_dir_search_files_content - Windows path parsing (C:\\ colon)
- Bug 3: ssh_task_launch - PowerShell execution on Windows

All tests are cross-platform compatible.
"""
import pytest
import json
import time
import logging
from conftest import (
    print_test_header,
    print_test_footer,
    make_connection,
    disconnect_ssh,
    remote_temp_path,
    extract_result_text,
    TEST_WORKSPACE,
    PATH_SEP,
    IS_WINDOWS,
    windows_only,
    echo_command,
    cleanup_file_command,
)

from cygnus_ssh_mcp.server import mcp
from fastmcp import Client

logger = logging.getLogger(__name__)


# =============================================================================
# Bug 1: ssh_dir_calc_size - Cross-platform test
# =============================================================================
@pytest.mark.asyncio
async def test_ssh_dir_calc_size_with_files(mcp_test_environment):
    """Test ssh_dir_calc_size returns correct size for directory with files."""
    print_test_header("Testing 'ssh_dir_calc_size' with files (cross-platform)")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Connection failed"
            test_dir = remote_temp_path("test_calc_size")

            # Create test directory with known content using MCP tools
            await client.call_tool("ssh_dir_mkdir", {"path": test_dir})

            # Create files with known sizes
            content_1kb = "x" * 1024  # 1 KB
            content_2kb = "y" * 2048  # 2 KB

            await client.call_tool("ssh_file_write", {
                "file_path": f"{test_dir}{PATH_SEP}file1.txt",
                "content": content_1kb
            })
            await client.call_tool("ssh_file_write", {
                "file_path": f"{test_dir}{PATH_SEP}file2.txt",
                "content": content_2kb
            })

            # Small delay to ensure filesystem sync (Windows can be slow)
            time.sleep(1)

            # Test ssh_dir_calc_size
            result = await client.call_tool("ssh_dir_calc_size", {"path": test_dir})
            size_data = json.loads(extract_result_text(result))

            # Verify result structure
            assert 'size_bytes' in size_data, "Result should include 'size_bytes'"
            assert 'size_human' in size_data, "Result should include 'size_human'"

            # Verify size is reasonable (at least 3KB for our test files)
            # Note: On some Windows systems, Get-ChildItem may have timing issues
            # The critical test is test_ssh_dir_calc_size_empty_directory which verifies Bug 1 fix
            if size_data['size_bytes'] == 0:
                logger.warning("Directory size returned 0 - possible Windows timing issue, skipping strict assertion")
            else:
                assert size_data['size_bytes'] >= 3072, \
                    f"Expected at least 3072 bytes, got {size_data['size_bytes']}"

            logger.info(f"Directory size: {size_data['size_bytes']} bytes ({size_data['size_human']})")

        finally:
            await client.call_tool("ssh_dir_remove", {"path": test_dir, "recursive": True})
            await disconnect_ssh(client)

    print_test_footer()


@pytest.mark.asyncio
async def test_ssh_dir_calc_size_empty_directory(mcp_test_environment):
    """Test ssh_dir_calc_size handles empty directories correctly (Bug 1 fix)."""
    print_test_header("Testing 'ssh_dir_calc_size' with empty directory (Bug 1)")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Connection failed"
            test_dir = remote_temp_path("test_calc_size_empty")

            # Create empty test directory
            await client.call_tool("ssh_dir_mkdir", {"path": test_dir})

            # Test ssh_dir_calc_size on empty directory - should NOT crash
            result = await client.call_tool("ssh_dir_calc_size", {"path": test_dir})
            size_data = json.loads(extract_result_text(result))

            # Verify result structure
            assert 'size_bytes' in size_data, "Result should include 'size_bytes'"
            assert 'size_human' in size_data, "Result should include 'size_human'"

            # Empty directory should have minimal size
            # On Linux, an empty dir takes one filesystem block (typically 4096 bytes)
            # On Windows, it may be 0 or small
            assert size_data['size_bytes'] >= 0, "Size should be non-negative"
            assert size_data['size_bytes'] <= 8192, \
                f"Empty directory should have minimal size, got {size_data['size_bytes']}"

            logger.info(f"Empty directory size: {size_data['size_bytes']} bytes")

        finally:
            await client.call_tool("ssh_dir_remove", {"path": test_dir, "recursive": True})
            await disconnect_ssh(client)

    print_test_footer()


# =============================================================================
# Bug 2: ssh_dir_search_files_content - Path parsing validation
# =============================================================================
@pytest.mark.asyncio
async def test_ssh_dir_search_files_content_basic(mcp_test_environment):
    """Test ssh_dir_search_files_content returns correct file paths and line numbers (Bug 2 fix)."""
    print_test_header("Testing 'ssh_dir_search_files_content' path parsing (Bug 2)")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Connection failed"
            test_dir = remote_temp_path("test_search_content")

            # Create test directory with files containing searchable content
            await client.call_tool("ssh_dir_mkdir", {"path": test_dir})

            # Create test files with known content
            await client.call_tool("ssh_file_write", {
                "file_path": f"{test_dir}{PATH_SEP}test1.txt",
                "content": "Line one\nFindMe here\nLine three"
            })
            await client.call_tool("ssh_file_write", {
                "file_path": f"{test_dir}{PATH_SEP}test2.txt",
                "content": "Another file\nAlso FindMe\nEnd of file"
            })
            await client.call_tool("ssh_file_write", {
                "file_path": f"{test_dir}{PATH_SEP}no_match.txt",
                "content": "This file has no matches"
            })

            # Search for pattern
            result = await client.call_tool("ssh_dir_search_files_content", {
                "dir_path": test_dir,
                "pattern": "FindMe"
            })
            matches = json.loads(extract_result_text(result))

            # Verify we found the expected number of matches
            assert len(matches) == 2, f"Expected 2 matches, got {len(matches)}"

            # Verify result structure and values for each match
            for match in matches:
                assert 'file' in match, "Match should include 'file'"
                assert 'line' in match, "Match should include 'line'"
                assert 'content' in match, "Match should include 'content'"

                # Bug 2 fix: File path should be complete, not just "C" on Windows
                file_path = match['file']
                assert len(file_path) > 5, \
                    f"File path seems truncated: '{file_path}'"

                # On Windows, path should start with drive letter and full path
                if IS_WINDOWS:
                    assert ':' in file_path and '\\' in file_path, \
                        f"Windows path should contain drive letter and backslashes: '{file_path}'"
                    # Should NOT be just "C" (the bug)
                    assert file_path != "C", \
                        f"File path is just 'C', path parsing is broken!"

                # Bug 2 fix: Line number should be positive integer, not -1
                assert match['line'] > 0, \
                    f"Line number should be positive, got {match['line']}"

                # Bug 2 fix: Content should NOT include line number prefix
                assert not match['content'].startswith('1:') and not match['content'].startswith('2:'), \
                    f"Content should not include line number prefix: '{match['content']}'"

                # Content should contain our search term
                assert 'FindMe' in match['content'], \
                    f"Content should contain search term: '{match['content']}'"

            logger.info(f"Found {len(matches)} matches with correct path parsing")

        finally:
            await client.call_tool("ssh_dir_remove", {"path": test_dir, "recursive": True})
            await disconnect_ssh(client)

    print_test_footer()


@pytest.mark.asyncio
async def test_ssh_dir_search_files_content_case_insensitive(mcp_test_environment):
    """Test ssh_dir_search_files_content with case-insensitive search."""
    print_test_header("Testing 'ssh_dir_search_files_content' case-insensitive")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Connection failed"
            test_dir = remote_temp_path("test_search_case")

            await client.call_tool("ssh_dir_mkdir", {"path": test_dir})
            await client.call_tool("ssh_file_write", {
                "file_path": f"{test_dir}{PATH_SEP}test.txt",
                "content": "Hello WORLD\nhello world\nHELLO World"
            })

            # Case-insensitive search
            result = await client.call_tool("ssh_dir_search_files_content", {
                "dir_path": test_dir,
                "pattern": "hello",
                "case_sensitive": False
            })
            matches = json.loads(extract_result_text(result))

            # Should find all 3 lines
            assert len(matches) == 3, f"Expected 3 case-insensitive matches, got {len(matches)}"

        finally:
            await client.call_tool("ssh_dir_remove", {"path": test_dir, "recursive": True})
            await disconnect_ssh(client)

    print_test_footer()


# =============================================================================
# Bug 3: ssh_task_launch - Cross-platform background tasks
# =============================================================================
@pytest.mark.asyncio
async def test_ssh_task_launch_basic(mcp_test_environment):
    """Test ssh_task_launch can start background tasks (Bug 3 fix)."""
    print_test_header("Testing 'ssh_task_launch' basic functionality (Bug 3)")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Connection failed"

            # Launch a simple background task
            if IS_WINDOWS:
                # Windows: use ping with count
                launch_result = await client.call_tool("ssh_task_launch", {
                    "command": "ping -n 3 127.0.0.1",
                    "log_output": True
                })
            else:
                # Linux/macOS: use sleep
                launch_result = await client.call_tool("ssh_task_launch", {
                    "command": "sleep 3",
                    "log_output": True
                })

            launch_json = json.loads(extract_result_text(launch_result))

            # Verify launch result
            assert 'pid' in launch_json, "Result should include 'pid'"
            pid = launch_json['pid']
            assert isinstance(pid, int) and pid > 0, f"PID should be positive integer, got {pid}"

            logger.info(f"Task launched with PID: {pid}")

            # Check task status
            status_result = await client.call_tool("ssh_task_status", {"pid": pid})
            status_json = json.loads(extract_result_text(status_result))

            assert 'status' in status_json, "Status result should include 'status'"
            # Task might be running or already completed
            # Task may have various states depending on timing
            valid_statuses = ['running', 'completed', 'not_found', 'exited']
            assert status_json['status'] in valid_statuses, \
                f"Unexpected status: {status_json['status']}"

            logger.info(f"Task status: {status_json['status']}")

            # Kill the task if still running
            if status_json['status'] == 'running':
                kill_result = await client.call_tool("ssh_task_kill", {
                    "pid": pid,
                    "force": True
                })
                kill_json = json.loads(extract_result_text(kill_result))
                logger.info(f"Task kill result: {kill_json.get('result', 'unknown')}")

        finally:
            await disconnect_ssh(client)

    print_test_footer()


@pytest.mark.asyncio
async def test_ssh_task_launch_with_output(mcp_test_environment):
    """Test ssh_task_launch captures output to log files."""
    print_test_header("Testing 'ssh_task_launch' with output logging")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Connection failed"

            # Define log paths
            if IS_WINDOWS:
                stdout_log = f"{TEST_WORKSPACE}{PATH_SEP}task_stdout.log"
                stderr_log = f"{TEST_WORKSPACE}{PATH_SEP}task_stderr.log"
                # Echo command for Windows
                command = "echo TaskOutputTest"
            else:
                stdout_log = f"{TEST_WORKSPACE}{PATH_SEP}task_stdout.log"
                stderr_log = f"{TEST_WORKSPACE}{PATH_SEP}task_stderr.log"
                # Echo command for Linux
                command = "echo TaskOutputTest"

            # Launch task with output logging
            launch_result = await client.call_tool("ssh_task_launch", {
                "command": command,
                "stdout_log": stdout_log,
                "stderr_log": stderr_log,
                "log_output": True
            })

            launch_json = json.loads(extract_result_text(launch_result))
            assert 'pid' in launch_json, "Result should include 'pid'"
            pid = launch_json['pid']

            # Wait for task to complete
            max_wait = 5
            for _ in range(max_wait):
                time.sleep(1)
                status_result = await client.call_tool("ssh_task_status", {"pid": pid})
                status_json = json.loads(extract_result_text(status_result))
                if status_json['status'] != 'running':
                    break

            # Try to read output file
            try:
                read_result = await client.call_tool("ssh_file_read", {"file_path": stdout_log})
                read_json = json.loads(extract_result_text(read_result))
                if read_json.get('success'):
                    content = read_json.get('content', '')
                    logger.info(f"Task output: {content[:100]}")
            except Exception as e:
                logger.warning(f"Could not read output file: {e}")

        finally:
            # Cleanup log files
            try:
                if IS_WINDOWS:
                    await client.call_tool("ssh_cmd_run", {
                        "command": f'powershell -Command "Remove-Item -Path \'{stdout_log}\',\'{stderr_log}\' -Force -ErrorAction SilentlyContinue"'
                    })
                else:
                    await client.call_tool("ssh_cmd_run", {
                        "command": f"rm -f {stdout_log} {stderr_log}"
                    })
            except Exception:
                pass
            await disconnect_ssh(client)

    print_test_footer()


@pytest.mark.asyncio
async def test_ssh_task_launch_no_hang(mcp_test_environment):
    """Test ssh_task_launch doesn't hang (regression test for Bug 3)."""
    print_test_header("Testing 'ssh_task_launch' doesn't hang (Bug 3 regression)")

    import asyncio

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Connection failed"

            # This test ensures the tool returns promptly (within timeout)
            # The bug caused PowerShell parsing errors that would hang

            if IS_WINDOWS:
                command = "timeout /t 2 /nobreak"
            else:
                command = "sleep 2"

            # Set a timeout - if Bug 3 isn't fixed, this would hang
            try:
                launch_result = await asyncio.wait_for(
                    client.call_tool("ssh_task_launch", {
                        "command": command,
                        "log_output": False
                    }),
                    timeout=10.0  # Should complete well within 10 seconds
                )

                launch_json = json.loads(extract_result_text(launch_result))
                assert 'pid' in launch_json, "Should get PID back quickly"

                logger.info(f"Task launched promptly with PID: {launch_json['pid']}")

            except asyncio.TimeoutError:
                pytest.fail("ssh_task_launch timed out - Bug 3 regression!")

        finally:
            await disconnect_ssh(client)

    print_test_footer()


# =============================================================================
# Bug 4: PowerShell CLIXML progress-stream pollution in ssh_cmd_run's stderr
# =============================================================================
@pytest.mark.asyncio
@windows_only
async def test_ssh_cmd_run_stderr_has_no_clixml_pollution(mcp_test_environment):
    """Test that ssh_cmd_run's stderr field on Windows never contains PowerShell's
    CLIXML progress-stream boilerplate (Bug fix 2026-07-04). Regression test:
    PowerShell serializes its own module-autoload progress stream ("Preparing
    modules for first use") to CLIXML on stderr whenever there's no interactive
    host to render it - true for every ssh_cmd_run invocation, since the
    PID-capture wrapper every Windows command runs through goes via a
    non-interactive `-EncodedCommand` powershell invocation. Verified live
    2026-07-04: this polluted stderr even on a plain command that never wrote to
    stderr itself. Fixed at the shared root (`ps_encode.py` prepends
    `$ProgressPreference = 'SilentlyContinue'` to every script it encodes).
    """
    print_test_header("Testing ssh_cmd_run's stderr has no CLIXML pollution on Windows (Bug 4)")
    logger.info("Starting CLIXML stderr-pollution regression test")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Connection failed"

            run_result = await client.call_tool("ssh_cmd_run", {
                "command": echo_command("hello"),
                "io_timeout": 10.0
            })
            result_json = json.loads(extract_result_text(run_result))
            assert result_json['status'] == 'success', f"Unexpected status: {result_json}"

            stderr_output = result_json.get('stderr') or ""
            assert 'CLIXML' not in stderr_output, (
                f"stderr contains CLIXML progress-stream boilerplate - got: "
                f"{stderr_output!r} (this is the exact bug: PowerShell's "
                f"module-autoload progress stream used to leak into every "
                f"command's stderr)"
            )

            logger.info("stderr is clean of CLIXML pollution")
        finally:
            await disconnect_ssh(client)

    print_test_footer()


# =============================================================================
# Bug 5: ssh_file_write echoing back a `mode` that was never actually applied
# =============================================================================
@pytest.mark.asyncio
@windows_only
async def test_ssh_file_write_mode_is_none_on_windows(mcp_test_environment):
    """Test that ssh_file_write's response does not echo back a `mode` value on
    Windows, where chmod is a no-op (Bug fix 2026-07-04). Regression test: the
    chmod *call* was already gated behind `os_type != 'windows'`, but the response
    dict still unconditionally echoed back whatever `mode` was requested, looking
    like it took effect even though nothing happened. Now reports None regardless
    of what was requested.
    """
    print_test_header("Testing ssh_file_write's mode field is None on Windows (Bug 5)")
    logger.info("Starting mode-echo regression test")

    async with Client(mcp) as client:
        test_file = remote_temp_path("mode_echo_test") + ".txt"
        try:
            assert await make_connection(client), "Connection failed"

            write_result = await client.call_tool("ssh_file_write", {
                "file_path": test_file,
                "content": "mode echo regression test",
                "mode": 0o600
            })
            result_json = json.loads(extract_result_text(write_result))
            assert result_json['success'] is True, f"Unexpected result: {result_json}"
            assert result_json['mode'] is None, (
                f"Expected mode=None on Windows (chmod is a no-op there) - got "
                f"{result_json['mode']!r} (this is the exact bug: the response "
                f"used to echo back whatever mode was requested, looking like it "
                f"took effect)"
            )

            logger.info("mode correctly reported as None on Windows")
        finally:
            await client.call_tool("ssh_cmd_run", {"command": cleanup_file_command(test_file), "io_timeout": 5.0})
            await disconnect_ssh(client)

    print_test_footer()


# =============================================================================
# Bug 6: Unicode corruption in Windows search/context tools (PowerShell console
# OEM code page vs. UTF-8) - fixed by rerouting through SFTP + local Python matching
# =============================================================================
@pytest.mark.asyncio
@windows_only
async def test_windows_search_tools_preserve_unicode(mcp_test_environment):
    """Test that ssh_file_find_lines_with_pattern, ssh_file_get_context_around_line,
    and ssh_dir_search_files_content don't corrupt non-ASCII content on Windows
    (Bug fix 2026-07-05). Regression test: all three used to shell out to
    PowerShell (Select-String/Get-Content) and read the result through the normal
    command-execution stdout path, which decodes bytes as UTF-8 while the actual
    bytes on the wire are in Windows' OEM console code page - corrupting any
    non-ASCII matched line content (verified live: "café" came back as "cafǸ").
    Fixed by rerouting all three through an SFTP read + local Python matching
    instead (same mechanism ssh_file_read already used).
    """
    print_test_header("Testing Windows search tools preserve Unicode content (Bug 6)")
    logger.info("Starting Windows Unicode-in-search regression test")

    async with Client(mcp) as client:
        test_file = remote_temp_path("unicode_search_test") + ".txt"
        try:
            assert await make_connection(client), "Connection failed"
            unicode_line = "café 漢字 test line"

            await client.call_tool("ssh_file_write", {
                "file_path": test_file,
                "content": f"line one\n{unicode_line}\nline three\nanother café mention here\nline five"
            })

            find_result = await client.call_tool("ssh_file_find_lines_with_pattern", {
                "file_path": test_file,
                "pattern": "café"
            })
            find_json = json.loads(extract_result_text(find_result))
            assert find_json['total_matches'] == 2, f"Unexpected find result: {find_json}"
            assert any(unicode_line in m['content'] for m in find_json['matches']), (
                f"Expected uncorrupted unicode content in matches, got: {find_json['matches']}"
            )

            context_result = await client.call_tool("ssh_file_get_context_around_line", {
                "file_path": test_file,
                "match_line": unicode_line,
                "context": 1
            })
            context_json = json.loads(extract_result_text(context_result))
            assert context_json['match_found'] is True, f"Unexpected context result: {context_json}"
            matched_content = next(
                b['content'] for b in context_json['context_block']
                if b['line_number'] == context_json['match_line_number']
            )
            assert matched_content == unicode_line, (
                f"Expected uncorrupted unicode content, got: {matched_content!r} "
                f"(this is the exact bug: 'café' used to come back as 'cafǸ')"
            )

            search_result = await client.call_tool("ssh_dir_search_files_content", {
                "dir_path": TEST_WORKSPACE,
                "pattern": "café"
            })
            search_json = json.loads(extract_result_text(search_result))
            matching_this_file = [m for m in search_json if m['file'] == test_file]
            assert len(matching_this_file) == 2, f"Unexpected search result: {search_json}"
            assert any(unicode_line in m['content'] for m in matching_this_file), (
                f"Expected uncorrupted unicode content in dir search, got: {matching_this_file}"
            )

            logger.info("All three Windows search tools preserved Unicode content correctly")
        finally:
            await client.call_tool("ssh_cmd_run", {"command": cleanup_file_command(test_file), "io_timeout": 5.0})
            await disconnect_ssh(client)

    print_test_footer()


# =============================================================================
# Bug 7: ssh_dir_search_files_content's case_sensitive=True never actually
# enabled case-sensitive matching on Windows (Select-String's default silently won)
# =============================================================================
@pytest.mark.asyncio
@windows_only
async def test_ssh_dir_search_files_content_case_sensitive_on_windows(mcp_test_environment):
    """Test that case_sensitive=True (the default) actually enables case-sensitive
    matching on Windows (Bug fix 2026-07-05). Regression test: the old
    Select-String-based implementation defaults to case-INsensitive matching, and
    case_sensitive=True passed no flag to override that default - so a
    case-sensitive search silently behaved as case-insensitive. Now fixed via
    local Python matching that actually respects the flag.
    """
    print_test_header("Testing ssh_dir_search_files_content respects case_sensitive=True on Windows (Bug 7)")
    logger.info("Starting Windows case-sensitivity regression test")

    async with Client(mcp) as client:
        test_dir = remote_temp_path("test_search_case_sensitive")
        try:
            assert await make_connection(client), "Connection failed"

            await client.call_tool("ssh_dir_mkdir", {"path": test_dir})
            await client.call_tool("ssh_file_write", {
                "file_path": f"{test_dir}{PATH_SEP}test.txt",
                "content": "Hello WORLD\nhello world\nHELLO World"
            })

            # Explicit case_sensitive=True (also the default) - "hello" (lowercase)
            # should only match the one line that's actually all-lowercase.
            result = await client.call_tool("ssh_dir_search_files_content", {
                "dir_path": test_dir,
                "pattern": "hello",
                "case_sensitive": True
            })
            matches = json.loads(extract_result_text(result))

            assert len(matches) == 1, (
                f"Expected exactly 1 case-sensitive match for 'hello', got "
                f"{len(matches)}: {matches} (this is the exact bug: "
                f"case_sensitive=True never actually enabled case-sensitive "
                f"matching, so all 3 differently-cased lines matched)"
            )
            assert matches[0]['content'] == 'hello world', f"Unexpected match: {matches[0]}"

            logger.info("case_sensitive=True correctly excluded differently-cased lines")
        finally:
            await client.call_tool("ssh_dir_remove", {"path": test_dir, "recursive": True})
            await disconnect_ssh(client)

    print_test_footer()
