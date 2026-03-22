# import pytest
# import asyncio
# import sys
# import os
# import logging
# from pathlib import Path
#
# # Add project root to path
# project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
# sys.path.insert(0, project_root)
#
# # Import the MCP instance from mcp_ssh_server
# from mcp_ssh_server import mcp
#
# # Import Client from fastmcp
# try:
#     from fastmcp import Client
# except ImportError as e:
#     print(f"FATAL: Failed to import FastMCP Client. Error: {e}", file=sys.stderr)
#     print("Make sure fastmcp is installed and you are running from the correct directory.", file=sys.stderr)
#     sys.exit(1)
#
# # Configure logging
# logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
# logger = logging.getLogger("minimalist_test")
#
# @pytest.mark.asyncio
# async def test_ssh_status_direct():
#     """
#     A minimalist test that directly uses the MCP instance from mcp_ssh_server.py
#     to test the ssh_status tool.
#     """
#     logger.info("Starting minimalist test with direct MCP instance")
#
#     # Import test environment variables
#     from conftest import SSH_TEST_USER, SSH_TEST_PASSWORD, SSH_TEST_HOST, SSH_TEST_PORT
#
#     # Use the Client context manager with the imported mcp instance
#     async with Client(mcp) as client:
#         logger.info("Client created. Testing ssh_status tool...")
#
#         # First, list available tools to verify ssh_status is available
#         try:
#             logger.info("Listing available tools...")
#             tools = await client.list_tools()
#             logger.info(f"Found {len(tools)} tool(s)")
#
#             # Print all tool names for debugging
#             tool_names = [tool.name for tool in tools]
#             logger.info(f"Available tools: {tool_names}")
#
#             # Check if ssh_status is in the list
#             assert "ssh_conn_status" in tool_names, "ssh_status tool not found in available tools"
#             logger.info("ssh_status tool is available")
#
#             # Connect to the test server first
#             logger.info("Adding and connecting to test server...")
#             await client.call_tool("ssh_conn_add_host", {
#                 "user": SSH_TEST_USER,
#                 "host": SSH_TEST_HOST,
#                 "password": SSH_TEST_PASSWORD,
#                 "port": SSH_TEST_PORT
#             })
#
#             host_key = f"{SSH_TEST_USER}@{SSH_TEST_HOST}"
#             await client.call_tool("ssh_conn_connect", {
#                 "host_name": host_key
#             })
#
#             # Now call the ssh_status tool which should work
#             logger.info("Calling ssh_conn_status tool...")
#             status_result = await client.call_tool("ssh_conn_status", {})
#             logger.info(f"ssh_status result: {status_result}")
#             assert status_result is not None, "ssh_status returned None"
#
#             logger.info("Minimalist test completed successfully")
#
#         except Exception as e:
#             logger.error(f"Error in minimalist test: {e}", exc_info=True)
#             raise
#
# @pytest.mark.asyncio
# async def test_with_fixtures():
#     """
#     Test using the fixtures directly without relying on pytest's fixture mechanism.
#     This approach avoids issues with the async generator fixture.
#     """
#     logger.info("Starting test with fixtures (direct approach)")
#
#     try:
#         # Import the necessary functions directly
#         from conftest import setup_test_environment, teardown_test_environment, SSH_TEST_USER, SSH_TEST_PASSWORD, SSH_TEST_HOST, SSH_TEST_PORT
#         from mcp_ssh_server import mcp
#
#         # Set up the test environment
#         logger.info("Setting up test environment")
#         try:
#             await setup_test_environment()
#         except RuntimeError as e:
#             if "Failed to connect to SSH test server" in str(e):
#                 pytest.skip(f"Skipping test due to Docker container connection issues: {e}")
#                 return
#             raise
#
#         try:
#             # Create a client directly
#             logger.info("Creating MCP client")
#             async with Client(mcp) as client:
#                 # List available tools
#                 logger.info("Listing available tools")
#                 tools = await client.list_tools()
#                 tool_names = [tool.name for tool in tools]
#                 logger.info(f"Available tools: {tool_names}")
#
#                 # Check if ssh_status is available
#                 assert "ssh_conn_status" in tool_names, "ssh_status tool not found"
#
#                 # Connect to the test server first
#                 logger.info("Adding and connecting to test server...")
#                 await client.call_tool("ssh_conn_add_host", {
#                     "user": SSH_TEST_USER,
#                     "host": SSH_TEST_HOST,
#                     "password": SSH_TEST_PASSWORD,
#                     "port": SSH_TEST_PORT
#                 })
#
#                 host_key = f"{SSH_TEST_USER}@{SSH_TEST_HOST}"
#                 await client.call_tool("ssh_conn_connect", {
#                     "host_name": host_key
#                 })
#
#                 # Call ssh_status
#                 logger.info("Calling ssh_conn_status")
#                 status_result = await client.call_tool("ssh_conn_status", {})
#                 logger.info(f"ssh_status result: {status_result}")
#
#                 # Basic validation of the result
#                 assert status_result is not None, "ssh_status returned None"
#                 # The result is a list of TextContent objects, not a dict
#                 assert isinstance(status_result, list), f"Expected list result, got {type(status_result)}"
#                 assert len(status_result) > 0, "Expected non-empty list result"
#
#                 # The first item should be a TextContent object with JSON text
#                 content = status_result[0]
#                 assert hasattr(content, 'text'), "Expected TextContent object with 'text' attribute"
#
#                 # The text should be a JSON string that we can parse
#                 import json
#                 status_json = json.loads(content.text)
#                 assert isinstance(status_json, dict), "Expected JSON to parse to dict"
#                 assert "connection" in status_json, "Expected 'connection' key in result"
#                 assert "system" in status_json, "Expected 'system' key in result"
#
#                 logger.info("Test with fixtures completed successfully")
#         finally:
#             # Clean up
#             logger.info("Tearing down test environment")
#             await teardown_test_environment()
#
#     except Exception as e:
#         logger.error(f"Error in test with fixtures: {e}", exc_info=True)
#         raise
#
#
#
# @pytest.mark.asyncio
# async def test_simple_fixture_usage():
#     """
#     A simpler test that directly imports and uses the test fixtures
#     without relying on pytest's fixture mechanism.
#     """
#     logger.info("Starting simple fixture test")
#
#     try:
#         from conftest import setup_test_environment, teardown_test_environment, SSH_TEST_USER, SSH_TEST_PASSWORD, SSH_TEST_HOST, SSH_TEST_PORT
#         from mcp_ssh_server import mcp
#
#         # Set up the test environment
#         logger.info("Setting up test environment")
#         try:
#             await setup_test_environment()
#         except RuntimeError as e:
#             if "Failed to connect to SSH test server" in str(e):
#                 pytest.skip(f"Skipping test due to Docker container connection issues: {e}")
#                 return
#             raise
#
#         try:
#             # Create a client directly instead of using get_mcp_client
#             logger.info("Creating MCP client directly")
#             async with Client(mcp) as client:
#                 # List available tools
#                 logger.info("Listing available tools")
#                 tools = await client.list_tools()
#                 tool_names = [tool.name for tool in tools]
#                 logger.info(f"Available tools: {tool_names}")
#
#                 # Check if ssh_status is available
#                 assert "ssh_conn_status" in tool_names, "ssh_status tool not found"
#
#                 # Connect to the test server first
#                 logger.info("Adding and connecting to test server...")
#                 await client.call_tool("ssh_conn_add_host", {
#                     "user": SSH_TEST_USER,
#                     "host": SSH_TEST_HOST,
#                     "password": SSH_TEST_PASSWORD,
#                     "port": SSH_TEST_PORT
#                 })
#
#                 host_key = f"{SSH_TEST_USER}@{SSH_TEST_HOST}"
#                 await client.call_tool("ssh_conn_connect", {
#                     "host_name": host_key
#                 })
#
#                 # Call ssh_status
#                 logger.info("Calling ssh_conn_status")
#                 status = await client.call_tool("ssh_conn_status", {})
#                 logger.info(f"Status result: {status}")
#                 assert status is not None
#
#                 # Parse the JSON from the TextContent
#                 import json
#                 status_json = json.loads(status[0].text)
#                 logger.info(f"Parsed status JSON: {status_json}")
#                 assert "connection" in status_json, "Expected 'connection' key in result"
#                 assert "system" in status_json, "Expected 'system' key in result"
#
#                 logger.info("Simple fixture test completed")
#
#         finally:
#             # Clean up
#             logger.info("Tearing down test environment")
#             await teardown_test_environment()
#
#     except Exception as e:
#         logger.error(f"Error in simple fixture test: {e}", exc_info=True)
#         raise
#
#
#
# @pytest.mark.asyncio
# async def test_ssh_connection_and_status():
#     """
#     Test that establishes an SSH connection and then checks the status.
#     This test creates its own SSH client and connects to the test server.
#     """
#     logger.info("Starting SSH connection and status test")
#
#     try:
#         from conftest import setup_test_environment, teardown_test_environment, SSH_TEST_USER, SSH_TEST_PASSWORD, SSH_TEST_HOST, SSH_TEST_PORT
#         from mcp_ssh_server import mcp
#         from ssh_client import SshClient
#
#         # Set up the test environment
#         logger.info("Setting up test environment")
#         try:
#             await setup_test_environment()
#         except RuntimeError as e:
#             if "Failed to connect to SSH test server" in str(e):
#                 pytest.skip(f"Skipping test due to Docker container connection issues: {e}")
#                 return
#             raise
#
#         try:
#             # Create our own SSH client
#             logger.info("Creating SSH client")
#
#             local_ssh_client = SshClient(
#                 host=SSH_TEST_HOST,
#                 user=SSH_TEST_USER,
#                 port=SSH_TEST_PORT,
#                 password=SSH_TEST_PASSWORD
#             )
#
#             # Check if the SSH client is connected
#             logger.info("Checking SSH client connection")
#             assert local_ssh_client is not None, "Failed to create SSH client"
#
#             # Get connection status
#             logger.info("Getting SSH connection status")
#             status = local_ssh_client.get_connection_status()
#             logger.info(f"Connection status: {status}")
#             assert status is not None, "Connection status is None"
#
#             # Create MCP client
#             logger.info("Creating MCP client")
#             async with Client(mcp) as client:
#                 # First, add the test server configuration to MCP
#                 logger.info("Adding test server configuration to MCP")
#                 await client.call_tool("ssh_conn_add_host", {
#                     "user": SSH_TEST_USER,
#                     "host": SSH_TEST_HOST,
#                     "password": SSH_TEST_PASSWORD,
#                     "port": SSH_TEST_PORT
#                 })
#
#                 # Connect to the test server using MCP
#                 logger.info("Connecting to test server using MCP")
#                 host_key = f"{SSH_TEST_USER}@{SSH_TEST_HOST}"
#                 connect_result = await client.call_tool("ssh_conn_connect", {
#                     "host_name": host_key
#                 })
#                 logger.info(f"Connection result: {connect_result}")
#
#                 # Now call ssh_status
#                 logger.info("Calling ssh_conn_status through MCP")
#                 status_result = await client.call_tool("ssh_conn_status", {})
#                 logger.info(f"ssh_status result: {status_result}")
#                 assert status_result is not None, "ssh_status returned None"
#
#                 # Parse the JSON from the TextContent
#                 import json
#                 status_json = json.loads(status_result[0].text)
#                 logger.info(f"Parsed status JSON: {status_json}")
#                 assert "connection" in status_json, "Expected 'connection' key in result"
#                 assert "system" in status_json, "Expected 'system' key in result"
#
#                 logger.info("SSH connection and status test completed successfully")
#
#             # Close our local SSH client
#             if local_ssh_client:
#                 local_ssh_client.close()
#
#         finally:
#             # Clean up
#             logger.info("Tearing down test environment")
#             await teardown_test_environment()
#
#     except Exception as e:
#         logger.error(f"Error in SSH connection and status test: {e}", exc_info=True)
#         raise
#
# # if __name__ == "__main__":
# #     """
# #     Allow running this test directly without pytest
# #     """
# #     try:
# #         asyncio.run(test_ssh_status_direct())
# #         print("Direct test completed successfully")
# #
# #         # Run the SSH connection and status test
# #         asyncio.run(test_ssh_connection_and_status())
# #         print("SSH connection and status test completed successfully")
# #     except Exception as e:
# #         print(f"Test failed: {e}")
# #         sys.exit(1)
