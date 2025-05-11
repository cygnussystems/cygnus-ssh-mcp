import pytest
import json
import logging
from conftest import print_test_header, print_test_footer

# Import necessary modules
from mcp_ssh_server import mcp
from fastmcp import Client
from conftest import SSH_TEST_CONFIG, is_ssh_connected, ensure_ssh_connection

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
            assert not await is_ssh_connected(client), "Test started with an existing SSH connection"
            logger.info("Verified no existing SSH connection")

            # Establish connection
            assert await ensure_ssh_connection(client), "Failed to establish SSH connection"
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

@pytest.mark.asyncio
async def test_ssh_reconnect():
    """Test reconnecting to SSH when a connection already exists."""
    print_test_header("Testing SSH reconnection")
    logger.info("Starting SSH reconnection test")

    # Use the Client context manager with the imported mcp instance
    async with Client(mcp) as client:
        try:
            # Ensure no connection exists at start
            assert not await is_ssh_connected(client), "Test started with an existing SSH connection"
            logger.info("Verified no existing SSH connection")

            # Establish first connection
            assert await ensure_ssh_connection(client), "Failed to establish initial SSH connection"
            logger.info("Verified first SSH connection is active")
            
            # Get status of first connection
            first_status = await client.call_tool("ssh_status", {})
            first_status_json = json.loads(first_status[0].text)
            logger.info(f"First connection status: {first_status_json}")
            
            # Now reconnect to the same host (in a real-world scenario, this could be a different host)
            logger.info("Attempting to reconnect while existing connection is active")
            reconnect_result = await client.call_tool("ssh_connect", {
                "host_name": "test_server"
            })
            reconnect_json = json.loads(reconnect_result[0].text)
            
            # Verify reconnection was successful
            assert reconnect_json['status'] == 'success', "Reconnection should succeed"
            logger.info("Reconnection successful")
            
            # Verify we still have an active connection
            assert await is_ssh_connected(client), "Should have active connection after reconnect"
            
            # Get status after reconnection
            second_status = await client.call_tool("ssh_status", {})
            second_status_json = json.loads(second_status[0].text)
            logger.info(f"Second connection status: {second_status_json}")
            
            # Even though we're connecting to the same host, the connection object should be different
            # We can verify this indirectly by checking timestamps are different
            assert first_status_json['connection']['timestamp'] != second_status_json['connection']['timestamp'], \
                "Connection timestamps should differ after reconnection"
                
            logger.info("SSH reconnection test completed successfully")
        except Exception as e:
            logger.error(f"Error in SSH reconnection test: {e}")
            raise
    
    print_test_footer()
