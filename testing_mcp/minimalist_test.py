import pytest
import asyncio
import sys
import os
import logging
from pathlib import Path

# Add project root to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

# Import the MCP instance from mcp_ssh_server
from mcp_ssh_server import mcp

# Import Client from fastmcp
try:
    from fastmcp import Client
except ImportError as e:
    print(f"FATAL: Failed to import FastMCP Client. Error: {e}", file=sys.stderr)
    print("Make sure fastmcp is installed and you are running from the correct directory.", file=sys.stderr)
    sys.exit(1)

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("minimalist_test")

@pytest.mark.asyncio
async def test_ssh_status_direct():
    """
    A minimalist test that directly uses the MCP instance from mcp_ssh_server.py
    to test the ssh_status tool.
    """
    logger.info("Starting minimalist test with direct MCP instance")
    
    # Use the Client context manager with the imported mcp instance
    async with Client(mcp) as client:
        logger.info("Client created. Testing ssh_status tool...")
        
        # First, list available tools to verify ssh_status is available
        try:
            logger.info("Listing available tools...")
            tools = await client.list_tools()
            logger.info(f"Found {len(tools)} tool(s)")
            
            # Print all tool names for debugging
            tool_names = [tool.name for tool in tools]
            logger.info(f"Available tools: {tool_names}")
            
            # Check if ssh_status is in the list
            assert "ssh_status" in tool_names, "ssh_status tool not found in available tools"
            logger.info("ssh_status tool is available")
            
            # Try to call the ssh_status tool
            # Note: This will likely fail if no SSH connection is established
            # but it's a good test of the MCP infrastructure
            try:
                logger.info("Attempting to call ssh_status tool...")
                status_result = await client.call_tool("ssh_status", {})
                logger.info(f"ssh_status result: {status_result}")
                assert status_result is not None, "ssh_status returned None"
            except Exception as e:
                logger.warning(f"Expected error calling ssh_status (no active connection): {e}")
                # This is expected to fail with "No active SSH connection"
                assert "No active SSH connection" in str(e), f"Unexpected error: {e}"
                logger.info("Received expected 'No active SSH connection' error")
                
            logger.info("Minimalist test completed successfully")
            
        except Exception as e:
            logger.error(f"Error in minimalist test: {e}", exc_info=True)
            raise

@pytest.mark.asyncio
async def test_with_fixtures():
    """
    Test using the fixtures directly without relying on pytest's fixture mechanism.
    This approach avoids issues with the async generator fixture.
    """
    logger.info("Starting test with fixtures (direct approach)")
    
    try:
        # Import the necessary functions directly
        from test_mcp_fixtures import setup_test_environment, teardown_test_environment
        from mcp_ssh_server import mcp
        
        # Set up the test environment
        logger.info("Setting up test environment")
        await setup_test_environment()
        
        try:
            # Create a client directly
            logger.info("Creating MCP client")
            async with Client(mcp) as client:
                # List available tools
                logger.info("Listing available tools")
                tools = await client.list_tools()
                tool_names = [tool.name for tool in tools]
                logger.info(f"Available tools: {tool_names}")
                
                # Check if ssh_status is available
                assert "ssh_status" in tool_names, "ssh_status tool not found"
                
                # Try to call ssh_status
                logger.info("Calling ssh_status")
                try:
                    status_result = await client.call_tool("ssh_status", {})
                    logger.info(f"ssh_status result: {status_result}")
                    
                    # Basic validation of the result
                    assert status_result is not None, "ssh_status returned None"
                    # The result is a list of TextContent objects, not a dict
                    assert isinstance(status_result, list), f"Expected list result, got {type(status_result)}"
                    assert len(status_result) > 0, "Expected non-empty list result"
                        
                    # The first item should be a TextContent object with JSON text
                    content = status_result[0]
                    assert hasattr(content, 'text'), "Expected TextContent object with 'text' attribute"
                        
                    # The text should be a JSON string that we can parse
                    import json
                    status_json = json.loads(content.text)
                    assert isinstance(status_json, dict), "Expected JSON to parse to dict"
                    assert "connection" in status_json, "Expected 'connection' key in result"
                    assert "system" in status_json, "Expected 'system' key in result"
                    
                    logger.info("Test with fixtures completed successfully")
                except Exception as e:
                    logger.warning(f"Error calling ssh_status: {e}")
                    # This might be expected if no SSH connection is established
                    if "No active SSH connection" in str(e):
                        logger.info("Received expected 'No active SSH connection' error")
                    else:
                        raise
        finally:
            # Clean up
            logger.info("Tearing down test environment")
            await teardown_test_environment()
            
    except Exception as e:
        logger.error(f"Error in test with fixtures: {e}", exc_info=True)
        raise

@pytest.mark.asyncio
async def test_simple_fixture_usage():
    """
    A simpler test that directly imports and uses the test fixtures
    without relying on pytest's fixture mechanism.
    """
    logger.info("Starting simple fixture test")
    
    try:
        from test_mcp_fixtures import setup_test_environment, teardown_test_environment
        from mcp_ssh_server import mcp
        
        # Set up the test environment
        logger.info("Setting up test environment")
        await setup_test_environment()
        
        try:
            # Create a client directly instead of using get_mcp_client
            logger.info("Creating MCP client directly")
            async with Client(mcp) as client:
                # List available tools
                logger.info("Listing available tools")
                tools = await client.list_tools()
                tool_names = [tool.name for tool in tools]
                logger.info(f"Available tools: {tool_names}")
                
                # Check if ssh_status is available
                assert "ssh_status" in tool_names, "ssh_status tool not found"
                
                # Try to call ssh_status
                logger.info("Calling ssh_status")
                try:
                    status = await client.call_tool("ssh_status", {})
                    logger.info(f"Status result: {status}")
                    assert status is not None
                    
                    # Parse the JSON from the TextContent
                    if isinstance(status, list) and len(status) > 0 and hasattr(status[0], 'text'):
                        import json
                        status_json = json.loads(status[0].text)
                        logger.info(f"Parsed status JSON: {status_json}")
                except Exception as e:
                    logger.warning(f"Error calling ssh_status: {e}")
                    # This might be expected if no SSH connection is established
                    if "No active SSH connection" in str(e):
                        logger.info("Received expected 'No active SSH connection' error")
                    else:
                        raise
                
                logger.info("Simple fixture test completed")
                
        finally:
            # Clean up
            logger.info("Tearing down test environment")
            await teardown_test_environment()
            
    except Exception as e:
        logger.error(f"Error in simple fixture test: {e}", exc_info=True)
        raise

@pytest.mark.asyncio
async def test_ssh_connection_and_status():
    """
    Test that establishes an SSH connection and then checks the status.
    This test creates its own SSH client and connects to the test server.
    """
    logger.info("Starting SSH connection and status test")
    
    try:
        from test_mcp_fixtures import setup_test_environment, teardown_test_environment
        from mcp_ssh_server import mcp
        from ssh_client import SshClient
        import os
        
        # Set up the test environment
        logger.info("Setting up test environment")
        await setup_test_environment()
        
        try:
            # Create our own SSH client
            logger.info("Creating SSH client")
            ssh_port = int(os.environ.get('SSH_TEST_PORT', 2222))
            ssh_user = os.environ.get('SSH_TEST_USER', 'testuser')
            ssh_password = os.environ.get('SSH_TEST_PASSWORD', 'testpass')
            
            local_ssh_client = SshClient(
                host='localhost',
                user=ssh_user,
                port=ssh_port,
                password=ssh_password
            )
            
            # Check if the SSH client is connected
            logger.info("Checking SSH client connection")
            assert local_ssh_client is not None, "Failed to create SSH client"
            
            # Get connection status
            logger.info("Getting SSH connection status")
            status = local_ssh_client.get_connection_status()
            logger.info(f"Connection status: {status}")
            assert status is not None, "Connection status is None"
            
            # Create MCP client
            logger.info("Creating MCP client")
            async with Client(mcp) as client:
                # Try to call ssh_status
                logger.info("Calling ssh_status through MCP")
                try:
                    status_result = await client.call_tool("ssh_status", {})
                    logger.info(f"ssh_status result: {status_result}")
                    assert status_result is not None, "ssh_status returned None"
                    
                    # Parse the JSON from the TextContent
                    if isinstance(status_result, list) and len(status_result) > 0 and hasattr(status_result[0], 'text'):
                        import json
                        status_json = json.loads(status_result[0].text)
                        logger.info(f"Parsed status JSON: {status_json}")
                        assert "connection" in status_json, "Expected 'connection' key in result"
                        assert "system" in status_json, "Expected 'system' key in result"
                except Exception as e:
                    logger.error(f"Error calling ssh_status: {e}")
                    raise
                
                logger.info("SSH connection and status test completed successfully")
                
            # Close our local SSH client
            if local_ssh_client:
                local_ssh_client.close()
                
        finally:
            # Clean up
            logger.info("Tearing down test environment")
            await teardown_test_environment()
            
    except Exception as e:
        logger.error(f"Error in SSH connection and status test: {e}", exc_info=True)
        raise

if __name__ == "__main__":
    """
    Allow running this test directly without pytest
    """
    try:
        asyncio.run(test_ssh_status_direct())
        print("Direct test completed successfully")
        
        # Run the SSH connection and status test
        asyncio.run(test_ssh_connection_and_status())
        print("SSH connection and status test completed successfully")
    except Exception as e:
        print(f"Test failed: {e}")
        sys.exit(1)
