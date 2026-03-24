import pytest
import json
import os
import time
from conftest import print_test_header, print_test_footer, make_connection, disconnect_ssh, extract_result_text, skip_on_windows

# Skip all tests in this module on Windows (sudo not available)
pytestmark = skip_on_windows
from cygnus_ssh_mcp.server import mcp
from fastmcp import Client


@pytest.mark.asyncio
async def test_ssh_dir_operations_with_sudo(mcp_test_environment):
    """Test directory operations that require sudo privileges."""
    print_test_header("Testing directory operations with sudo")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            
            # Check if we have sudo access
            sudo_check = await client.call_tool("ssh_conn_verify_sudo", {})
            sudo_json = json.loads(extract_result_text(sudo_check))
            
            if not sudo_json['available']:
                print("Skipping sudo tests as sudo is not available")
                return
            
            # Create a protected directory in /opt (requires sudo)
            timestamp = int(time.time())
            protected_dir = f"/opt/sudo_test_dir_{timestamp}"
            
            # Create directory with sudo
            mkdir_result = await client.call_tool("ssh_dir_mkdir", {
                "path": protected_dir,
                "use_sudo": True,
                "mode": 0o700  # Restrictive permissions
            })
            mkdir_json = json.loads(extract_result_text(mkdir_result))
            assert mkdir_json['status'] == 'success', f"Failed to create directory with sudo: {mkdir_json}"
            
            # Verify directory exists and has correct permissions
            stat_result = await client.call_tool("ssh_cmd_run", {
                "command": f"stat -c '%a %U:%G' {protected_dir}",
                "use_sudo": True
            })
            stat_json = json.loads(extract_result_text(stat_result))
            assert stat_json['status'] == 'success', "Failed to stat directory"
            # Should be "700 root:root" or similar
            assert "700" in stat_json['output'], f"Directory should have 700 permissions, got: {stat_json['output']}"
            
            # Create a subdirectory to test recursive operations
            subdir = f"{protected_dir}/subdir"
            await client.call_tool("ssh_dir_mkdir", {
                "path": subdir,
                "use_sudo": True
            })
            
            # Create a test file in the protected directory
            test_file = f"{protected_dir}/test_file.txt"
            await client.call_tool("ssh_cmd_run", {
                "command": f"echo 'Test content' > {test_file}",
                "use_sudo": True
            })
            
            # Test directory listing with sudo
            list_result = await client.call_tool("ssh_dir_list_advanced", {
                "path": protected_dir,
                "use_sudo": True
            })
            list_json = json.loads(extract_result_text(list_result))
            assert len(list_json) > 0, "Directory listing should return items"
            # The full path is returned, so we need to check if any item contains the test file name
            assert any('test_file.txt' in item.get('path', '') for item in list_json), "Test file not found in directory listing"
            
            # Test directory copy with sudo
            copy_dest = f"/opt/sudo_test_copy_{timestamp}"
            copy_result = await client.call_tool("ssh_dir_copy", {
                "source_path": protected_dir,
                "destination_path": copy_dest,
                "use_sudo": True
            })
            copy_json = json.loads(extract_result_text(copy_result))
            # The ssh_dir_copy tool might not return a 'success' key directly
            assert 'error' not in copy_json, f"Failed to copy directory with sudo: {copy_json}"
            
            # Verify copy exists
            verify_copy = await client.call_tool("ssh_cmd_run", {
                "command": f"ls -la {copy_dest}",
                "use_sudo": True
            })
            verify_json = json.loads(extract_result_text(verify_copy))
            assert verify_json['status'] == 'success', "Failed to verify copied directory"
            assert "test_file.txt" in verify_json['output'], "Test file not found in copied directory"
            
            # Test recursive directory deletion with sudo
            delete_result = await client.call_tool("ssh_dir_delete", {
                "path": protected_dir,
                "dry_run": False,
                "use_sudo": True
            })
            delete_json = json.loads(extract_result_text(delete_result))
            # The ssh_dir_delete tool might not return a 'success' key directly
            assert 'error' not in delete_json, f"Failed to delete directory with sudo: {delete_json}"
            
            # Verify directory was deleted
            verify_delete = await client.call_tool("ssh_cmd_run", {
                "command": f"ls -la {protected_dir} 2>/dev/null || echo 'Directory not found'",
                "use_sudo": True
            })
            verify_delete_json = json.loads(extract_result_text(verify_delete))
            assert "Directory not found" in verify_delete_json['output'], "Directory should have been deleted"
            
            # Clean up the copied directory
            await client.call_tool("ssh_dir_delete", {
                "path": copy_dest,
                "dry_run": False,
                "use_sudo": True
            })
            
        finally:
            # Ensure cleanup of any remaining test directories
            for path in [protected_dir, f"/opt/sudo_test_copy_{timestamp}"]:
                try:
                    await client.call_tool("ssh_cmd_run", {
                        "command": f"rm -rf {path}",
                        "use_sudo": True,
                        "io_timeout": 5.0
                    })
                except Exception:
                    pass  # Ignore cleanup errors
            await disconnect_ssh(client)
    
    print_test_footer()
