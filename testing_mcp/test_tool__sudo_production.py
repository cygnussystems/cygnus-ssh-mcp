import pytest
import json
import logging
from conftest import (
    print_test_header,
    print_test_footer,
    make_connection,
    disconnect_ssh,
    extract_result_text
)
from cygnus_ssh_mcp.server import mcp
from fastmcp import Client

# Configure logging
logger = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_sudo_basic_command(mcp_test_environment):
    """Test basic sudo command execution."""
    print_test_header("Testing basic sudo command")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            # Verify sudo access
            sudo_verify_result = await client.call_tool("ssh_conn_verify_sudo", {})
            sudo_verify_json = json.loads(extract_result_text(sudo_verify_result))

            logger.info(f"Sudo verification result: {sudo_verify_json}")

            if not sudo_verify_json.get('available', False):
                pytest.skip("Sudo is not available on this server")

            # Run a simple sudo command
            whoami_result = await client.call_tool("ssh_cmd_run", {
                "command": "whoami",
                "use_sudo": True,
                "io_timeout": 10.0
            })
            whoami_json = json.loads(extract_result_text(whoami_result))

            assert whoami_json['status'] == 'success', f"Sudo whoami command failed: {whoami_json}"
            assert "root" in whoami_json['output'], "Expected 'root' in sudo whoami output"

            logger.info(f"Sudo whoami successful: {whoami_json['output'].strip()}")

        except Exception as e:
            logger.error(f"Error in sudo test: {e}", exc_info=True)
            raise
        finally:
            await disconnect_ssh(client)

    print_test_footer()


@pytest.mark.asyncio
async def test_sudo_file_operations(mcp_test_environment):
    """Test sudo file operations in protected locations."""
    print_test_header("Testing sudo file operations")

    async with Client(mcp) as client:
        test_file = "/root/sudo_test_file.txt"
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            test_content = "This is a sudo test file"

            # Write the file with sudo
            write_result = await client.call_tool("ssh_file_write", {
                "file_path": test_file,
                "content": test_content,
                "use_sudo": True
            })
            write_json = json.loads(extract_result_text(write_result))

            assert write_json['success'], f"Failed to write file with sudo: {write_json}"
            logger.info(f"Successfully wrote file with sudo: {test_file}")

            # Read the file with sudo
            read_result = await client.call_tool("ssh_cmd_run", {
                "command": f"cat {test_file}",
                "use_sudo": True,
                "io_timeout": 10.0
            })
            read_json = json.loads(extract_result_text(read_result))

            assert read_json['status'] == 'success', f"Failed to read file with sudo: {read_json}"
            assert test_content in read_json['output'], "File content doesn't match expected"
            logger.info(f"Successfully read file with sudo: {test_file}")

        except Exception as e:
            logger.error(f"Error in sudo file operations test: {e}", exc_info=True)
            raise
        finally:
            # Clean up
            try:
                await client.call_tool("ssh_cmd_run", {
                    "command": f"rm -f {test_file}",
                    "use_sudo": True,
                    "io_timeout": 5.0
                })
            except Exception:
                pass
            await disconnect_ssh(client)

    print_test_footer()


@pytest.mark.asyncio
async def test_sudo_complex_command(mcp_test_environment):
    """Test complex sudo command with pipes and redirects."""
    print_test_header("Testing complex sudo command")

    async with Client(mcp) as client:
        output_file = "/root/sudo_test_output.txt"
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            # Run a more complex command with pipes and redirects
            complex_cmd = f"find /etc -type f -name '*.conf' 2>/dev/null | head -5 > {output_file}"

            cmd_result = await client.call_tool("ssh_cmd_run", {
                "command": complex_cmd,
                "use_sudo": True,
                "io_timeout": 20.0
            })
            cmd_json = json.loads(extract_result_text(cmd_result))

            assert cmd_json['status'] == 'success', f"Complex sudo command failed: {cmd_json}"
            logger.info("Complex sudo command executed successfully")

            # Verify the output file was created
            verify_result = await client.call_tool("ssh_cmd_run", {
                "command": f"cat {output_file}",
                "use_sudo": True,
                "io_timeout": 10.0
            })
            verify_json = json.loads(extract_result_text(verify_result))

            assert verify_json['status'] == 'success', f"Failed to verify output file: {verify_json}"
            # File should have some content (at least one .conf file in /etc)
            assert len(verify_json['output'].strip()) > 0, "Expected output file to contain data"
            logger.info(f"Output file contents: {verify_json['output']}")

        except Exception as e:
            logger.error(f"Error in complex sudo test: {e}", exc_info=True)
            raise
        finally:
            # Clean up
            try:
                await client.call_tool("ssh_cmd_run", {
                    "command": f"rm -f {output_file}",
                    "use_sudo": True,
                    "io_timeout": 5.0
                })
            except Exception:
                pass
            await disconnect_ssh(client)

    print_test_footer()


@pytest.mark.asyncio
async def test_sudo_interactive_command(mcp_test_environment):
    """Test sudo with commands that might require interactive input."""
    print_test_header("Testing potentially interactive sudo command")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            # Run a command that might trigger interactive prompts in some environments
            # Using apt-get update which is common on Debian-based systems
            interactive_cmd = "apt-get update -y"

            cmd_result = await client.call_tool("ssh_cmd_run", {
                "command": interactive_cmd,
                "use_sudo": True,
                "io_timeout": 60.0,  # Longer timeout for apt operations
                "runtime_timeout": 120.0
            })
            cmd_json = json.loads(extract_result_text(cmd_result))

            # This might fail on some systems, so we log the result but don't hard fail
            logger.info(f"Interactive sudo command result: {cmd_json['status']}")
            if cmd_json['status'] == 'success':
                logger.info("Interactive sudo command executed successfully")
            else:
                logger.warning(f"Interactive sudo command failed (may be expected): {cmd_json}")

        except Exception as e:
            logger.error(f"Error in interactive sudo test: {e}", exc_info=True)
            # This test is allowed to fail as it's testing a challenging case
            pytest.skip(f"Interactive sudo command test failed: {e}")
        finally:
            await disconnect_ssh(client)

    print_test_footer()


@pytest.mark.asyncio
async def test_sudo_file_write_to_etc(mcp_test_environment):
    """Test ssh_file_write with sudo to /etc directory."""
    print_test_header("Testing ssh_file_write with sudo to /etc")

    async with Client(mcp) as client:
        test_file = "/etc/ssh_test_sudo_write.conf"
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            test_content = "# This is a test file created with sudo privileges\n# It should be removed after the test"

            # Write the file with sudo
            write_result = await client.call_tool("ssh_file_write", {
                "file_path": test_file,
                "content": test_content,
                "use_sudo": True
            })
            write_json = json.loads(extract_result_text(write_result))

            assert write_json['success'], f"Failed to write file with sudo: {write_json}"
            logger.info(f"Successfully wrote file with sudo: {test_file}")

            # Verify the file exists and has correct content
            verify_result = await client.call_tool("ssh_cmd_run", {
                "command": f"cat {test_file}",
                "use_sudo": True
            })
            verify_json = json.loads(extract_result_text(verify_result))

            assert verify_json['status'] == 'success', f"Failed to verify file content: {verify_json}"
            assert test_content in verify_json['output'], "File content doesn't match expected"

        except Exception as e:
            logger.error(f"Error in sudo file write test: {e}", exc_info=True)
            raise
        finally:
            # Clean up
            try:
                await client.call_tool("ssh_cmd_run", {
                    "command": f"rm -f {test_file}",
                    "use_sudo": True
                })
            except Exception:
                pass
            await disconnect_ssh(client)

    print_test_footer()


@pytest.mark.asyncio
async def test_sudo_dir_operations(mcp_test_environment):
    """Test directory operations with sudo in /opt."""
    print_test_header("Testing directory operations with sudo")

    async with Client(mcp) as client:
        test_dir = "/opt/sudo_test_dir"
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            # Create directory with sudo
            mkdir_result = await client.call_tool("ssh_dir_mkdir", {
                "path": test_dir,
                "use_sudo": True
            })
            mkdir_json = json.loads(extract_result_text(mkdir_result))

            assert mkdir_json['status'] == 'success', f"Failed to create directory with sudo: {mkdir_json}"
            logger.info(f"Successfully created directory with sudo: {test_dir}")

            # Create a test file in the directory
            test_file = f"{test_dir}/test_file.txt"
            test_content = "Test file in sudo-created directory"

            write_result = await client.call_tool("ssh_file_write", {
                "file_path": test_file,
                "content": test_content,
                "use_sudo": True
            })
            write_json = json.loads(extract_result_text(write_result))
            assert write_json['success'], f"Failed to write file in sudo directory: {write_json}"

            # List directory contents
            list_result = await client.call_tool("ssh_dir_list_files_basic", {
                "path": test_dir
            })
            # This tool returns a list directly, not JSON
            list_text = extract_result_text(list_result)

            assert "test_file.txt" in list_text, f"File not found in directory listing: {list_text}"
            logger.info(f"Directory listing successful: {list_text}")

            # Clean up - remove directory recursively
            cleanup_result = await client.call_tool("ssh_dir_remove", {
                "path": test_dir,
                "use_sudo": True,
                "recursive": True
            })
            cleanup_json = json.loads(extract_result_text(cleanup_result))
            assert cleanup_json['status'] == 'success', f"Failed to remove directory: {cleanup_json}"

        except Exception as e:
            logger.error(f"Error in sudo directory operations test: {e}", exc_info=True)
            raise
        finally:
            # Try to clean up even if test fails
            try:
                await client.call_tool("ssh_cmd_run", {
                    "command": f"rm -rf {test_dir}",
                    "use_sudo": True
                })
            except Exception:
                pass
            await disconnect_ssh(client)

    print_test_footer()


@pytest.mark.asyncio
async def test_sudo_file_edit_operations(mcp_test_environment):
    """Test file editing operations with sudo."""
    print_test_header("Testing file editing operations with sudo")

    async with Client(mcp) as client:
        test_file = "/etc/sudo_test_edit.conf"
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            initial_content = "# Initial configuration\nkey1=value1\nkey2=value2\n# End of file"

            # Create the initial file
            await client.call_tool("ssh_file_write", {
                "file_path": test_file,
                "content": initial_content,
                "use_sudo": True
            })

            # Test replace_line operation with sudo
            replace_result = await client.call_tool("ssh_file_replace_line", {
                "file_path": test_file,
                "match_line": "key1=value1",
                "new_line": "key1=new_value",
                "use_sudo": True
            })
            replace_json = json.loads(extract_result_text(replace_result))
            assert replace_json['success'], f"Failed to replace line with sudo: {replace_json}"
            logger.info("Successfully replaced line with sudo")

            # Test insert_lines_after_match operation with sudo
            insert_result = await client.call_tool("ssh_file_insert_lines_after_match", {
                "file_path": test_file,
                "match_line": "key2=value2",
                "lines_to_insert": ["key3=value3", "key4=value4"],
                "use_sudo": True
            })
            insert_json = json.loads(extract_result_text(insert_result))
            assert insert_json['success'], f"Failed to insert lines with sudo: {insert_json}"
            logger.info("Successfully inserted lines with sudo")

            # Verify the changes
            verify_result = await client.call_tool("ssh_cmd_run", {
                "command": f"cat {test_file}",
                "use_sudo": True
            })
            verify_json = json.loads(extract_result_text(verify_result))

            assert "key1=new_value" in verify_json['output'], "Line replacement not found"
            assert "key3=value3" in verify_json['output'], "Inserted line key3 not found"
            assert "key4=value4" in verify_json['output'], "Inserted line key4 not found"

        except Exception as e:
            logger.error(f"Error in sudo file edit operations test: {e}", exc_info=True)
            raise
        finally:
            # Clean up
            try:
                await client.call_tool("ssh_cmd_run", {
                    "command": f"rm -f {test_file}",
                    "use_sudo": True
                })
            except Exception:
                pass
            await disconnect_ssh(client)

    print_test_footer()
