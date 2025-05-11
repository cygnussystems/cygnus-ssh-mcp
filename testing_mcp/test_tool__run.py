import pytest
import json
import asyncio # Retained as pytest.mark.asyncio might use it or for general async context
import logging
from conftest import print_test_header, print_test_footer, make_connection, disconnect_ssh, mcp_test_environment
# Import necessary modules
from mcp_ssh_server import mcp
from fastmcp import Client

# Configure logging
logger = logging.getLogger(__name__)

@pytest.mark.asyncio
async def test_ssh_run_basic(mcp_test_environment):
    """Test basic command execution with ssh_run."""
    print_test_header("Testing 'ssh_run' basic command")
    logger.info("Starting SSH run basic test")

    async with Client(mcp) as client:
        try:
            # Ensure connection is established
            assert await make_connection(client), "Failed to establish SSH connection"
            logger.info("SSH connection established or verified for basic test")

            # Simple echo command
            run_params = {
                "command": "echo 'Hello from MCP SSH!'",
                "io_timeout": 10.0
            }
            run_result = await client.call_tool("ssh_run", run_params)
            
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
        finally:
            await disconnect_ssh(client)
            logger.info("SSH connection for basic test cleaned up")
    
    print_test_footer()

@pytest.mark.asyncio
async def test_ssh_run_multiline(mcp_test_environment):
    """Test command execution with multiple output lines."""
    print_test_header("Testing 'ssh_run' multiline command")
    logger.info("Starting SSH run multiline test")

    async with Client(mcp) as client:
        try:
            # Ensure connection is established
            assert await make_connection(client), "Failed to establish SSH connection"
            logger.info("SSH connection established or verified for multiline test")

            # Test with a command that produces multiple lines
            run_params = {
                "command": "for i in {1..5}; do echo \"Line $i\"; done",
                "io_timeout": 10.0
            }
            run_result = await client.call_tool("ssh_run", run_params)
            
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
        finally:
            await disconnect_ssh(client)
            logger.info("SSH connection for multiline test cleaned up")
    
    print_test_footer()

@pytest.mark.asyncio
async def test_ssh_run_failure(mcp_test_environment):
    """Test command execution with a failing command."""
    print_test_header("Testing 'ssh_run' failure command")
    logger.info("Starting SSH run failure test")
    
    async with Client(mcp) as client:
        try:
            # Ensure connection is established
            assert await make_connection(client), "Failed to establish SSH connection"
            logger.info("SSH connection established or verified for failure test")
            
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
        finally:
            await disconnect_ssh(client)
            logger.info("SSH connection for failure test cleaned up")
            
    print_test_footer()
