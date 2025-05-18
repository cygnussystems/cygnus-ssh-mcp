import pytest
import json
import time
from conftest import print_test_header, print_test_footer, make_connection, disconnect_ssh
from mcp_ssh_server import mcp
from fastmcp import Client


@pytest.mark.asyncio
async def test_ssh_archive_operations_with_sudo(mcp_test_environment):
    """Test archive operations that require sudo privileges."""
    print_test_header("Testing archive operations with sudo")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            
            # Check if we have sudo access
            sudo_check = await client.call_tool("ssh_conn_verify_sudo", {})
            sudo_json = json.loads(sudo_check[0].text)
            
            if not sudo_json['available']:
                print("Skipping sudo tests as sudo is not available")
                return
            
            # Create a protected directory in /opt (requires sudo)
            timestamp = int(time.time())
            protected_dir = f"/opt/sudo_archive_test_{timestamp}"
            archive_path = f"/opt/sudo_archive_test_{timestamp}.tar.gz"
            extract_dir = f"/opt/sudo_archive_extract_{timestamp}"
            
            # Create directory with sudo
            mkdir_result = await client.call_tool("ssh_dir_mkdir", {
                "path": protected_dir,
                "sudo": True,
                "mode": 0o700  # Restrictive permissions
            })
            mkdir_json = json.loads(mkdir_result[0].text)
            assert mkdir_json['status'] == 'success', f"Failed to create directory with sudo: {mkdir_json}"
            
            # Create some test files in the protected directory
            for i in range(3):
                file_path = f"{protected_dir}/test_file_{i}.txt"
                await client.call_tool("ssh_cmd_run", {
                    "command": f"echo 'Test content {i}' > {file_path}",
                    "sudo": True
                })
            
            # Create archive with sudo
            create_result = await client.call_tool("ssh_archive_create", {
                "source_path": protected_dir,
                "archive_path": archive_path,
                "format": "tar.gz",
                "sudo": True
            })
            create_json = json.loads(create_result[0].text)
            assert create_json['success'], f"Failed to create archive with sudo: {create_json}"
            
            # Verify archive exists
            verify_archive = await client.call_tool("ssh_cmd_run", {
                "command": f"ls -la {archive_path}",
                "sudo": True
            })
            verify_json = json.loads(verify_archive[0].text)
            assert verify_json['status'] == 'success', "Failed to verify archive"
            assert archive_path in verify_json['output'], "Archive not found"
            
            # Extract archive with sudo
            extract_result = await client.call_tool("ssh_archive_extract", {
                "archive_path": archive_path,
                "destination_path": extract_dir,
                "overwrite": False,
                "sudo": True
            })
            extract_json = json.loads(extract_result[0].text)
            assert extract_json['success'], f"Failed to extract archive with sudo: {extract_json}"
            
            # Verify extraction
            verify_extract = await client.call_tool("ssh_cmd_run", {
                "command": f"ls -la {extract_dir}",
                "sudo": True
            })
            verify_extract_json = json.loads(verify_extract[0].text)
            assert verify_extract_json['status'] == 'success', "Failed to verify extraction"
            assert "test_file_0.txt" in verify_extract_json['output'], "Extracted files not found"
            assert "test_file_1.txt" in verify_extract_json['output'], "Extracted files not found"
            assert "test_file_2.txt" in verify_extract_json['output'], "Extracted files not found"
            
        finally:
            # Clean up
            for path in [protected_dir, archive_path, extract_dir]:
                try:
                    await client.call_tool("ssh_cmd_run", {
                        "command": f"rm -rf {path}",
                        "sudo": True,
                        "io_timeout": 5.0
                    })
                except Exception:
                    pass  # Ignore cleanup errors
            await disconnect_ssh(client)
    
    print_test_footer()
