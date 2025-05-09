import pytest
import json
import asyncio
import logging
from conftest import print_test_header, print_test_footer

# Configure logging
logger = logging.getLogger(__name__)

@pytest.mark.asyncio
async def test_ssh_run_basic():
    """Test basic command execution with ssh_run."""
    print_test_header("Testing 'ssh_run' basic command")
    logger.info("Starting SSH run basic test")
    
    # Import necessary modules
    from mcp_ssh_server import mcp
    from fastmcp import Client
    
    # Use the Client context manager with the imported mcp instance
    async with Client(mcp) as client:
        # First, add the test server configuration
        from conftest import SSH_TEST_USER, SSH_TEST_PASSWORD, SSH_TEST_PORT
        
        try:
            # Try to run a command first (might fail if no connection)
            try:
                # Simple echo command
                run_params = {
                    "command": "echo 'Hello from MCP SSH!'",
                    "io_timeout": 10.0
                }
                run_result = await client.call_tool("ssh_run", run_params)
                logger.info("SSH connection already established")
            except Exception as e:
                if "No active SSH connection" in str(e):
                    # Add the test server configuration
                    logger.info("Adding test server configuration")
                    await client.call_tool("ssh_add_host", {
                        "name": "test_server",
                        "host": "localhost",
                        "user": SSH_TEST_USER,
                        "password": SSH_TEST_PASSWORD,
                        "port": SSH_TEST_PORT
                    })
                    
                    # Connect to the test server
                    logger.info("Connecting to test server")
                    await client.call_tool("ssh_connect", {
                        "host_name": "test_server"
                    })
                    
                    # Now run the command
                    run_params = {
                        "command": "echo 'Hello from MCP SSH!'",
                        "io_timeout": 10.0
                    }
                    run_result = await client.call_tool("ssh_run", run_params)
                else:
                    raise
            
            # Verify the result
            assert run_result is not None, "Expected non-empty result"
            assert isinstance(run_result, list), f"Expected list result, got {type(run_result)}"
            assert len(run_result) > 0, "Expected non-empty list result"
            assert hasattr(run_result[0], 'text'), "Expected TextContent object with 'text' attribute"
            
            # Parse the JSON response
            result_json = json.loads(run_result[0].text)
            logger.info(f"Command result: {result_json}")
            
            assert result_json['exit_code'] == 0, f"Expected exit code 0, got {result_json['exit_code']}"
            assert "Hello from MCP SSH!" in result_json['output'], "Expected output not found"
            assert 'pid' in result_json, "PID should be included in result"
            assert 'start_time' in result_json, "Start time should be included in result"
            assert 'end_time' in result_json, "End time should be included in result"
            
            logger.info("SSH run basic test completed successfully")
        except Exception as e:
            logger.error(f"Error in SSH run basic test: {e}")
            raise
    
    print_test_footer()

@pytest.mark.asyncio
async def test_ssh_run_multiline():
    """Test command execution with multiple output lines."""
    print_test_header("Testing 'ssh_run' multiline command")
    logger.info("Starting SSH run multiline test")
    
    # Import necessary modules
    from mcp_ssh_server import mcp
    from fastmcp import Client
    
    # Use the Client context manager with the imported mcp instance
    async with Client(mcp) as client:
        # First, add the test server configuration
        from conftest import SSH_TEST_USER, SSH_TEST_PASSWORD, SSH_TEST_PORT
        
        try:
            # Try to run a command first (might fail if no connection)
            try:
                # Test with a command that produces multiple lines
                run_params = {
                    "command": "for i in {1..5}; do echo \"Line $i\"; done",
                    "io_timeout": 10.0
                }
                run_result = await client.call_tool("ssh_run", run_params)
                logger.info("SSH connection already established")
            except Exception as e:
                if "No active SSH connection" in str(e):
                    # Add the test server configuration
                    logger.info("Adding test server configuration")
                    await client.call_tool("ssh_add_host", {
                        "name": "test_server",
                        "host": "localhost",
                        "user": SSH_TEST_USER,
                        "password": SSH_TEST_PASSWORD,
                        "port": SSH_TEST_PORT
                    })
                    
                    # Connect to the test server
                    logger.info("Connecting to test server")
                    await client.call_tool("ssh_connect", {
                        "host_name": "test_server"
                    })
                    
                    # Now run the command
                    run_params = {
                        "command": "for i in {1..5}; do echo \"Line $i\"; done",
                        "io_timeout": 10.0
                    }
                    run_result = await client.call_tool("ssh_run", run_params)
                else:
                    raise
            
            # Verify the result
            assert run_result is not None, "Expected non-empty result"
            assert isinstance(run_result, list), f"Expected list result, got {type(run_result)}"
            assert len(run_result) > 0, "Expected non-empty list result"
            
            # Parse the JSON response
            result_json = json.loads(run_result[0].text)
            logger.info(f"Multi-line command result: {result_json}")
            
            # Verify the result
            assert result_json['exit_code'] == 0, f"Expected exit code 0, got {result_json['exit_code']}"
            assert "Line 1" in result_json['output'], "Expected 'Line 1' not found in output"
            assert "Line 5" in result_json['output'], "Expected 'Line 5' not found in output"
            
            logger.info("SSH run multiline test completed successfully")
        except Exception as e:
            logger.error(f"Error in SSH run multiline test: {e}")
            raise
    
    print_test_footer()

@pytest.mark.asyncio
async def test_ssh_run_failure():
    """Test command execution with a failing command."""
    print_test_header("Testing 'ssh_run' failure command")
    logger.info("Starting SSH run failure test")
    
    # Import necessary modules
    from mcp_ssh_server import mcp
    from fastmcp import Client
    
    # Use the Client context manager with the imported mcp instance
    async with Client(mcp) as client:
        # First, add the test server configuration
        from conftest import SSH_TEST_USER, SSH_TEST_PASSWORD, SSH_TEST_PORT
        
        try:
            # Ensure we have a connection first
            try:
                # Try a simple command to check connection
                await client.call_tool("ssh_status", {})
                logger.info("SSH connection already established")
            except Exception as e:
                if "No active SSH connection" in str(e):
                    # Add the test server configuration
                    logger.info("Adding test server configuration")
                    await client.call_tool("ssh_add_host", {
                        "name": "test_server",
                        "host": "localhost",
                        "user": SSH_TEST_USER,
                        "password": SSH_TEST_PASSWORD,
                        "port": SSH_TEST_PORT
                    })
                    
                    # Connect to the test server
                    logger.info("Connecting to test server")
                    await client.call_tool("ssh_connect", {
                        "host_name": "test_server"
                    })
                else:
                    raise
            
            # Now run the failing command
            run_params = {
                "command": "exit 42",
                "io_timeout": 10.0
            }
            
            # Run the failing command via MCP
            logger.info("Running command failure test")
            with pytest.raises(Exception) as excinfo:
                await client.call_tool("ssh_run", run_params)
            
            # Verify the exception
            error_message = str(excinfo.value)
            logger.info(f"Received expected error: {error_message}")
            assert "exit code 42" in error_message, "Exception should mention exit code 42"
            
            logger.info("SSH run failure test completed successfully")
        except Exception as e:
            logger.error(f"Error in SSH run failure test: {e}")
            raise
    
    print_test_footer()
# if __name__ == "__main__":
#     """
#     Allow running this test directly without pytest
#     """
#     import sys
#     from conftest import setup_test_environment, teardown_test_environment
# 
#     async def run_tests():
#         """Run all tests in this file"""
#         logger.info("Setting up test environment")
#         await setup_test_environment()
# 
#         try:
#             # Run the tests
#             logger.info("Running tests")
#             await test_ssh_run_basic()
#             await test_ssh_run_multiline()
# 
#             # The failure test is expected to raise an exception
#             try:
#                 await test_ssh_run_failure()
#                 print("ERROR: Failure test did not raise an exception as expected")
#             except Exception as e:
#                 if "exit code 42" in str(e):
#                     logger.info("Failure test passed with expected exception")
#                 else:
#                     logger.error(f"Failure test raised unexpected exception: {e}")
#                     raise
# 
#             logger.info("All tests completed successfully")
# 
#         finally:
#             # Clean up
#             logger.info("Tearing down test environment")
#             await teardown_test_environment()
# 
#     try:
#         # Configure logging for direct execution
#         logging.basicConfig(level=logging.INFO, 
#                            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
# 
#         # Run the tests
#         asyncio.run(run_tests())
#         print("All tests completed successfully")
#     except Exception as e:
#         print(f"Tests failed: {e}")
#         sys.exit(1)
