import pytest
import json
import logging
from conftest import print_test_header, print_test_footer

# Import necessary modules
from mcp_ssh_server import mcp
from fastmcp import Client

# First, add the test server configuration
from conftest import SSH_TEST_USER, SSH_TEST_PASSWORD, SSH_TEST_PORT

# Configure logging
logger = logging.getLogger(__name__)

@pytest.mark.asyncio
async def test_ssh_verify_sudo():
    """Test verifying sudo access."""
    print_test_header("Testing 'ssh_verify_sudo' tool")
    logger.info("Starting SSH sudo verification test")

    # Use the Client context manager with the imported mcp instance
    async with Client(mcp) as client:

        try:
            # Ensure we have a connection
            try:
                await client.call_tool("ssh_status", {})
                logger.info("SSH connection already established")
            except Exception as e:
                if "No active SSH connection" in str(e):
                    # Add the test server configuration
                    logger.info("Adding test server configuration")
                    await client.call_tool("ssh_add_host", {
                        "name": "test_server",
                        "host": "localhost",
                        "user": SSH_TEST_USER,
                        "password": SSH_TEST_PASSWORD,
                        "port": SSH_TEST_PORT
                    })
                    
                    # Connect to the test server
                    logger.info("Connecting to test server")
                    await client.call_tool("ssh_connect", {
                        "host_name": "test_server"
                    })
                else:
                    raise
            
            # Test verify_sudo
            logger.info("Verifying sudo access")
            sudo_result = await client.call_tool("ssh_verify_sudo", {})
            logger.info(f"verify_sudo result: {sudo_result}")
            
            # Verify the result
            assert sudo_result is not None, "Expected non-empty result"
            sudo_json = json.loads(sudo_result[0].text)
            
            # The result should be a boolean
            assert isinstance(sudo_json, bool), f"Expected boolean result, got {type(sudo_json)}"
            
            # Note: We don't assert the actual value (True/False) since it depends on the test environment
            logger.info(f"Sudo access available: {sudo_json}")
            
            logger.info("SSH sudo verification test completed successfully")
        except Exception as e:
            logger.error(f"Error in SSH sudo verification test: {e}")
            raise
    
    print_test_footer()






@pytest.mark.asyncio
async def test_ssh_system_operations():
    """Test various system operations."""
    print_test_header("Testing system operations")
    logger.info("Starting SSH system operations test")

    # Use the Client context manager with the imported mcp instance
    async with Client(mcp) as client:
        try:
            # Ensure we have a connection
            try:
                await client.call_tool("ssh_status", {})
                logger.info("SSH connection already established")
            except Exception as e:
                if "No active SSH connection" in str(e):
                    # Add the test server configuration
                    logger.info("Adding test server configuration")
                    await client.call_tool("ssh_add_host", {
                        "name": "test_server",
                        "host": "localhost",
                        "user": SSH_TEST_USER,
                        "password": SSH_TEST_PASSWORD,
                        "port": SSH_TEST_PORT
                    })
                    
                    # Connect to the test server
                    logger.info("Connecting to test server")
                    await client.call_tool("ssh_connect", {
                        "host_name": "test_server"
                    })
                else:
                    raise
            
            # Test running a command with sudo
            # This might fail if sudo access is not available, so we'll handle the exception
            try:
                logger.info("Running a command with sudo")
                sudo_run_params = {
                    "command": "id",
                    "sudo": True,
                    "io_timeout": 5.0
                }
                
                sudo_run_result = await client.call_tool("ssh_run", sudo_run_params)
                sudo_run_json = json.loads(sudo_run_result[0].text)
                logger.info(f"Sudo command result: {sudo_run_json}")
                
                # If sudo works, the output should contain "uid=0(root)"
                if "uid=0(root)" in sudo_run_json['output']:
                    logger.info("Sudo command executed successfully as root")
                else:
                    logger.warning("Sudo command did not run as root")
            except Exception as e:
                logger.warning(f"Sudo command failed (this may be expected): {e}")
            
            # Test getting system status
            logger.info("Getting system status")
            status_result = await client.call_tool("ssh_status", {})
            status_json = json.loads(status_result[0].text)
            
            # Verify system information
            system_info = status_json['system']
            assert 'os_type' in system_info, "System info should include OS type"
            assert 'hostname' in system_info, "System info should include hostname"
            assert 'cpu_count' in system_info, "System info should include CPU count"
            assert 'mem_total_mb' in system_info, "System info should include memory info"
            
            logger.info(f"System information: OS={system_info['os_type']}, " +
                       f"Hostname={system_info['hostname']}, " +
                       f"CPUs={system_info['cpu_count']}, " +
                       f"Memory={system_info['mem_total_mb']}MB")
            
            logger.info("SSH system operations test completed successfully")
        except Exception as e:
            logger.error(f"Error in SSH system operations test: {e}")
            raise
    
    print_test_footer()
