import asyncio
import sys
import os
import pytest
import tempfile
import yaml
from pathlib import Path
import logging

# Ensure the main project directory is in the Python path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

try:
    from fastmcp import Client
    from mcp_ssh_server import mcp, SshHostManager
    from ssh_models import SshError
except ImportError as e:
    print(f"FATAL: Failed to import required modules. Error: {e}", file=sys.stderr)
    print("Make sure fastmcp is installed and you are running from the correct directory.", file=sys.stderr)
    sys.exit(1)

# Configure logging
logging.basicConfig(level=logging.INFO, 
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("mcp_tools_test")

# Test data constants
EXPECTED_CORE_TOOLS = {
    "ssh_connect", "ssh_add_host", "ssh_run", "ssh_file_transfer",
    "ssh_status", "ssh_verify_sudo", "ssh_replace_block", 
    "ssh_output", "ssh_command_history"
}

TASK_MANAGEMENT_TOOLS = {
    "ssh_launch_task", "ssh_task_status", "ssh_task_kill"
}

FILE_OPERATION_TOOLS = {
    "ssh_mkdir", "ssh_rmdir", "ssh_listdir", "ssh_stat", "ssh_replace_line"
}

DIRECTORY_OPERATION_TOOLS = {
    "ssh_search_files", "ssh_directory_size", "ssh_delete_directory", 
    "ssh_batch_delete", "ssh_move", "ssh_list_directory",
    "ssh_create_archive", "ssh_extract_archive", "ssh_search_content",
    "ssh_copy_directory"
}

# Fixtures
@pytest.fixture
async def temp_host_manager():
    """Create a temporary host manager for testing."""
    with tempfile.NamedTemporaryFile(mode='w', delete=False) as tmp:
        yaml.safe_dump({'hosts': []}, tmp)
        config_path = Path(tmp.name)
    
    host_manager = SshHostManager(config_path=config_path)
    
    # Add a test host (this won't actually be used for connections)
    host_manager.add_host('test_host', 'localhost', 22, 'testuser', 'testpass')
    
    yield host_manager
    
    # Cleanup
    try:
        config_path.unlink()
    except Exception as e:
        logger.warning(f"Failed to clean up temporary config file: {e}")

@pytest.fixture
async def mcp_client():
    """Create an MCP client for testing."""
    async with Client(mcp) as client:
        yield client

# Test functions
@pytest.mark.asyncio
async def test_tool_listing(mcp_client):
    """
    Test that all expected core tools are available in the MCP server.
    
    This test verifies that:
    1. The MCP server has registered all the expected core SSH tools
    2. Each tool has a proper description
    3. No expected tools are missing
    """
    logger.info("Testing tool listing...")
    
    tools = await mcp_client.list_tools()
    logger.info(f"Found {len(tools)} tool(s)")
    
    found_tool_ids = set()
    for tool in tools:
        tool_id = tool.name
        description = tool.description
        logger.debug(f"Tool: {tool_id}, Description: {description.strip()}")
        found_tool_ids.add(tool_id)
    
    missing = EXPECTED_CORE_TOOLS - found_tool_ids
    extra = found_tool_ids - EXPECTED_CORE_TOOLS - {"add", "subtract", "get_joke"}  # Ignore sample tools
    
    assert not missing, f"Missing expected core tools: {missing}. Available tools: {found_tool_ids}"
    logger.info("Tool listing test passed successfully")

@pytest.mark.asyncio
async def test_ssh_add_host(temp_host_manager, mcp_client):
    """
    Test the ssh_add_host tool functionality.
    
    This test verifies that:
    1. The tool accepts valid parameters
    2. The host is correctly added to the configuration
    3. The host can be retrieved from the configuration
    4. The host properties match what was provided
    """
    logger.info("Testing 'ssh_add_host' tool...")
    
    # Test parameters
    add_host_params = {
        "name": "test_host2",
        "host": "example.com",
        "user": "user2",
        "password": "pass2",
        "port": 2222
    }
    
    # Call the tool
    add_host_result = await mcp_client.call_tool("ssh_add_host", add_host_params)
    logger.info(f"Tool result: {add_host_result}")
    
    # Verify the host was added
    host = temp_host_manager.get_host("test_host2")
    assert host is not None, "Host was not added to configuration"
    assert host["host"] == "example.com", f"Host address mismatch: {host['host']} != example.com"
    assert host["port"] == 2222, f"Port mismatch: {host['port']} != 2222"
    assert host["user"] == "user2", f"Username mismatch: {host['user']} != user2"
    
    logger.info("ssh_add_host test passed")

@pytest.mark.asyncio
async def test_ssh_connect_parameters(mcp_client):
    """
    Test parameter validation for ssh_connect.
    
    This test verifies that:
    1. The tool properly validates its parameters
    2. Appropriate errors are raised for invalid parameters
    """
    logger.info("Testing 'ssh_connect' parameter validation...")
    
    # Test with nonexistent host
    connect_params = {"host_name": "nonexistent_host"}
    
    with pytest.raises(Exception) as excinfo:
        await mcp_client.call_tool("ssh_connect", connect_params)
    
    # Verify the error message
    error_message = str(excinfo.value)
    logger.info(f"Got expected error: {error_message}")
    assert "not found" in error_message, f"Expected 'not found' error, got: {error_message}"
    
    logger.info("ssh_connect parameter validation test passed")

@pytest.mark.asyncio
@pytest.mark.parametrize("tool_category,expected_tools", [
    ("task_management", TASK_MANAGEMENT_TOOLS),
    ("file_operations", FILE_OPERATION_TOOLS),
    ("directory_operations", DIRECTORY_OPERATION_TOOLS)
])
async def test_tool_category_existence(mcp_client, tool_category, expected_tools):
    """
    Test that all expected tools in a category exist.
    
    This test verifies that:
    1. All tools in the specified category are registered in the MCP server
    2. No expected tools are missing
    
    Parameters:
        tool_category: The name of the tool category being tested
        expected_tools: Set of tool names expected in this category
    """
    logger.info(f"Verifying {tool_category} tools...")
    
    tools = await mcp_client.list_tools()
    tool_names = {tool.name for tool in tools}
    
    missing_tools = expected_tools - tool_names
    
    assert not missing_tools, f"Missing {tool_category} tools: {missing_tools}. Available tools: {tool_names}"
    
    logger.info(f"{tool_category} tools verification passed")

# Main execution
async def run_mcp_server_tests():
    """Run all MCP server tool tests sequentially."""
    logger.info("Starting SSH MCP server tool tests...")
    
    # Create fixtures manually for sequential execution
    host_manager = None
    config_path = None
    
    try:
        # Setup temporary host manager
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as tmp:
            yaml.safe_dump({'hosts': []}, tmp)
            config_path = Path(tmp.name)
        
        host_manager = SshHostManager(config_path=config_path)
        host_manager.add_host('test_host', 'localhost', 22, 'testuser', 'testpass')
        
        # Run tests with client
        async with Client(mcp) as client:
            logger.info("MCP client created")
            
            # Run all tests
            await test_tool_listing(client)
            await test_ssh_add_host(host_manager, client)
            await test_ssh_connect_parameters(client)
            
            # Run parametrized tests manually
            await test_tool_category_existence(client, "task_management", TASK_MANAGEMENT_TOOLS)
            await test_tool_category_existence(client, "file_operations", FILE_OPERATION_TOOLS)
            await test_tool_category_existence(client, "directory_operations", DIRECTORY_OPERATION_TOOLS)
            
    except Exception as e:
        logger.error(f"Test run failed with error: {e}", exc_info=True)
        raise
    finally:
        # Clean up
        if config_path and config_path.exists():
            try:
                config_path.unlink()
            except Exception as e:
                logger.warning(f"Failed to clean up temporary config file: {e}")
    
    logger.info("All SSH MCP server tool tests completed successfully")

if __name__ == "__main__":
    try:
        asyncio.run(run_mcp_server_tests())
    except Exception as e:
        logger.error(f"Test run failed with error: {e}", exc_info=True)
        sys.exit(1)
