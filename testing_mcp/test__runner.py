import asyncio
import sys
import pytest

# Import test fixtures
from test_mcp_fixtures import setup_test_environment, teardown_test_environment

# Import all test modules
from test_tool__run import test_ssh_run_basic, test_ssh_run_multiline, test_ssh_run_failure
from test_tool__status import test_ssh_status
from test_tool__history import test_ssh_command_history
from test_mcp_server_tools import (
    test_tool_listing,
    test_ssh_add_host,
    test_ssh_connect_parameters,
    test_tool_category_existence,
    TASK_MANAGEMENT_TOOLS,
    FILE_OPERATION_TOOLS,
    DIRECTORY_OPERATION_TOOLS
)

async def run_mcp_ssh_integration_tests():
    """Run integration tests for the SSH MCP server tools using a real SSH connection."""
    print("Starting SSH MCP server integration tests...")
    
    try:
        # Set up the test environment
        setup_success = await setup_test_environment()
        if not setup_success:
            print("Failed to set up test environment")
            return
        
        # Run all tests sequentially
        await test_ssh_run_basic()
        await test_ssh_run_multiline()
        await test_ssh_run_failure()
        await test_ssh_status()
        await test_ssh_command_history()
        
        # Run tool verification tests
        from mcp_ssh_server import mcp
        from fastmcp import Client
        
        async with Client(mcp) as client:
            await test_tool_listing(client)
            await test_tool_category_existence(client, "task_management", TASK_MANAGEMENT_TOOLS)
            await test_tool_category_existence(client, "file_operations", FILE_OPERATION_TOOLS)
            await test_tool_category_existence(client, "directory_operations", DIRECTORY_OPERATION_TOOLS)
        
        # Add more test functions here as they are created
        
    except Exception as e:
        print(f"\nTest run failed with error: {e}", file=sys.stderr)
        raise
    finally:
        # Clean up the test environment
        await teardown_test_environment()

    print("\nAll SSH MCP server integration tests completed!")

if __name__ == "__main__":
    try:
        asyncio.run(run_mcp_ssh_integration_tests())
    except Exception as e:
        print(f"\nTest run failed with error: {e}", file=sys.stderr)
        sys.exit(1)
