import pytest
import json
from conftest import print_test_header, print_test_footer, make_connection, disconnect_ssh
from mcp_ssh_server import mcp
from fastmcp import Client

@pytest.mark.asyncio
async def test_ssh_search_files(mcp_test_environment):
    """Test searching for files in directories."""
    print_test_header("Testing 'ssh_dir_search_glob' tool")
    
    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Connection failed"
            test_dir = "/tmp/ssh_test_search"
            
            # Setup test files
            await client.call_tool("ssh_cmd_run", {
                "command": f"""
                rm -rf {test_dir}
                mkdir -p {test_dir}/{{dir1,dir2,dir3}}
                touch {test_dir}/{{file1.txt,file2.log}}
                touch {test_dir}/dir1/file3.txt {test_dir}/dir2/file4.log
                """,
                "io_timeout": 10.0
            })

            # Test .txt files search
            result = await client.call_tool("ssh_dir_search_glob", {
                "path": test_dir,
                "pattern": "*.txt",
                "max_depth": None,
                "include_dirs": False
            })
            files = json.loads(result[0].text)
            paths = [f['path'] for f in files]
            assert all(f"{test_dir}/{p}" in paths for p in ["file1.txt", "dir1/file3.txt"])

            # Test .log files search
            result = await client.call_tool("ssh_dir_search_glob", {
                "path": test_dir,
                "pattern": "*.log",
                "max_depth": None,
                "include_dirs": False
            })
            assert len(json.loads(result[0].text)) >= 2
            
        finally:
            await client.call_tool("ssh_cmd_run", {"command": f"rm -rf {test_dir}", "io_timeout": 5.0})
            await disconnect_ssh(client)
    
    print_test_footer()

@pytest.mark.asyncio
async def test_ssh_directory_size(mcp_test_environment):
    """Test calculating directory size."""
    print_test_header("Testing 'ssh_dir_calc_size' tool")
    
    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Connection failed"
            test_dir = "/tmp/ssh_test_size"
            
            await client.call_tool("ssh_cmd_run", {
                "command": f"""
                rm -rf {test_dir}
                mkdir -p {test_dir}
                dd if=/dev/zero of={test_dir}/file1.bin bs=1M count=1
                dd if=/dev/zero of={test_dir}/file2.bin bs=1M count=2
                sync
                """,
                "io_timeout": 10.0
            })

            result = await client.call_tool("ssh_dir_calc_size", {"path": test_dir})
            size_data = json.loads(result[0].text)
            
            assert 'size_bytes' in size_data and 'size_human' in size_data
            assert size_data['size_bytes'] >= 3 * 1024 * 1024  # 3MB minimum
            
        finally:
            await client.call_tool("ssh_cmd_run", {"command": f"rm -rf {test_dir}", "io_timeout": 5.0})
            await disconnect_ssh(client)
    
    print_test_footer()

@pytest.mark.asyncio
async def test_ssh_list_directory(mcp_test_environment):
    """Test recursive directory listing."""
    print_test_header("Testing 'ssh_dir_list_advanced' tool")
    
    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Connection failed"
            test_dir = "/tmp/ssh_test_list"
            
            await client.call_tool("ssh_cmd_run", {
                "command": f"""
                rm -rf {test_dir}
                mkdir -p {test_dir}/dir1/subdir1 {test_dir}/dir2
                touch {test_dir}/{{file1.txt,dir1/file2.txt,dir1/subdir1/file3.txt,dir2/file4.txt}}
                """,
                "io_timeout": 10.0
            })

            # Test full recursive list
            result = await client.call_tool("ssh_dir_list_advanced", {"path": test_dir})
            entries = json.loads(result[0].text)
            paths = [e['path'] for e in entries]
            assert all(p in paths for p in [
                f"{test_dir}/dir1/subdir1/file3.txt",
                f"{test_dir}/dir1",
                f"{test_dir}/dir2"
            ])

            # Test depth-limited list
            result = await client.call_tool("ssh_dir_list_advanced", {
                "path": test_dir,
                "max_depth": 1
            })
            assert len(json.loads(result[0].text)) <= 4  # dir1, dir2, file1.txt
            
        finally:
            await client.call_tool("ssh_cmd_run", {"command": f"rm -rf {test_dir}", "io_timeout": 5.0})
            await disconnect_ssh(client)
    
    print_test_footer()




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
            assert rmdir_json[
                       'status'] == 'success', f"ssh_dir_remove recursive failed: {rmdir_json.get('message', '')}"

            # Verify directory no longer exists
            stat_result = await client.call_tool("ssh_file_stat", {"path": parent_dir})
            stat_json = json.loads(stat_result[0].text)  # ssh_file_stat returns JSON directly
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
                        "recursive": True  # Recursive to clean up any potential leftovers
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
            with pytest.raises(Exception) as excinfo:  # Or a more specific FastMCP/SshError if available
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
                "sudo": False  # Assuming normal user created it
            })
            await disconnect_ssh(client)
    print_test_footer()
