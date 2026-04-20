import pytest
import json
import time
from conftest import (
    print_test_header, print_test_footer, make_connection, disconnect_ssh,
    extract_result_text, remote_temp_path, cleanup_remote_path,
    TEST_WORKSPACE, PATH_SEP, IS_WINDOWS
)

from cygnus_ssh_mcp.server import mcp
from fastmcp import Client


@pytest.mark.asyncio
async def test_ssh_dir_operations_with_sudo(mcp_test_environment):
    """Test directory operations that require sudo privileges (cross-platform)."""
    print_test_header("Testing directory operations with sudo")

    async with Client(mcp) as client:
        timestamp = int(time.time())
        protected_dir = f"{TEST_WORKSPACE}{PATH_SEP}sudo_test_dir_{timestamp}"
        copy_dest = f"{TEST_WORKSPACE}{PATH_SEP}sudo_test_copy_{timestamp}"

        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            # Check if we have sudo access (on Windows, checks for admin elevation)
            sudo_check = await client.call_tool("ssh_conn_verify_sudo", {})
            sudo_json = json.loads(extract_result_text(sudo_check))

            if not sudo_json['available']:
                print("Skipping sudo tests as sudo/elevation is not available")
                return

            # Create directory with sudo (on Windows: ignored, on Linux: runs as root)
            mkdir_result = await client.call_tool("ssh_dir_mkdir", {
                "path": protected_dir,
                "use_sudo": True,
                "mode": 0o700  # Restrictive permissions
            })
            mkdir_json = json.loads(extract_result_text(mkdir_result))
            assert mkdir_json['status'] == 'success', f"Failed to create directory with sudo: {mkdir_json}"

            # Verify directory exists (cross-platform)
            stat_result = await client.call_tool("ssh_file_stat", {"path": protected_dir})
            stat_json = json.loads(extract_result_text(stat_result))
            assert stat_json.get('exists') == True, "Directory was not created"

            # On Linux only: verify permissions (stat -c doesn't exist on Windows)
            if not IS_WINDOWS:
                perm_result = await client.call_tool("ssh_cmd_run", {
                    "command": f"stat -c '%a %U:%G' {protected_dir}",
                    "use_sudo": True
                })
                perm_json = json.loads(extract_result_text(perm_result))
                if perm_json['status'] == 'success':
                    assert "700" in perm_json['output'], f"Directory should have 700 permissions, got: {perm_json['output']}"

            # Create a subdirectory to test recursive operations
            subdir = f"{protected_dir}{PATH_SEP}subdir"
            await client.call_tool("ssh_dir_mkdir", {
                "path": subdir,
                "use_sudo": True
            })

            # Create a test file in the protected directory using ssh_file_write (cross-platform)
            test_file = f"{protected_dir}{PATH_SEP}test_file.txt"
            await client.call_tool("ssh_file_write", {
                "file_path": test_file,
                "content": "Test content",
                "use_sudo": True
            })

            # Test directory listing with sudo
            list_result = await client.call_tool("ssh_dir_list_advanced", {
                "path": protected_dir,
                "use_sudo": True
            })
            list_json = json.loads(extract_result_text(list_result))
            assert len(list_json) > 0, "Directory listing should return items"
            assert any('test_file.txt' in item.get('path', '') for item in list_json), "Test file not found in directory listing"

            # Test directory copy with sudo
            copy_result = await client.call_tool("ssh_dir_copy", {
                "source_path": protected_dir,
                "destination_path": copy_dest,
                "use_sudo": True
            })
            copy_json = json.loads(extract_result_text(copy_result))
            assert 'error' not in copy_json, f"Failed to copy directory with sudo: {copy_json}"

            # Verify copy exists using ssh_file_stat (cross-platform)
            verify_stat = await client.call_tool("ssh_file_stat", {"path": copy_dest})
            verify_stat_json = json.loads(extract_result_text(verify_stat))
            assert verify_stat_json.get('exists') == True, "Copied directory doesn't exist"

            # Test recursive directory deletion with sudo
            delete_result = await client.call_tool("ssh_dir_delete", {
                "path": protected_dir,
                "dry_run": False,
                "use_sudo": True
            })
            delete_json = json.loads(extract_result_text(delete_result))
            assert 'error' not in delete_json, f"Failed to delete directory with sudo: {delete_json}"

            # Verify directory was deleted using ssh_file_stat (cross-platform)
            verify_del = await client.call_tool("ssh_file_stat", {"path": protected_dir})
            verify_del_json = json.loads(extract_result_text(verify_del))
            assert verify_del_json.get('exists') == False, "Directory should have been deleted"

        finally:
            # Ensure cleanup of any remaining test directories
            await cleanup_remote_path(client, protected_dir)
            await cleanup_remote_path(client, copy_dest)
            await disconnect_ssh(client)

    print_test_footer()
