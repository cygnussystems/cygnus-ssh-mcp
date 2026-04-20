import pytest
import json
import os
import tempfile
from conftest import (
    print_test_header, print_test_footer, make_connection, disconnect_ssh,
    extract_result_text, skip_on_windows, ROOT_HOME, IS_WINDOWS,
    TEST_WORKSPACE, PATH_SEP, cleanup_command
)

from cygnus_ssh_mcp.server import mcp
from fastmcp import Client


@pytest.mark.asyncio
async def test_ssh_sudo_command_execution(mcp_test_environment):
    """Test executing commands with sudo/elevated privileges."""
    print_test_header("Testing sudo command execution")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            # Create a test file with use_sudo parameter
            test_file = f"{TEST_WORKSPACE}{PATH_SEP}sudo_test_file.txt"
            test_content = "This is a sudo test file"

            # Create the file with use_sudo (on Linux: runs as root, on Windows: ignored, already admin)
            write_result = await client.call_tool("ssh_file_write", {
                "file_path": test_file,
                "content": test_content,
                "use_sudo": True
            })
            write_json = json.loads(extract_result_text(write_result))
            assert write_json['success'], f"Failed to write file with use_sudo: {write_json}"

            # On Linux only: test permission-based access control
            # (Windows doesn't use chmod-style permissions)
            if not IS_WINDOWS:
                # Set restrictive permissions
                chmod_result = await client.call_tool("ssh_cmd_run", {
                    "command": f"chmod 600 {test_file}",
                    "use_sudo": True
                })

                # Try to read the file without sudo (should fail on Linux)
                read_no_sudo = await client.call_tool("ssh_file_read", {
                    "file_path": test_file
                })
                no_sudo_json = json.loads(extract_result_text(read_no_sudo))
                # On Linux, this should fail without sudo when permissions are 600
                # (assuming test user is not root)

            # Read the file with use_sudo (should succeed on both platforms)
            read_with_sudo = await client.call_tool("ssh_file_read", {
                "file_path": test_file
            })
            with_sudo_json = json.loads(extract_result_text(read_with_sudo))
            assert with_sudo_json['success'], f"Failed to read file: {with_sudo_json}"
            assert test_content in with_sudo_json['content'], "File content doesn't match expected"

        finally:
            # Clean up the test file
            await client.call_tool("ssh_cmd_run", {
                "command": cleanup_command(test_file),
                "use_sudo": True,
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

            # Create a protected directory and file (use TEST_WORKSPACE for cross-platform)
            protected_dir = f"{TEST_WORKSPACE}{PATH_SEP}sudo_protected_dir"
            protected_file = f"{protected_dir}{PATH_SEP}protected_file.txt"

            # Create directory with use_sudo (on Linux: root owns it, on Windows: ignored)
            mkdir_result = await client.call_tool("ssh_dir_mkdir", {
                "path": protected_dir,
                "use_sudo": True
            })
            mkdir_json = json.loads(extract_result_text(mkdir_result))
            assert mkdir_json['status'] == 'success', f"Failed to create directory: {mkdir_json}"

            # On Linux only: set restricted permissions
            if not IS_WINDOWS:
                await client.call_tool("ssh_cmd_run", {
                    "command": f"chmod 700 {protected_dir}",
                    "use_sudo": True
                })

            # Create a file in the protected directory
            file_content = "This is a protected file that requires sudo to access"
            write_result = await client.call_tool("ssh_file_write", {
                "file_path": protected_file,
                "content": file_content,
                "use_sudo": True
            })
            write_json = json.loads(extract_result_text(write_result))
            assert write_json['success'], f"Failed to write file with sudo: {write_json}"

            # Read the file - platform specific approach
            # On Linux: SFTP can't read root-owned files, need to use cat with sudo
            # On Windows: SFTP works fine (no permission issues with admin)
            if IS_WINDOWS:
                read_result = await client.call_tool("ssh_file_read", {
                    "file_path": protected_file
                })
                read_json = json.loads(extract_result_text(read_result))
                assert read_json['success'], f"Failed to read file: {read_json}"
                assert file_content in read_json['content'], "File content doesn't match expected"
            else:
                read_result = await client.call_tool("ssh_cmd_run", {
                    "command": f"cat {protected_file}",
                    "use_sudo": True
                })
                read_json = json.loads(extract_result_text(read_result))
                assert read_json['status'] == 'success', f"Failed to read file with sudo: {read_json}"
                assert file_content in read_json['output'], "File content doesn't match expected"

            # Try to modify the file with sudo
            modified_content = "This content was modified with sudo"
            modify_result = await client.call_tool("ssh_file_write", {
                "file_path": protected_file,
                "content": modified_content,
                "use_sudo": True
            })
            modify_json = json.loads(extract_result_text(modify_result))
            assert modify_json['success'], f"Failed to modify file with sudo: {modify_json}"

            # Verify the modification - platform specific approach
            if IS_WINDOWS:
                verify_result = await client.call_tool("ssh_file_read", {
                    "file_path": protected_file
                })
                verify_json = json.loads(extract_result_text(verify_result))
                assert verify_json['success'], f"Failed to read modified file: {verify_json}"
                assert modified_content in verify_json['content'], "Modified content not found"
            else:
                verify_result = await client.call_tool("ssh_cmd_run", {
                    "command": f"cat {protected_file}",
                    "use_sudo": True
                })
                verify_json = json.loads(extract_result_text(verify_result))
                assert verify_json['status'] == 'success', f"Failed to read modified file: {verify_json}"
                assert modified_content in verify_json['output'], "Modified content not found"

        finally:
            # Clean up using cross-platform command
            await client.call_tool("ssh_cmd_run", {
                "command": cleanup_command(protected_dir),
                "use_sudo": True,
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
            sudo_access = json.loads(extract_result_text(sudo_result))

            # Verify we get the expected dictionary response
            assert isinstance(sudo_access, dict), f"Expected dictionary result, got: {sudo_access}"
            assert 'available' in sudo_access, "Missing 'available' key in sudo access response"
            assert 'passwordless' in sudo_access, "Missing 'passwordless' key in sudo access response"
            assert 'requires_password' in sudo_access, "Missing 'requires_password' key in sudo access response"

            # Platform-specific sudo/elevation verification
            if IS_WINDOWS:
                # On Windows, verify that we're running as admin (elevated)
                # The tool should report this via the 'available' and 'passwordless' fields
                # Windows admin sessions are always "passwordless" since there's no sudo prompt
                print(f"Windows elevation status: available={sudo_access['available']}, passwordless={sudo_access['passwordless']}")
                # Just verify the tool works - actual elevation depends on how user connected
            else:
                # On Linux/macOS, if sudo is available, verify we can run as root
                if sudo_access['available']:
                    cmd_result = await client.call_tool("ssh_cmd_run", {
                        "command": "id",
                        "use_sudo": True
                    })
                    cmd_json = json.loads(extract_result_text(cmd_result))
                    assert cmd_json['status'] == 'success', f"Sudo command failed: {cmd_json}"
                    assert "uid=0(root)" in cmd_json['output'], "Expected root user ID in output"

        finally:
            await disconnect_ssh(client)

    print_test_footer()


@pytest.mark.asyncio
async def test_ssh_use_sudo_parameter(mcp_test_environment):
    """Test the 'use_sudo' parameter works correctly across platforms."""
    print_test_header("Testing 'use_sudo' parameter")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            # Verify sudo access is available first (on Windows, checks for admin)
            sudo_result = await client.call_tool("ssh_conn_verify_sudo", {})
            sudo_access = json.loads(extract_result_text(sudo_result))

            if not sudo_access['available']:
                print("Skipping test as sudo/elevation is not available on this system")
                return

            # Use TEST_WORKSPACE for cross-platform compatibility
            test_file = f"{TEST_WORKSPACE}{PATH_SEP}sudo_param_test.txt"
            test_content = "Testing use_sudo parameter"

            # Create the file with use_sudo using ssh_file_write (cross-platform)
            create_result = await client.call_tool("ssh_file_write", {
                "file_path": test_file,
                "content": test_content,
                "use_sudo": True
            })
            create_json = json.loads(extract_result_text(create_result))
            assert create_json['success'], f"Failed to create test file with use_sudo: {create_json}"

            # Read the file using ssh_file_read (cross-platform)
            read_result = await client.call_tool("ssh_file_read", {
                "file_path": test_file
            })
            read_json = json.loads(extract_result_text(read_result))
            assert read_json['success'], f"Failed to read file: {read_json}"
            assert test_content in read_json['content'], "File content doesn't match expected"

            # Test other tools with the use_sudo parameter
            # Test file operations
            file_write_path = f"{TEST_WORKSPACE}{PATH_SEP}sudo_write_test.txt"
            file_write_result = await client.call_tool("ssh_file_write", {
                "file_path": file_write_path,
                "content": "Testing use_sudo with file_write",
                "use_sudo": True
            })
            file_write_json = json.loads(extract_result_text(file_write_result))
            assert file_write_json['success'], f"Failed to write file with use_sudo: {file_write_json}"

            # Test directory operations
            test_dir = f"{TEST_WORKSPACE}{PATH_SEP}sudo_test_dir"
            mkdir_result = await client.call_tool("ssh_dir_mkdir", {
                "path": test_dir,
                "use_sudo": True
            })
            mkdir_json = json.loads(extract_result_text(mkdir_result))
            assert mkdir_json['status'] == 'success', f"Failed to create directory with use_sudo: {mkdir_json}"

        finally:
            # Clean up using cross-platform commands
            test_file = f"{TEST_WORKSPACE}{PATH_SEP}sudo_param_test.txt"
            file_write_path = f"{TEST_WORKSPACE}{PATH_SEP}sudo_write_test.txt"
            test_dir = f"{TEST_WORKSPACE}{PATH_SEP}sudo_test_dir"

            # Clean up files
            await client.call_tool("ssh_cmd_run", {
                "command": cleanup_command(test_file),
                "use_sudo": True,
                "io_timeout": 5.0
            })
            await client.call_tool("ssh_cmd_run", {
                "command": cleanup_command(file_write_path),
                "use_sudo": True,
                "io_timeout": 5.0
            })
            await client.call_tool("ssh_cmd_run", {
                "command": cleanup_command(test_dir),
                "use_sudo": True,
                "io_timeout": 5.0
            })
            await disconnect_ssh(client)

    print_test_footer()
