import pytest
import json
import logging
from conftest import print_test_header, print_test_footer

# Import necessary modules
from mcp_ssh_server import mcp
from fastmcp import Client
from conftest import SSH_TEST_CONFIG

# Configure logging
logger = logging.getLogger(__name__)

@pytest.mark.asyncio
async def test_ssh_status():
    """Test retrieving SSH connection status."""
    print_test_header("Testing 'ssh_status' tool")
    logger.info("Starting SSH status test")

    # Use the Client context manager with the imported mcp instance
    async with Client(mcp) as client:
        try:
            # Ensure no connection exists at start
            is_connected_result = await client.call_tool("ssh_is_connected", {})
            is_connected_json = json.loads(is_connected_result[0].text)
            assert not is_connected_json, "Test started with an existing SSH connection"
            logger.info("Verified no existing SSH connection")

            # Add the test server configuration
            logger.info("Adding test server configuration")
            await client.call_tool("ssh_add_host", SSH_TEST_CONFIG)
            
            # Connect to the test server
            logger.info("Connecting to test server")
            connect_result = await client.call_tool("ssh_connect", {
                "host_name": "test_server"
            })
            connect_json = json.loads(connect_result[0].text)
            assert connect_json['status'] == 'success', "Connection should be successful"
            
            # Verify connection is now active
            is_connected_result = await client.call_tool("ssh_is_connected", {})
            is_connected_json = json.loads(is_connected_result[0].text)
            assert is_connected_json, "SSH connection should now be active"
            logger.info("Verified SSH connection is active")

            # Now get the status
            status_result = await client.call_tool("ssh_status", {})
            
            # Verify the result
            assert status_result is not None, "Expected non-empty result"
            assert isinstance(status_result, list), f"Expected list result, got {type(status_result)}"
            assert len(status_result) > 0, "Expected non-empty list result"
            assert hasattr(status_result[0], 'text'), "Expected TextContent object with 'text' attribute"
            
            # Parse the JSON response
            result_json = json.loads(status_result[0].text)
            logger.info(f"Status result: {result_json}")
            
            # Verify the structure of the result
            assert 'connection' in result_json, "Expected 'connection' key in result"
            assert 'system' in result_json, "Expected 'system' key in result"
            
            # Verify connection details
            connection = result_json['connection']
            # Check for essential connection fields instead of 'connected' flag
            assert 'host' in connection, "Expected 'host' in connection info"
            assert 'user' in connection, "Expected 'user' in connection info"
            assert 'os_type' in connection, "Expected 'os_type' in connection info"
            # Verify the host matches what we expect
            assert connection['host'] == 'localhost', "Expected host to be 'localhost'"
            
            # Verify system information is present
            system = result_json['system']
            assert isinstance(system, dict), "Expected system info to be a dictionary"
            
            logger.info("SSH status test completed successfully")
        except Exception as e:
            logger.error(f"Error in SSH status test: {e}")
            raise
    
    print_test_footer()
