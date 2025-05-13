import pytest
import json
import logging
from conftest import print_test_header, print_test_footer

# Import necessary modules and constants from conftest
from mcp_ssh_server import mcp
from fastmcp import Client
from conftest import (
    SSH_TEST_USER, 
    SSH_TEST_HOST, 
    is_ssh_connected, 
    make_connection, 
    disconnect_ssh
)

# Configure logging
logger = logging.getLogger(__name__)

@pytest.mark.asyncio
async def test_ssh_status():
    """Test retrieving SSH connection status."""
    print_test_header("Testing 'ssh_conn_status' tool")
    logger.info("Starting SSH status test")

    # Use the Client context manager with the imported mcp instance
    async with Client(mcp) as client:
        try:
            # Ensure no connection exists at start
            await disconnect_ssh(client) # Ensure clean state
            assert not await is_ssh_connected(client), "Test started with an existing SSH connection"
            logger.info("Verified no existing SSH connection")

            # Establish connection using the helper from conftest
            assert await make_connection(client), "Failed to establish SSH connection"
            logger.info("Verified SSH connection is active via make_connection")
            
            # Now get the status
            status_result = await client.call_tool("ssh_conn_status", {})
            
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
            connection_info = result_json['connection']
            assert 'host' in connection_info, "Expected 'host' in connection info"
            assert 'user' in connection_info, "Expected 'user' in connection info"
            assert 'os_type' in connection_info, "Expected 'os_type' in connection info"
            
            # Verify the host and user match what we expect from conftest defaults
            assert connection_info['host'] == SSH_TEST_HOST, f"Expected host to be '{SSH_TEST_HOST}'"
            assert connection_info['user'] == SSH_TEST_USER, f"Expected user to be '{SSH_TEST_USER}'"
            
            # Verify system information is present
            system_info = result_json['system']
            assert isinstance(system_info, dict), "Expected system info to be a dictionary"
            
            logger.info("SSH status test completed successfully")
        except Exception as e:
            logger.error(f"Error in SSH status test: {e}", exc_info=True)
            raise
        finally:
            # Clean up the connection after the test
            await disconnect_ssh(client)
    
    print_test_footer()



@pytest.mark.asyncio
async def test_ssh_reconnect():
    """Test reconnecting to SSH when a connection already exists."""
    print_test_header("Testing SSH reconnection")
    logger.info("Starting SSH reconnection test")

    # Construct the user@hostname key that make_connection will use
    host_key_for_connection = f"{SSH_TEST_USER}@{SSH_TEST_HOST}"

    # Use the Client context manager with the imported mcp instance
    async with Client(mcp) as client:
        try:
            # Ensure no connection exists at start
            await disconnect_ssh(client) # Ensure clean state
            assert not await is_ssh_connected(client), "Test started with an existing SSH connection"
            logger.info("Verified no existing SSH connection")
            
            # Establish first connection using the helper
            # make_connection will add the host config for host_key_for_connection
            assert await make_connection(client), "Failed to establish initial SSH connection"
            logger.info(f"Established initial SSH connection to {host_key_for_connection}")
            
            # Get status of first connection
            first_status_result = await client.call_tool("ssh_conn_status", {})
            first_status_json = json.loads(first_status_result[0].text)
            logger.info(f"First connection status: {first_status_json}")
            
            # Now attempt to reconnect to the same host using its user@hostname key
            logger.info(f"Attempting to reconnect to {host_key_for_connection} while existing connection is active")
            reconnect_params = {
                "host_name": host_key_for_connection
            }
            reconnect_result = await client.call_tool("ssh_conn_connect", reconnect_params)
            reconnect_json = json.loads(reconnect_result[0].text)
            
            # Verify reconnection was successful
            assert reconnect_json.get('status') == 'success', f"Reconnection should succeed, got: {reconnect_json}"
            assert reconnect_json.get('connected_to') == host_key_for_connection, \
                f"Reconnection should be to '{host_key_for_connection}', got: {reconnect_json.get('connected_to')}"
            logger.info(f"Reconnection to {host_key_for_connection} successful")
            
            # Verify we still have an active connection
            assert await is_ssh_connected(client), "Should have active connection after reconnect"
            
            # Get status after reconnection
            second_status_result = await client.call_tool("ssh_conn_status", {})
            second_status_json = json.loads(second_status_result[0].text)
            logger.info(f"Second connection status: {second_status_json}")
            
            # The connection details (host, user, port) should be the same
            assert second_status_json['connection']['host'] == SSH_TEST_HOST
            assert second_status_json['connection']['user'] == SSH_TEST_USER
            
            # Timestamps should differ as a new SshClient instance is created upon reconnection
            assert 'timestamp' in first_status_json['connection'], "Timestamp missing in first status"
            assert 'timestamp' in second_status_json['connection'], "Timestamp missing in second status"
            assert first_status_json['connection']['timestamp'] != second_status_json['connection']['timestamp'], \
                "Connection timestamps should differ after reconnection, indicating a new connection object"
                
            logger.info("SSH reconnection test completed successfully")
        except Exception as e:
            logger.error(f"Error in SSH reconnection test: {e}", exc_info=True)
            raise
        finally:
            # Clean up the connection after the test
            await disconnect_ssh(client)
    
    print_test_footer()


@pytest.mark.asyncio
async def test_list_tools():
    """Test the 'list_tools' tool to ensure it returns available tools."""
    print_test_header("Testing 'list_tools' tool")
    logger.info("Starting list_tools test")

    async with Client(mcp) as client:
        try:
            # Call the list_tools tool
            result = await client.call_tool("list_tools", {})
            logger.info(f"Raw result from list_tools: {result}")

            # Verify the result structure from client.call_tool
            assert result is not None, "Expected non-empty result from call_tool"
            assert isinstance(result, list), f"Expected list result from call_tool, got {type(result)}"
            assert len(result) > 0, "Expected non-empty list from call_tool"
            assert hasattr(result[0], 'text'), "Expected TextContent object with 'text' attribute"

            # Parse the JSON response
            tools_list = json.loads(result[0].text)
            logger.info(f"Parsed tools list: {tools_list}")

            # Verify the parsed list
            assert isinstance(tools_list, list), f"Expected parsed data to be a list, got {type(tools_list)}"
            assert len(tools_list) > 0, "Expected a non-empty list of tools"

            # Verify the structure of each item in the list
            known_tool_names = []
            for tool_info in tools_list:
                assert isinstance(tool_info, dict), f"Expected tool_info to be a dict, got {type(tool_info)}"
                assert 'name' in tool_info, "Expected 'name' key in tool_info"
                assert isinstance(tool_info['name'], str), "Expected 'name' to be a string"
                assert 'description' in tool_info, "Expected 'description' key in tool_info"
                assert isinstance(tool_info['description'], str), "Expected 'description' to be a string"
                known_tool_names.append(tool_info['name'])

            # Verify that some essential tools are listed
            assert "list_tools" in known_tool_names, "'list_tools' itself should be in the list"
            assert "ssh_conn_status" in known_tool_names, "'ssh_conn_status' should be in the list"
            assert "ssh_cmd_run" in known_tool_names, "'ssh_cmd_run' should be in the list"
            
            logger.info(f"Found {len(tools_list)} tools. Verified presence of essential tools.")
            logger.info("list_tools test completed successfully")

        except Exception as e:
            logger.error(f"Error in list_tools test: {e}", exc_info=True)
            raise
        # No specific cleanup like disconnect_ssh is needed as this tool doesn't manage connections
    
    print_test_footer()
