import pytest
import json
import logging
from conftest import print_test_header, print_test_footer, IS_WINDOWS

# Import necessary modules and constants from conftest
from cygnus_ssh_mcp.server import mcp
from fastmcp import Client
from conftest import (
    SSH_TEST_USER,
    SSH_TEST_HOST,
    is_ssh_connected,
    make_connection,
    disconnect_ssh,
    extract_result_text
)

# Configure logging
logger = logging.getLogger(__name__)


def user_matches(actual_user: str, expected_user: str) -> bool:
    """
    Check if the actual username matches the expected username.
    On Windows, the actual user may include a domain prefix (e.g., "DOMAIN\\user" or "COMPUTER\\user").
    This helper allows for both exact match and domain-prefixed match.
    """
    if actual_user == expected_user:
        return True
    # Windows may return "DOMAIN\user" format
    if IS_WINDOWS and actual_user.endswith(f"\\{expected_user}"):
        return True
    # Also check for forward slash variant
    if IS_WINDOWS and actual_user.endswith(f"/{expected_user}"):
        return True
    return False


@pytest.mark.asyncio
async def test_ssh_status():
    """Test retrieving SSH connection status."""
    print_test_header("Testing 'ssh_conn_status' tool")
    logger.info("Starting SSH status test")

    # Use the Client context manager with the imported mcp instance
    async with Client(mcp) as client:
        try:
            # Ensure no connection exists at start
            await disconnect_ssh(client)  # Ensure clean state
            assert not await is_ssh_connected(client), "Test started with an existing SSH connection"
            logger.info("Verified no existing SSH connection")

            # Establish connection using the helper from conftest
            assert await make_connection(client), "Failed to establish SSH connection"
            logger.info("Verified SSH connection is active via make_connection")

            # Now get the status
            status_result = await client.call_tool("ssh_conn_status", {})

            # Verify the result
            assert status_result is not None, "Expected non-empty result"
            result_text = extract_result_text(status_result)
            assert result_text, "Expected result with text content"

            # Parse the JSON response
            result_json = json.loads(result_text)
            logger.info(f"Status result: {result_json}")

            # Verify the structure of the result - now using the simplified status format
            assert 'user' in result_json, "Expected 'user' key in result"
            assert 'host' in result_json, "Expected 'host' key in result"
            assert 'os_type' in result_json, "Expected 'os_type' key in result"
            assert 'current_directory' in result_json, "Expected 'current_directory' key in result"
            assert 'connected' in result_json, "Expected 'connected' key in result"

            # Verify the host matches and user matches (allowing for Windows domain prefix)
            assert result_json['host'] == SSH_TEST_HOST, f"Expected host to be '{SSH_TEST_HOST}'"
            assert user_matches(result_json['user'], SSH_TEST_USER), \
                f"Expected user to be '{SSH_TEST_USER}' (or with domain prefix), got '{result_json['user']}'"
            assert result_json['connected'] is True, "Expected connected to be True"

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
            await disconnect_ssh(client)  # Ensure clean state
            assert not await is_ssh_connected(client), "Test started with an existing SSH connection"
            logger.info("Verified no existing SSH connection")

            # Establish first connection using the helper
            assert await make_connection(client), "Failed to establish initial SSH connection"
            logger.info(f"Established initial SSH connection to {host_key_for_connection}")

            # Get status of first connection
            first_status_result = await client.call_tool("ssh_conn_status", {})
            first_status_json = json.loads(extract_result_text(first_status_result))
            logger.info(f"First connection status: {first_status_json}")

            # Now attempt to reconnect to the same host using its user@hostname key
            logger.info(f"Attempting to reconnect to {host_key_for_connection} while existing connection is active")
            reconnect_params = {
                "host_name": host_key_for_connection
            }
            reconnect_result = await client.call_tool("ssh_conn_connect", reconnect_params)
            reconnect_json = json.loads(extract_result_text(reconnect_result))

            # Verify reconnection was successful
            assert reconnect_json.get('status') == 'success', f"Reconnection should succeed, got: {reconnect_json}"
            assert reconnect_json.get('connected_to') == host_key_for_connection, \
                f"Reconnection should be to '{host_key_for_connection}', got: {reconnect_json.get('connected_to')}"
            logger.info(f"Reconnection to {host_key_for_connection} successful")

            # Verify we still have an active connection
            assert await is_ssh_connected(client), "Should have active connection after reconnect"

            # Get status after reconnection
            second_status_result = await client.call_tool("ssh_conn_status", {})
            second_status_json = json.loads(extract_result_text(second_status_result))
            logger.info(f"Second connection status: {second_status_json}")

            # The connection details (host, user) should be the same
            assert second_status_json['host'] == SSH_TEST_HOST
            assert user_matches(second_status_json['user'], SSH_TEST_USER), \
                f"Expected user '{SSH_TEST_USER}', got '{second_status_json['user']}'"

            # Both connections should show as connected
            assert first_status_json['connected'] is True, "First connection should be connected"
            assert second_status_json['connected'] is True, "Second connection should be connected"

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
            result_text = extract_result_text(result)
            assert result_text, "Expected result with text content"

            # Parse the JSON response
            tools_list = json.loads(result_text)
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


@pytest.mark.asyncio
async def test_ssh_conn_host_info():
    """Test retrieving detailed SSH host information."""
    print_test_header("Testing 'ssh_conn_host_info' tool")
    logger.info("Starting SSH host info test")

    # Use the Client context manager with the imported mcp instance
    async with Client(mcp) as client:
        try:
            # Ensure no connection exists at start
            await disconnect_ssh(client)  # Ensure clean state
            assert not await is_ssh_connected(client), "Test started with an existing SSH connection"
            logger.info("Verified no existing SSH connection")

            # Establish connection using the helper from conftest
            assert await make_connection(client), "Failed to establish SSH connection"
            logger.info("Verified SSH connection is active via make_connection")

            # Now get the detailed host info
            host_info_result = await client.call_tool("ssh_conn_host_info", {})

            # Verify the result
            assert host_info_result is not None, "Expected non-empty result"
            result_text = extract_result_text(host_info_result)
            assert result_text, "Expected result with text content"

            # Parse the JSON response
            result_json = json.loads(result_text)
            logger.info(f"Host info result: {result_json}")

            # Verify the structure of the result
            assert 'connection' in result_json, "Expected 'connection' key in result"
            assert 'system' in result_json, "Expected 'system' key in result"

            # Verify connection details
            connection_info = result_json['connection']
            assert 'host' in connection_info, "Expected 'host' in connection info"
            assert 'user' in connection_info, "Expected 'user' in connection info"
            assert 'os_type' in connection_info, "Expected 'os_type' in connection info"

            # Verify the host and user match (allowing for Windows domain prefix)
            assert connection_info['host'] == SSH_TEST_HOST, f"Expected host to be '{SSH_TEST_HOST}'"
            assert user_matches(connection_info['user'], SSH_TEST_USER), \
                f"Expected user to be '{SSH_TEST_USER}', got '{connection_info['user']}'"

            # Verify system information is present
            system_info = result_json['system']
            assert isinstance(system_info, dict), "Expected system info to be a dictionary"
            assert 'os_type' in system_info, "System info should include OS type"
            assert 'hostname' in system_info, "System info should include hostname"
            assert 'cpu_count' in system_info, "System info should include CPU count"
            assert 'mem_total_mb' in system_info, "System info should include memory info"

            logger.info("SSH host info test completed successfully")
        except Exception as e:
            logger.error(f"Error in SSH host info test: {e}", exc_info=True)
            raise
        finally:
            # Clean up the connection after the test
            await disconnect_ssh(client)

    print_test_footer()


@pytest.mark.asyncio
async def test_ssh_host_disconnect():
    """Test the 'ssh_host_disconnect' tool to ensure it properly disconnects an active SSH connection."""
    print_test_header("Testing 'ssh_host_disconnect' tool")
    logger.info("Starting ssh_host_disconnect test")

    # Use the Client context manager with the imported mcp instance
    async with Client(mcp) as client:
        try:
            # Ensure no connection exists at start
            await disconnect_ssh(client)  # Ensure clean state
            assert not await is_ssh_connected(client), "Test started with an existing SSH connection"
            logger.info("Verified no existing SSH connection")

            # Test disconnecting when no connection exists
            no_conn_result = await client.call_tool("ssh_host_disconnect", {})
            no_conn_json = json.loads(extract_result_text(no_conn_result))

            # Verify the result when no connection exists
            assert no_conn_json['status'] == 'success', "Expected success status when no connection exists"
            assert not no_conn_json['was_connected'], "Expected was_connected to be False when no connection exists"
            logger.info("Successfully tested disconnection when no connection exists")

            # Establish a connection
            assert await make_connection(client), "Failed to establish SSH connection"
            assert await is_ssh_connected(client), "Failed to verify SSH connection is active"
            logger.info("Established SSH connection for disconnect test")

            # Get connection details before disconnecting
            status_result = await client.call_tool("ssh_conn_status", {})
            status_json = json.loads(extract_result_text(status_result))
            conn_user = status_json['user']
            conn_host = status_json['host']
            logger.info(f"Connected to {conn_user}@{conn_host}")

            # Test disconnecting an active connection
            disconnect_result = await client.call_tool("ssh_host_disconnect", {})
            disconnect_json = json.loads(extract_result_text(disconnect_result))

            # Verify the disconnect result
            assert disconnect_json['status'] == 'success', "Expected success status for disconnect"
            assert disconnect_json['was_connected'], "Expected was_connected to be True"
            # Check that the disconnected_from contains the host (user may have domain prefix on Windows)
            assert conn_host in disconnect_json['disconnected_from'], \
                f"Disconnect should report the correct host. Got: {disconnect_json['disconnected_from']}"
            logger.info(f"Disconnect result: {disconnect_json}")

            # Verify connection is actually closed
            assert not await is_ssh_connected(client), "Connection should be closed after disconnect"
            logger.info("Verified SSH connection is closed after disconnect")

            # Test that we can reconnect after explicit disconnection
            assert await make_connection(client), "Failed to re-establish SSH connection after disconnect"
            assert await is_ssh_connected(client), "Failed to verify SSH connection is active after reconnect"
            logger.info("Successfully reconnected after explicit disconnect")

            # Clean up
            await disconnect_ssh(client)
            logger.info("SSH disconnect test completed successfully")

        except Exception as e:
            logger.error(f"Error in SSH disconnect test: {e}", exc_info=True)
            raise
        finally:
            # Ensure connection is closed after the test
            await disconnect_ssh(client)

    print_test_footer()
