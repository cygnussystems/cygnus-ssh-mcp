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
async def test_with_fixtures(mcp_client):
    """
    Test using the fixtures from conftest.py.
    This test assumes the fixtures properly set up an SSH connection.
    """
    logger.info("Starting test with fixtures")
    
    try:
        # Test if the client is properly connected
        logger.info("Testing ssh_status with fixture-provided client")
        status_result = await mcp_client.call_tool("ssh_status", {})
        logger.info(f"ssh_status result: {status_result}")
        
        # Basic validation of the result
        assert status_result is not None, "ssh_status returned None"
        assert isinstance(status_result, dict), f"Expected dict result, got {type(status_result)}"
        assert "connection" in status_result, "Expected 'connection' key in result"
        assert "system" in status_result, "Expected 'system' key in result"
        
        logger.info("Test with fixtures completed successfully")
    except Exception as e:
        logger.error(f"Error in test with fixtures: {e}", exc_info=True)
        raise

if __name__ == "__main__":
    """
    Allow running this test directly without pytest
    """
    try:
        asyncio.run(test_ssh_status_direct())
        print("Direct test completed successfully")
    except Exception as e:
        print(f"Test failed: {e}")
        sys.exit(1)
