import pytest
import asyncio
import json
import logging
from conftest import print_test_header, print_test_footer, setup_test_environment, teardown_test_environment

# Configure logging
logger = logging.getLogger(__name__)

@pytest.mark.asyncio
async def test_ssh_status():
    """Test retrieving SSH connection status."""
    print_test_header("Testing 'ssh_status' tool")
    logger.info("Starting SSH status test")
    
    # Import necessary modules
    from mcp_ssh_server import mcp
    from fastmcp import Client
    
    # Use the Client context manager with the imported mcp instance
    async with Client(mcp) as client:
        # First, add the test server configuration
        from conftest import SSH_TEST_USER, SSH_TEST_PASSWORD, SSH_TEST_PORT
        
        try:
            # Try to get status first (might fail if no connection)
            try:
                status_result = await client.call_tool("ssh_status", {})
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
                    
                    # Now get the status
                    status_result = await client.call_tool("ssh_status", {})
                else:
                    raise
            
            # Verify the result
            assert status_result is not None, "Expected non-empty result"
            assert isinstance(status_result, list), f"Expected list result, got {type(status_result)}"
            assert len(status_result) > 0, "Expected non-empty list result"
            assert hasattr(status_result[0], 'text'), "Expected TextContent object with 'text' attribute"
            
            # Parse the JSON response
            result_json = json.loads(status_result[0].text)
            logger.info(f"Status result: {result_json}")
            
            # Verify the result structure
            assert 'connection' in result_json, "Result should include connection info"
            assert 'system' in result_json, "Result should include system info"
            
            # Check connection details
            conn = result_json['connection']
            assert 'host' in conn, "Connection info should include host"
            
            # Check system details
            system = result_json['system']
            assert 'os_name' in system, "System info should include OS name"
            assert 'cpu_count' in system, "System info should include CPU count"
        except Exception as e:
            logger.error(f"Error in SSH status test: {e}")
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
#             await test_ssh_status()
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
