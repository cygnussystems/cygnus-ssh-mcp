import pytest
import json
import os
from conftest import (
    print_test_header,
    print_test_footer,
    make_connection,
    disconnect_ssh,
    extract_result_text,
    remote_temp_path,
    cleanup_file_command,
    read_file_command,
    linux_only,
    PATH_SEP
)
from cygnus_ssh_mcp.server import mcp
from fastmcp import Client


@pytest.mark.asyncio
@linux_only
async def test_ssh_file_modify_sudo_and_force(mcp_test_environment):
    """Test file modification with sudo=True and force=True on a root-owned file (Linux only - uses chown/sudo)."""
    print_test_header("Testing file modification with sudo and force")

    test_file = remote_temp_path("test_mcp_sudo_force_file") + ".txt"
    original_content = "This is a root-owned file."
    new_line_content = "This line was added with sudo and force."

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            # 1. Create the file as the current user
            await client.call_tool("ssh_cmd_run", {
                "command": f"echo '{original_content}' > {test_file}",
                "io_timeout": 5.0
            })

            # 2. Change ownership to root
            chown_result = await client.call_tool("ssh_cmd_run", {
                "command": f"chown root:root {test_file}",
                "io_timeout": 5.0,
                "use_sudo": True
            })
            chown_json = json.loads(extract_result_text(chown_result))
            assert chown_json['status'] == 'success', f"sudo chown failed: {chown_json.get('error', '')}"

            # 3. Attempt to replace line with sudo=True and force=True
            replace_result = await client.call_tool("ssh_file_replace_line_multi", {
                "file_path": test_file,
                "match_line": original_content,
                "new_lines": [new_line_content],
                "use_sudo": True,
                "force": True
            })
            replace_json = json.loads(extract_result_text(replace_result))
            assert replace_json['success'] == True, f"ssh_file_replace_line_multi with sudo/force failed: {replace_json.get('error', '')}"

            # 4. Verify content (read as root to be sure)
            cat_result = await client.call_tool("ssh_cmd_run", {
                "command": f"cat {test_file}",
                "io_timeout": 5.0,
                "use_sudo": True
            })
            cat_json = json.loads(extract_result_text(cat_result))
            assert cat_json['status'] == 'success'
            output = cat_json['output']
            assert new_line_content in output, "New line not found after sudo/force replace."
            assert original_content not in output, "Original content still present after sudo/force replace."

            # 5. Verify permissions and ownership were restored
            stat_perm_result = await client.call_tool("ssh_cmd_run", {
                "command": f"stat -c '%U:%G %a' {test_file}",
                "io_timeout": 5.0,
                "use_sudo": False
            })
            stat_perm_json = json.loads(extract_result_text(stat_perm_result))
            assert stat_perm_json['status'] == 'success'
            perms_owner_group = stat_perm_json['output'].strip()
            assert perms_owner_group.startswith("root:root"), f"File ownership should be root:root, but was '{perms_owner_group}'"

        finally:
            await client.call_tool("ssh_cmd_run", {
                "command": f"rm -f {test_file}",
                "io_timeout": 5.0,
                "use_sudo": True
            })
            await disconnect_ssh(client)
    print_test_footer()


@pytest.mark.asyncio
async def test_ssh_file_find_lines_with_pattern_variations(mcp_test_environment):
    """Test ssh_file_find_lines_with_pattern with regex, no match, and empty file."""
    print_test_header("Testing 'ssh_file_find_lines_with_pattern' variations")

    test_file = remote_temp_path("test_find_pattern_vars") + ".txt"
    empty_file = remote_temp_path("test_find_pattern_vars_empty") + ".txt"

    file_content = """Line 1: apple
Line 2: banana
Line 3: Apple Pie
Line 4: orange123"""

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            # Create test files using MCP tools (cross-platform)
            await client.call_tool("ssh_file_write", {"file_path": test_file, "content": file_content})
            await client.call_tool("ssh_file_write", {"file_path": empty_file, "content": ""})

            # 1. Test with regex
            find_regex_result = await client.call_tool("ssh_file_find_lines_with_pattern", {
                "file_path": test_file, "pattern": "^Line [0-9]: [aA]pple.*", "regex": True
            })
            regex_json = json.loads(extract_result_text(find_regex_result))
            assert regex_json['total_matches'] == 2, f"Regex search failed to find correct matches: {regex_json}"
            assert "Line 1: apple" in regex_json['matches'][0]['content']
            assert "Line 3: Apple Pie" in regex_json['matches'][1]['content']

            # 2. Test pattern not found
            find_no_match_result = await client.call_tool("ssh_file_find_lines_with_pattern", {
                "file_path": test_file, "pattern": "nonexistent_pattern", "regex": False
            })
            no_match_json = json.loads(extract_result_text(find_no_match_result))
            assert no_match_json['total_matches'] == 0, "Pattern not found test failed"
            assert len(no_match_json['matches']) == 0

            # 3. Test on an empty file
            find_empty_file_result = await client.call_tool("ssh_file_find_lines_with_pattern", {
                "file_path": empty_file, "pattern": "anything", "regex": False
            })
            empty_file_json = json.loads(extract_result_text(find_empty_file_result))
            assert empty_file_json['total_matches'] == 0, "Search on empty file failed"
            assert len(empty_file_json['matches']) == 0

        finally:
            await client.call_tool("ssh_cmd_run", {
                "command": cleanup_file_command(test_file), "io_timeout": 5.0
            })
            await client.call_tool("ssh_cmd_run", {
                "command": cleanup_file_command(empty_file), "io_timeout": 5.0
            })
            await disconnect_ssh(client)
    print_test_footer()


@pytest.mark.asyncio
async def test_ssh_file_get_context_around_line_edge_cases(mcp_test_environment):
    """Test ssh_file_get_context_around_line for edge cases."""
    print_test_header("Testing 'ssh_file_get_context_around_line' edge cases")

    test_file_normal = remote_temp_path("test_context_edges_normal") + ".txt"
    test_file_short = remote_temp_path("test_context_edges_short") + ".txt"

    file_content_normal = """Line 1: First line
Line 2: Second line
Line 3: Target Middle
Line 4: Fourth line
Line 5: Last line"""

    file_content_short = """Line A
Line B"""

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            # Create test files using MCP tools (cross-platform)
            await client.call_tool("ssh_file_write", {"file_path": test_file_normal, "content": file_content_normal})
            await client.call_tool("ssh_file_write", {"file_path": test_file_short, "content": file_content_short})

            # 1. Match at the beginning of the file
            context_begin_result = await client.call_tool("ssh_file_get_context_around_line", {
                "file_path": test_file_normal, "match_line": "Line 1: First line", "context": 2
            })
            begin_json = json.loads(extract_result_text(context_begin_result))
            assert begin_json['match_found'] == True
            assert begin_json['match_line_number'] == 1
            assert len(begin_json['context_block']) == 3  # Line 1, Line 2, Line 3
            assert begin_json['context_block'][0]['content'] == "Line 1: First line"
            assert begin_json['context_block'][1]['content'] == "Line 2: Second line"
            assert begin_json['context_block'][2]['content'] == "Line 3: Target Middle"

            # 2. Match at the end of the file
            context_end_result = await client.call_tool("ssh_file_get_context_around_line", {
                "file_path": test_file_normal, "match_line": "Line 5: Last line", "context": 2
            })
            end_json = json.loads(extract_result_text(context_end_result))
            assert end_json['match_found'] == True
            assert end_json['match_line_number'] == 5
            assert len(end_json['context_block']) == 3  # Line 3, Line 4, Line 5
            assert end_json['context_block'][0]['content'] == "Line 3: Target Middle"
            assert end_json['context_block'][1]['content'] == "Line 4: Fourth line"
            assert end_json['context_block'][2]['content'] == "Line 5: Last line"

            # 3. File with fewer lines than 2 * context + 1
            context_short_file_result = await client.call_tool("ssh_file_get_context_around_line", {
                "file_path": test_file_short, "match_line": "Line A", "context": 3
            })
            short_file_json = json.loads(extract_result_text(context_short_file_result))
            assert short_file_json['match_found'] == True
            assert short_file_json['match_line_number'] == 1
            assert len(short_file_json['context_block']) == 2  # Line A, Line B (all lines)
            assert short_file_json['context_block'][0]['content'] == "Line A"
            assert short_file_json['context_block'][1]['content'] == "Line B"

        finally:
            await client.call_tool("ssh_cmd_run", {
                "command": cleanup_file_command(test_file_normal), "io_timeout": 5.0
            })
            await client.call_tool("ssh_cmd_run", {
                "command": cleanup_file_command(test_file_short), "io_timeout": 5.0
            })
            await disconnect_ssh(client)
    print_test_footer()


@pytest.mark.asyncio
async def test_ssh_file_copy_overwrite(mcp_test_environment):
    """Test ssh_file_copy overwrite behavior when destination exists and append_timestamp=False."""
    print_test_header("Testing 'ssh_file_copy' overwrite behavior")

    source_file = remote_temp_path("test_copy_source_overwrite") + ".txt"
    dest_file = remote_temp_path("test_copy_dest_overwrite") + ".txt"

    original_content_dest = "Initial content for destination file."
    new_content_source = "New content from source, should overwrite."

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            # 1. Create initial destination file using MCP tools
            await client.call_tool("ssh_file_write", {"file_path": dest_file, "content": original_content_dest})

            # 2. Create source file with different content
            await client.call_tool("ssh_file_write", {"file_path": source_file, "content": new_content_source})

            # 3. Copy source to destination (expect overwrite)
            copy_result = await client.call_tool("ssh_file_copy", {
                "source_path": source_file,
                "destination_path": dest_file,
                "append_timestamp": False,
                "use_sudo": False
            })
            copy_json = json.loads(extract_result_text(copy_result))
            assert copy_json['success'] == True, f"ssh_file_copy failed: {copy_json.get('error', '')}"
            assert copy_json['copied_to'] == dest_file

            # 4. Verify destination file content is overwritten
            read_result = await client.call_tool("ssh_cmd_run", {
                "command": read_file_command(dest_file), "io_timeout": 5.0
            })
            read_json = json.loads(extract_result_text(read_result))
            assert read_json['status'] == 'success'
            output = read_json['output'].strip().replace('\r\n', '\n')
            assert output == new_content_source, f"Destination file content was not overwritten. Got: {output}"
            assert output != original_content_dest, "Original destination content still present."

        finally:
            await client.call_tool("ssh_cmd_run", {
                "command": cleanup_file_command(source_file), "io_timeout": 5.0
            })
            await client.call_tool("ssh_cmd_run", {
                "command": cleanup_file_command(dest_file), "io_timeout": 5.0
            })
            await disconnect_ssh(client)
    print_test_footer()


@pytest.mark.asyncio
@linux_only
async def test_ssh_file_copy_overwrite_sudo(mcp_test_environment):
    """Test ssh_file_copy overwrite with sudo on root-owned files (Linux only - uses chown/sudo)."""
    print_test_header("Testing 'ssh_file_copy' overwrite behavior with sudo")

    source_file = remote_temp_path("test_copy_source_sudo") + ".txt"
    dest_file = remote_temp_path("test_copy_dest_sudo") + ".txt"

    original_content_dest = "Initial content for destination file."
    new_content_source = "New content from source, should overwrite."

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            # Create initial destination file, owned by root
            await client.call_tool("ssh_cmd_run", {
                "command": f"echo '{original_content_dest}' > {dest_file}", "io_timeout": 5.0, "use_sudo": True
            })
            await client.call_tool("ssh_cmd_run", {
                "command": f"chown root:root {dest_file}", "io_timeout": 5.0, "use_sudo": True
            })

            # Create source file (as normal user is fine)
            await client.call_tool("ssh_cmd_run", {
                "command": f"echo '{new_content_source}' > {source_file}", "io_timeout": 5.0
            })

            # Copy with sudo
            copy_sudo_result = await client.call_tool("ssh_file_copy", {
                "source_path": source_file,
                "destination_path": dest_file,
                "append_timestamp": False,
                "use_sudo": True
            })
            copy_sudo_json = json.loads(extract_result_text(copy_sudo_result))
            assert copy_sudo_json['success'] == True, f"ssh_file_copy with sudo failed: {copy_sudo_json.get('error', '')}"
            assert copy_sudo_json['copied_to'] == dest_file

            # Verify content (read with sudo as it might be root owned now)
            cat_sudo_result = await client.call_tool("ssh_cmd_run", {
                "command": f"cat {dest_file}", "io_timeout": 5.0, "use_sudo": True
            })
            cat_sudo_json = json.loads(extract_result_text(cat_sudo_result))
            assert cat_sudo_json['status'] == 'success'
            output_sudo = cat_sudo_json['output'].strip()
            assert output_sudo == new_content_source, "Destination file content was not overwritten with sudo."

        finally:
            await client.call_tool("ssh_cmd_run", {
                "command": f"rm -f {source_file} {dest_file}", "io_timeout": 5.0, "use_sudo": True
            })
            await disconnect_ssh(client)
    print_test_footer()
