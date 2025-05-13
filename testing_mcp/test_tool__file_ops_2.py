import pytest
import json
import os
from conftest import print_test_header, print_test_footer, make_connection, disconnect_ssh
from mcp_ssh_server import mcp
from fastmcp import Client
import time # For unique file/dir names

# Helper to create a unique temporary path on the remote server
def remote_temp_path(base_name):
    return f"/tmp/{base_name}_{int(time.time())}_{os.getpid()}_{time.monotonic_ns()}"

@pytest.mark.asyncio
async def test_ssh_dir_mkdir_sudo(mcp_test_environment):
    """Test ssh_dir_mkdir with sudo=True."""
    print_test_header("Testing 'ssh_dir_mkdir' with sudo")
    test_dir_base = "test_mcp_sudo_dir"
    # Using /tmp for sudo tests to avoid issues with /root if not fully permissive
    # The key is that the 'mkdir' command itself is run with sudo.
    # A more robust test might try to create in a place only root can write,
    # but that depends heavily on the test environment's strictness.
    # For now, we verify sudo is *used* by the tool.
    test_dir = remote_temp_path(test_dir_base)


    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            # Ensure directory does not exist (cleanup from previous failed run if any)
            await client.call_tool("ssh_cmd_run", {
                "command": f"sudo rm -rf {test_dir}",
                "io_timeout": 10.0,
                "sudo": True # The cleanup command itself might need sudo
            })

            # Test create directory with sudo
            mkdir_result = await client.call_tool("ssh_dir_mkdir", {
                "path": test_dir,
                "mode": 0o755,
                "sudo": True
            })
            mkdir_json = json.loads(mkdir_result[0].text)
            assert mkdir_json['status'] == 'success', f"ssh_dir_mkdir with sudo failed: {mkdir_json.get('message', '')}"

            # Verify directory exists and check ownership (should be root if sudo worked as expected)
            stat_result = await client.call_tool("ssh_cmd_run", {
                "command": f"stat -c '%U' {test_dir}", # Get username of owner
                "io_timeout": 5.0,
                "sudo": False # Stat can be run as normal user
            })
            stat_json = json.loads(stat_result[0].text)
            assert stat_json['status'] == 'success', f"Stat command failed: {stat_json.get('error', '')}"
            owner = stat_json['output'].strip()
            # This assertion depends on the SSH user NOT being root.
            # If the SSH user is root, then sudo doesn't change owner from the user.
            # A common test setup might use a non-root user with passwordless sudo.
            assert owner == "root", f"Directory owner should be root when created with sudo, but was '{owner}'"

        finally:
            # Cleanup
            await client.call_tool("ssh_cmd_run", {
                "command": f"sudo rm -rf {test_dir}",
                "io_timeout": 10.0,
                "sudo": True
            })
            await disconnect_ssh(client)
    print_test_footer()

@pytest.mark.asyncio
async def test_ssh_file_modify_sudo_and_force(mcp_test_environment):
    """Test file modification with sudo=True and force=True on a root-owned file."""
    print_test_header("Testing file modification with sudo and force")
    
    test_file_base = "test_mcp_sudo_force_file.txt"
    test_file = remote_temp_path(test_file_base)
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
                "sudo": True
            })
            chown_json = json.loads(chown_result[0].text)
            assert chown_json['status'] == 'success', f"sudo chown failed: {chown_json.get('error', '')}"

            # 3. Attempt to replace line with sudo=True and force=True
            # The 'force=True' is key if the SshFileOperations_Linux._replace_content_sudo
            # tries an initial non-sudo read which would fail on a root-owned file.
            replace_result = await client.call_tool("ssh_file_replace_line_by_content", {
                "file_path": test_file,
                "match_line": original_content,
                "new_lines": [new_line_content],
                "sudo": True,
                "force": True 
            })
            replace_json = json.loads(replace_result[0].text)
            assert replace_json['success'] == True, f"ssh_file_replace_line_by_content with sudo/force failed: {replace_json.get('error', '')}"

            # 4. Verify content (read as root to be sure)
            cat_result = await client.call_tool("ssh_cmd_run", {
                "command": f"cat {test_file}",
                "io_timeout": 5.0,
                "sudo": True # Read with sudo to ensure we can access it
            })
            cat_json = json.loads(cat_result[0].text)
            assert cat_json['status'] == 'success'
            output = cat_json['output']
            assert new_line_content in output, "New line not found after sudo/force replace."
            assert original_content not in output, "Original content still present after sudo/force replace."

            # 5. Verify permissions and ownership were restored (or handled reasonably)
            # SshFileOperations_Linux._replace_content_sudo attempts to restore.
            stat_perm_result = await client.call_tool("ssh_cmd_run", {
                "command": f"stat -c '%U:%G %a' {test_file}", # User:Group Perms
                "io_timeout": 5.0,
                "sudo": False # Check as normal user
            })
            stat_perm_json = json.loads(stat_perm_result[0].text)
            assert stat_perm_json['status'] == 'success'
            perms_owner_group = stat_perm_json['output'].strip()
            # Expected: root:root and original perms (or perms of temp file if original couldn't be stat'd)
            # This can be tricky if original perms were very restrictive.
            # For now, we check it's still root owned.
            assert perms_owner_group.startswith("root:root"), f"File ownership should be root:root, but was '{perms_owner_group}'"


        finally:
            # Cleanup (remove the file with sudo)
            await client.call_tool("ssh_cmd_run", {
                "command": f"rm -f {test_file}",
                "io_timeout": 5.0,
                "sudo": True
            })
            await disconnect_ssh(client)
    print_test_footer()

@pytest.mark.asyncio
async def test_ssh_dir_remove_recursive_with_content(mcp_test_environment):
    """Test ssh_dir_remove with recursive=True on a directory with content."""
    print_test_header("Testing 'ssh_dir_remove' with recursive=True and content")
    
    parent_dir_base = "test_mcp_parent_rec_remove"
    parent_dir = remote_temp_path(parent_dir_base)
    inner_file = f"{parent_dir}/somefile.txt"

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            # Setup: Create directory and a file inside it
            await client.call_tool("ssh_dir_mkdir", {"path": parent_dir, "mode": 0o755})
            await client.call_tool("ssh_cmd_run", {
                "command": f"echo 'hello' > {inner_file}",
                "io_timeout": 5.0
            })

            # Test remove directory recursively
            rmdir_result = await client.call_tool("ssh_dir_remove", {
                "path": parent_dir,
                "recursive": True
            })
            rmdir_json = json.loads(rmdir_result[0].text)
            assert rmdir_json['status'] == 'success', f"ssh_dir_remove recursive failed: {rmdir_json.get('message', '')}"

            # Verify directory no longer exists
            stat_result = await client.call_tool("ssh_file_stat", {"path": parent_dir})
            stat_json = json.loads(stat_result[0].text) # ssh_file_stat returns JSON directly
            assert stat_json.get('exists') == False, f"Directory '{parent_dir}' should have been removed."

        finally:
            # Ensure cleanup if test failed before removal
            # Check if it exists before trying to remove, to avoid error if already gone
            stat_check_result = await client.call_tool("ssh_file_stat", {"path": parent_dir})
            # Ensure stat_check_result[0].text is valid JSON before parsing
            try:
                stat_check_json = json.loads(stat_check_result[0].text)
                if stat_check_json.get('exists') == True:
                    await client.call_tool("ssh_dir_remove", {
                        "path": parent_dir,
                        "recursive": True # Recursive to clean up any potential leftovers
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
    inner_file = f"{parent_dir}/somefile.txt"

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            # Setup: Create directory and a file inside it
            await client.call_tool("ssh_dir_mkdir", {"path": parent_dir, "mode": 0o755})
            await client.call_tool("ssh_cmd_run", {
                "command": f"echo 'hello' > {inner_file}",
                "io_timeout": 5.0
            })

            # Test remove directory non-recursively (expect failure)
            # The ssh_dir_remove tool re-raises exceptions from SshClient.rmdir,
            # which in turn re-raises CommandFailed from SshClient.run.
            # FastMCP client.call_tool will raise an exception if the tool raises one.
            with pytest.raises(Exception) as excinfo: # Or a more specific FastMCP/SshError if available
                await client.call_tool("ssh_dir_remove", {
                    "path": parent_dir,
                    "recursive": False
                })
            
            # Check if the exception message contains relevant info (e.g., "Directory not empty")
            # This depends on the exact error message from 'rmdir' on the target OS.
            assert "Directory not empty" in str(excinfo.value) or "Failed to remove directory" in str(excinfo.value)


            # Verify directory still exists
            stat_result = await client.call_tool("ssh_file_stat", {"path": parent_dir})
            stat_json = json.loads(stat_result[0].text)
            assert stat_json.get('exists') == True, f"Directory '{parent_dir}' should still exist."

        finally:
            # Cleanup (recursively, as it should still contain the file)
            await client.call_tool("ssh_dir_remove", {
                "path": parent_dir,
                "recursive": True,
                "sudo": False # Assuming normal user created it
            })
            await disconnect_ssh(client)
    print_test_footer()

@pytest.mark.asyncio
async def test_ssh_file_find_lines_with_pattern_variations(mcp_test_environment):
    """Test ssh_file_find_lines_with_pattern with regex, no match, and empty file."""
    print_test_header("Testing 'ssh_file_find_lines_with_pattern' variations")
    
    test_file_base = "test_find_pattern_vars"
    test_file = remote_temp_path(test_file_base + ".txt")
    empty_file = remote_temp_path(test_file_base + "_empty.txt")

    file_content = """Line 1: apple
Line 2: banana
Line 3: Apple Pie
Line 4: orange123"""

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            # Create test file
            await client.call_tool("ssh_cmd_run", {
                "command": f"echo '{file_content}' > {test_file}", "io_timeout": 5.0
            })
            # Create empty file
            await client.call_tool("ssh_cmd_run", {
                "command": f"touch {empty_file}", "io_timeout": 5.0
            })

            # 1. Test with regex
            find_regex_result = await client.call_tool("ssh_file_find_lines_with_pattern", {
                "file_path": test_file, "pattern": "^Line \\d: [aA]pple.*", "regex": True
            })
            regex_json = json.loads(find_regex_result[0].text)
            assert regex_json['total_matches'] == 2, "Regex search failed to find correct matches"
            assert "Line 1: apple" in regex_json['matches'][0]['content']
            assert "Line 3: Apple Pie" in regex_json['matches'][1]['content']

            # 2. Test pattern not found
            find_no_match_result = await client.call_tool("ssh_file_find_lines_with_pattern", {
                "file_path": test_file, "pattern": "nonexistent_pattern", "regex": False
            })
            no_match_json = json.loads(find_no_match_result[0].text)
            assert no_match_json['total_matches'] == 0, "Pattern not found test failed"
            assert len(no_match_json['matches']) == 0

            # 3. Test on an empty file
            find_empty_file_result = await client.call_tool("ssh_file_find_lines_with_pattern", {
                "file_path": empty_file, "pattern": "anything", "regex": False
            })
            empty_file_json = json.loads(find_empty_file_result[0].text)
            assert empty_file_json['total_matches'] == 0, "Search on empty file failed"
            assert len(empty_file_json['matches']) == 0
            
        finally:
            await client.call_tool("ssh_cmd_run", {
                "command": f"rm -f {test_file} {empty_file}", "io_timeout": 5.0
            })
            await disconnect_ssh(client)
    print_test_footer()

@pytest.mark.asyncio
async def test_ssh_file_get_context_around_line_edge_cases(mcp_test_environment):
    """Test ssh_file_get_context_around_line for edge cases."""
    print_test_header("Testing 'ssh_file_get_context_around_line' edge cases")

    test_file_base = "test_context_edges"
    test_file_normal = remote_temp_path(test_file_base + "_normal.txt")
    test_file_short = remote_temp_path(test_file_base + "_short.txt")

    file_content_normal = """Line 1: First line
Line 2: Second line
Line 3: Target Middle
Line 4: Fourth line
Line 5: Last line"""

    file_content_short = """Line A
Line B""" # Only 2 lines

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            await client.call_tool("ssh_cmd_run", {
                "command": f"echo '{file_content_normal}' > {test_file_normal}", "io_timeout": 5.0
            })
            await client.call_tool("ssh_cmd_run", {
                "command": f"echo '{file_content_short}' > {test_file_short}", "io_timeout": 5.0
            })

            # 1. Match at the beginning of the file
            context_begin_result = await client.call_tool("ssh_file_get_context_around_line", {
                "file_path": test_file_normal, "match_line": "Line 1: First line", "context": 2
            })
            begin_json = json.loads(context_begin_result[0].text)
            assert begin_json['match_found'] == True
            assert begin_json['match_line_number'] == 1
            assert len(begin_json['context_block']) == 3 # Line 1, Line 2, Line 3
            assert begin_json['context_block'][0]['content'] == "Line 1: First line"
            assert begin_json['context_block'][1]['content'] == "Line 2: Second line"
            assert begin_json['context_block'][2]['content'] == "Line 3: Target Middle"


            # 2. Match at the end of the file
            context_end_result = await client.call_tool("ssh_file_get_context_around_line", {
                "file_path": test_file_normal, "match_line": "Line 5: Last line", "context": 2
            })
            end_json = json.loads(context_end_result[0].text)
            assert end_json['match_found'] == True
            assert end_json['match_line_number'] == 5
            assert len(end_json['context_block']) == 3 # Line 3, Line 4, Line 5
            assert end_json['context_block'][0]['content'] == "Line 3: Target Middle"
            assert end_json['context_block'][1]['content'] == "Line 4: Fourth line"
            assert end_json['context_block'][2]['content'] == "Line 5: Last line"

            # 3. File with fewer lines than 2 * context + 1
            context_short_file_result = await client.call_tool("ssh_file_get_context_around_line", {
                "file_path": test_file_short, "match_line": "Line A", "context": 3
            })
            short_file_json = json.loads(context_short_file_result[0].text)
            assert short_file_json['match_found'] == True
            assert short_file_json['match_line_number'] == 1
            assert len(short_file_json['context_block']) == 2 # Line A, Line B (all lines)
            assert short_file_json['context_block'][0]['content'] == "Line A"
            assert short_file_json['context_block'][1]['content'] == "Line B"

        finally:
            await client.call_tool("ssh_cmd_run", {
                "command": f"rm -f {test_file_normal} {test_file_short}", "io_timeout": 5.0
            })
            await disconnect_ssh(client)
    print_test_footer()

@pytest.mark.asyncio
async def test_ssh_file_copy_overwrite(mcp_test_environment):
    """Test ssh_file_copy overwrite behavior when destination exists and append_timestamp=False."""
    print_test_header("Testing 'ssh_file_copy' overwrite behavior")

    source_file_base = "test_copy_source_overwrite"
    dest_file_base = "test_copy_dest_overwrite"
    source_file = remote_temp_path(source_file_base + ".txt")
    dest_file = remote_temp_path(dest_file_base + ".txt") # Fixed destination name

    original_content_source = "Original content for source file."
    original_content_dest = "Initial content for destination file."
    new_content_source = "New content from source, should overwrite."

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            # 1. Create initial destination file
            await client.call_tool("ssh_cmd_run", {
                "command": f"echo '{original_content_dest}' > {dest_file}", "io_timeout": 5.0
            })

            # 2. Create source file with different content
            await client.call_tool("ssh_cmd_run", {
                "command": f"echo '{new_content_source}' > {source_file}", "io_timeout": 5.0
            })

            # 3. Copy source to destination (expect overwrite)
            copy_result = await client.call_tool("ssh_file_copy", {
                "source_path": source_file,
                "destination_path": dest_file,
                "append_timestamp": False,
                "sudo": False # Test non-sudo path first
            })
            copy_json = json.loads(copy_result[0].text)
            assert copy_json['success'] == True, f"ssh_file_copy failed: {copy_json.get('error', '')}"
            assert copy_json['copied_to'] == dest_file

            # 4. Verify destination file content is overwritten
            cat_result = await client.call_tool("ssh_cmd_run", {
                "command": f"cat {dest_file}", "io_timeout": 5.0
            })
            cat_json = json.loads(cat_result[0].text)
            assert cat_json['status'] == 'success'
            output = cat_json['output'].strip()
            assert output == new_content_source, "Destination file content was not overwritten."
            assert output != original_content_dest, "Original destination content still present."

            # 5. Test with sudo (should also overwrite)
            # Recreate initial destination file, owned by root
            await client.call_tool("ssh_cmd_run", {
                "command": f"echo '{original_content_dest}' > {dest_file}", "io_timeout": 5.0, "sudo": True
            })
            await client.call_tool("ssh_cmd_run", {
                "command": f"chown root:root {dest_file}", "io_timeout": 5.0, "sudo": True
            })
            # Recreate source file (as normal user is fine)
            await client.call_tool("ssh_cmd_run", {
                "command": f"echo '{new_content_source}' > {source_file}", "io_timeout": 5.0
            })

            copy_sudo_result = await client.call_tool("ssh_file_copy", {
                "source_path": source_file,
                "destination_path": dest_file,
                "append_timestamp": False,
                "sudo": True
            })
            copy_sudo_json = json.loads(copy_sudo_result[0].text)
            assert copy_sudo_json['success'] == True, f"ssh_file_copy with sudo failed: {copy_sudo_json.get('error', '')}"
            assert copy_sudo_json['copied_to'] == dest_file
            
            # Verify content (read with sudo as it might be root owned now)
            cat_sudo_result = await client.call_tool("ssh_cmd_run", {
                "command": f"cat {dest_file}", "io_timeout": 5.0, "sudo": True
            })
            cat_sudo_json = json.loads(cat_sudo_result[0].text)
            assert cat_sudo_json['status'] == 'success'
            output_sudo = cat_sudo_json['output'].strip()
            assert output_sudo == new_content_source, "Destination file content was not overwritten with sudo."


        finally:
            await client.call_tool("ssh_cmd_run", {
                "command": f"rm -f {source_file} {dest_file}", "io_timeout": 5.0, "sudo": True # sudo for dest if root owned
            })
            await disconnect_ssh(client)
    print_test_footer()
