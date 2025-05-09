import pytest
import json
import logging
from conftest import print_test_header, print_test_footer

# Configure logging
logger = logging.getLogger(__name__)

@pytest.mark.asyncio
async def test_ssh_command_history():
    """Test retrieving command history."""
    print_test_header("Testing 'ssh_command_history' tool")
    logger.info("Starting SSH command history test")
    
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
                # Simple echo command to check connection
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
            
            # First run a few commands to ensure we have history
            logger.info("Running commands to build history")
            for i in range(3):
                run_params = {
                    "command": f"echo 'History test {i}'",
                    "io_timeout": 5.0
                }
                await client.call_tool("ssh_run", run_params)
            
            # Get command history
            logger.info("Retrieving command history")
            history_params = {
                "limit": 5,
                "include_output": True,
                "output_lines": 2
            }
            
            history_result = await client.call_tool("ssh_command_history", history_params)
            
            logger.info(f"History result: {history_result}")
            
            # Verify the result
            assert isinstance(history_result, list), "History result should be a list"
            assert len(history_result) > 0, "History should contain at least one entry"
            
            # Check the most recent entry
            latest = history_result[-1]
            assert 'command' in latest, "History entry should include command"
            assert 'exit_code' in latest, "History entry should include exit code"
            assert 'output' in latest, "History entry should include output"
            
            logger.info("SSH command history test completed successfully")
        except Exception as e:
            logger.error(f"Error in SSH command history test: {e}")
            raise
    
    print_test_footer()
