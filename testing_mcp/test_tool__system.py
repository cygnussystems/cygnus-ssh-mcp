import pytest
import json
import logging
from conftest import print_test_header, print_test_footer, make_connection, disconnect_ssh, mcp_test_environment, extract_result_text
# Import necessary modules
from mcp_ssh_server import mcp
from fastmcp import Client

# Configure logging
logger = logging.getLogger(__name__)

@pytest.mark.asyncio
async def test_ssh_verify_sudo(mcp_test_environment):
    """Test verifying sudo access."""
    print_test_header("Testing 'ssh_conn_verify_sudo' tool")
    logger.info("Starting SSH sudo verification test")

    async with Client(mcp) as client:
        try:
            # Ensure connection is established
            assert await make_connection(client), "Failed to establish SSH connection"
            logger.info("SSH connection established or verified for sudo verification test")
            
            # Test verify_sudo
            logger.info("Verifying sudo access")
            sudo_result = await client.call_tool("ssh_conn_verify_sudo", {})
            logger.info(f"verify_sudo result: {sudo_result}")
            
            # Verify the result
            assert sudo_result is not None, "Expected non-empty result"
            result_text = extract_result_text(sudo_result)
            assert result_text, "Expected result with text content"

            sudo_json = json.loads(result_text)
            
            # The result should be a dictionary with specific keys
            assert isinstance(sudo_json, dict), f"Expected dictionary result, got {type(sudo_json)}"
            assert "available" in sudo_json, "Expected 'available' key in result"
            assert isinstance(sudo_json["available"], bool), "Expected 'available' to be a boolean"
                
            # Note: We don't assert the actual value (True/False) since it depends on the test environment's setup
            # The test container is configured with passwordless sudo for the test user.
            assert sudo_json["available"] is True, "Expected sudo access to be available in the test environment"
            logger.info(f"Sudo access available: {sudo_json['available']}")
            
            logger.info("SSH sudo verification test completed successfully")
        except Exception as e:
            logger.error(f"Error in SSH sudo verification test: {e}")
            raise
        finally:
            await disconnect_ssh(client)
            logger.info("SSH connection for sudo verification test cleaned up")
    
    print_test_footer()

@pytest.mark.asyncio
async def test_ssh_system_operations(mcp_test_environment):
    """Test various system operations."""
    print_test_header("Testing system operations")
    logger.info("Starting SSH system operations test")

    async with Client(mcp) as client:
        try:
            # Ensure connection is established
            assert await make_connection(client), "Failed to establish SSH connection"
            logger.info("SSH connection established or verified for system operations test")
            
            # Test running a command with sudo
            # This should work as the test container has passwordless sudo for the test user.
            try:
                logger.info("Running a command with sudo")
                sudo_run_params = {
                    "command": "id",
                    "use_sudo": True,
                    "io_timeout": 5.0
                }
                
                sudo_run_result = await client.call_tool("ssh_cmd_run", sudo_run_params)
                result_text = extract_result_text(sudo_run_result)
                assert result_text, "Expected result with text content"
                sudo_run_json = json.loads(result_text)
                logger.info(f"Sudo command result: {sudo_run_json}")
                
                # If sudo works, the output should contain "uid=0(root)"
                assert "uid=0(root)" in sudo_run_json['output'], "Sudo command did not run as root or output is unexpected"
                logger.info("Sudo command executed successfully as root")
            except Exception as e:
                logger.error(f"Sudo command failed unexpectedly: {e}")
                raise # Re-raise if sudo command fails, as it's expected to work
            
            # Test getting system status - first get basic status
            logger.info("Getting basic system status")
            status_result = await client.call_tool("ssh_conn_status", {})
            result_text = extract_result_text(status_result)
            assert result_text, "Expected result with text content"
            status_json = json.loads(result_text)
            
            # Verify basic system information
            assert 'os_type' in status_json, "Status should include OS type"
            assert status_json['os_type'] == 'linux', f"Expected OS type 'linux', got '{status_json['os_type']}'"
            
            # Now get detailed host info
            logger.info("Getting detailed host info")
            host_info_result = await client.call_tool("ssh_conn_host_info", {})
            result_text = extract_result_text(host_info_result)
            assert result_text, "Expected result with text content"
            host_info_json = json.loads(result_text)
            
            # Verify detailed system information
            assert 'connection' in host_info_json, "Expected 'connection' key in host info result"
            assert 'system' in host_info_json, "Expected 'system' key in host info result"
            
            system_info = host_info_json['system']
            assert 'os_type' in system_info, "System info should include OS type"
            assert system_info['os_type'] == 'linux', f"Expected OS type 'linux', got '{system_info['os_type']}'"
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
        finally:
            await disconnect_ssh(client)
            logger.info("SSH connection for system operations test cleaned up")
    
    print_test_footer()
