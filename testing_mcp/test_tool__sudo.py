import pytest
import json
import os
import tempfile
from conftest import print_test_header, print_test_footer, make_connection, disconnect_ssh
from mcp_ssh_server import mcp
from fastmcp import Client


@pytest.mark.asyncio
async def test_ssh_sudo_command_execution(mcp_test_environment):
    """Test executing commands with sudo privileges."""
    print_test_header("Testing sudo command execution")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            
            # Create a test file that requires sudo to access
            test_file = "/tmp/sudo_test_file.txt"
            test_content = "This is a sudo test file"
            
            # Create the file with sudo
            create_result = await client.call_tool("ssh_cmd_run", {
                "command": f"echo '{test_content}' > {test_file} && chmod 600 {test_file}",
                "sudo": True
            })
            create_json = json.loads(create_result[0].text)
            
            if create_json['status'] != 'success':
                print(f"Failed to create test file with sudo: {create_json}")
                return
            
            # Try to read the file without sudo (should fail)
            read_no_sudo = await client.call_tool("ssh_cmd_run", {
                "command": f"cat {test_file}",
                "sudo": False
            })
            no_sudo_json = json.loads(read_no_sudo[0].text)
            assert no_sudo_json['status'] != 'success', "Should not be able to read file without sudo"
            
            # Read the file with sudo (should succeed)
            read_with_sudo = await client.call_tool("ssh_cmd_run", {
                "command": f"cat {test_file}",
                "sudo": True
            })
            with_sudo_json = json.loads(read_with_sudo[0].text)
            assert with_sudo_json['status'] == 'success', f"Failed to read file with sudo: {with_sudo_json}"
            assert test_content in with_sudo_json['output'], "File content doesn't match expected"
            
        finally:
            # Clean up the test file
            await client.call_tool("ssh_cmd_run", {
                "command": f"rm -f {test_file}",
                "sudo": True,
                "io_timeout": 5.0
            })
            await disconnect_ssh(client)
    
    print_test_footer()


@pytest.mark.asyncio
async def test_ssh_sudo_file_operations(mcp_test_environment):
    """Test file operations that require sudo privileges."""
    print_test_header("Testing sudo file operations")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            
            # Create a protected directory and file
            protected_dir = "/tmp/sudo_protected_dir"
            protected_file = f"{protected_dir}/protected_file.txt"
            
            # Create directory with restricted permissions
            await client.call_tool("ssh_cmd_run", {
                "command": f"mkdir -p {protected_dir} && chmod 700 {protected_dir}",
                "sudo": True
            })
            
            # Create a file in the protected directory
            file_content = "This is a protected file that requires sudo to access"
            write_result = await client.call_tool("ssh_file_write", {
                "file_path": protected_file,
                "content": file_content,
                "sudo": True
            })
            write_json = json.loads(write_result[0].text)
            assert write_json['success'], f"Failed to write file with sudo: {write_json}"
            
            # Try to read the file with sudo
            read_result = await client.call_tool("ssh_cmd_run", {
                "command": f"cat {protected_file}",
                "sudo": True
            })
            read_json = json.loads(read_result[0].text)
            assert read_json['status'] == 'success', f"Failed to read file with sudo: {read_json}"
            assert file_content in read_json['output'], "File content doesn't match expected"
            
            # Try to modify the file with sudo
            modified_content = "This content was modified with sudo"
            modify_result = await client.call_tool("ssh_file_write", {
                "file_path": protected_file,
                "content": modified_content,
                "sudo": True
            })
            modify_json = json.loads(modify_result[0].text)
            assert modify_json['success'], f"Failed to modify file with sudo: {modify_json}"
            
            # Verify the modification
            verify_result = await client.call_tool("ssh_cmd_run", {
                "command": f"cat {protected_file}",
                "sudo": True
            })
            verify_json = json.loads(verify_result[0].text)
            assert modified_content in verify_json['output'], "Modified content not found"
            
        finally:
            # Clean up
            await client.call_tool("ssh_cmd_run", {
                "command": f"rm -rf {protected_dir}",
                "sudo": True,
                "io_timeout": 5.0
            })
            await disconnect_ssh(client)
    
    print_test_footer()


@pytest.mark.asyncio
async def test_ssh_verify_sudo_access(mcp_test_environment):
    """Test the ssh_conn_verify_sudo tool."""
    print_test_header("Testing 'ssh_conn_verify_sudo' tool")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            
            # Check sudo access
            sudo_result = await client.call_tool("ssh_conn_verify_sudo", {})
            sudo_access = json.loads(sudo_result[0].text)
            
            # Verify we get the expected dictionary response
            assert isinstance(sudo_access, dict), f"Expected dictionary result, got: {sudo_access}"
            assert 'available' in sudo_access, "Missing 'available' key in sudo access response"
            assert 'passwordless' in sudo_access, "Missing 'passwordless' key in sudo access response"
            assert 'requires_password' in sudo_access, "Missing 'requires_password' key in sudo access response"
            
            # If sudo is available, try a simple sudo command
            if sudo_access['available']:
                cmd_result = await client.call_tool("ssh_cmd_run", {
                    "command": "id",
                    "sudo": True
                })
                cmd_json = json.loads(cmd_result[0].text)
                assert cmd_json['status'] == 'success', f"Sudo command failed: {cmd_json}"
                assert "uid=0(root)" in cmd_json['output'], "Expected root user ID in output"
            
        finally:
            await disconnect_ssh(client)
    
    print_test_footer()
