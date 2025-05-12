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




@pytest.mark.asyncio
async def test_ssh_wait_and_check(mcp_test_environment):
    """Test waiting and checking command status."""
    print_test_header("Testing 'ssh_wait_and_check' tool")
    logger.info("Starting SSH wait and check test")
    
    async with Client(mcp) as client:
        try:
            # Ensure connection is established
            assert await make_connection(client), "Failed to establish SSH connection"
            logger.info("SSH connection established for wait and check test")
            
            # First run a long-running command
            run_params = {
                "command": "sleep 3 && echo 'Command completed'",
                "io_timeout": 10.0
            }
            
            # Run the command
            run_result = await client.call_tool("ssh_run", run_params)
            assert run_result is not None, "Expected non-empty result"
            result_json = json.loads(run_result[0].text)
            
            # The handle ID might be in different fields depending on implementation
            # Try to find it in various possible locations
            handle_id = None
            if 'id' in result_json:
                handle_id = result_json['id']
            elif 'handle_id' in result_json:
                handle_id = result_json['handle_id']
            else:
                # If we can't find a specific ID field, we can use command history to get the latest command
                history_result = await client.call_tool("ssh_command_history", {"limit": 1, "reverse": True})
                history_json = json.loads(history_result[0].text)
                if history_json and len(history_json) > 0:
                    handle_id = history_json[0]['id']
            
            assert handle_id is not None, "Could not determine handle ID from result or history"
            logger.info(f"Command executed with handle ID: {handle_id}")
            
            # Now test the wait_and_check tool with a short wait
            wait_params = {
                "handle_id": handle_id,
                "wait_seconds": 1.0  # Wait for 1 second
            }
            
            # Call the wait_and_check tool
            wait_result = await client.call_tool("ssh_wait_and_check", wait_params)
            assert wait_result is not None, "Expected non-empty result"
            wait_json = json.loads(wait_result[0].text)
            logger.info(f"Wait and check result: {wait_json}")
            
            # Verify the result
            assert wait_json['handle_id'] == handle_id, "Handle ID should match"
            assert wait_json['waited_seconds'] == 1.0, "Wait time should be 1.0 seconds"
            assert 'status' in wait_json, "Status should be included in result"
            assert 'timestamp' in wait_json, "Timestamp should be included in result"
            
            # Wait for the command to complete
            wait_params = {
                "handle_id": handle_id,
                "wait_seconds": 3.0  # Wait for 3 seconds (should be enough for the command to complete)
            }
            
            # Call the wait_and_check tool again
            wait_result = await client.call_tool("ssh_wait_and_check", wait_params)
            wait_json = json.loads(wait_result[0].text)
            logger.info(f"Second wait and check result: {wait_json}")
            
            # Verify the command completed
            assert wait_json['status'] == 'completed', "Command should be completed after waiting"
            assert wait_json['exit_code'] == 0, "Exit code should be 0"
            
            logger.info("SSH wait and check test completed successfully")
        except Exception as e:
            logger.error(f"Error in SSH wait and check test: {e}")
            raise
        finally:
            await disconnect_ssh(client)
            logger.info("SSH connection for wait and check test cleaned up")
            
    print_test_footer()






@pytest.mark.asyncio
async def test_ssh_busy_lock(mcp_test_environment):
    """Test that attempting to run a command while another is running raises BusyError."""
    print_test_header("Testing SSH busy lock mechanism")
    logger.info("Starting SSH busy lock test")
    
    async with Client(mcp) as client:
        try:
            # Ensure connection is established
            assert await make_connection(client), "Failed to establish SSH connection"
            logger.info("SSH connection established for busy lock test")
            
            # Run a long-running command (10 seconds)
            long_command_params = {
                "command": "sleep 10 && echo 'Long command completed'",
                "io_timeout": 15.0
            }
            
            # Start the long-running command but don't await it
            long_command_task = asyncio.create_task(client.call_tool("ssh_run", long_command_params))
            
            # Give the command a moment to start
            await asyncio.sleep(1.0)
            
            # Try to run another command while the first is still running
            second_command_params = {
                "command": "echo 'This should fail due to busy lock'",
                "io_timeout": 5.0
            }
            
            # This should raise an exception due to the busy lock
            logger.info("Attempting to run second command while first is still running")
            try:
                await client.call_tool("ssh_run", second_command_params)
                # If we get here, the test failed
                assert False, "Expected BusyError was not raised"
            except Exception as e:
                # Verify that the exception is related to the busy lock
                error_message = str(e)
                logger.info(f"Received expected error: {error_message}")
                assert "busy" in error_message.lower() or "another command" in error_message.lower(), \
                    f"Expected busy lock error, got: {error_message}"
                logger.info("Successfully detected busy lock error")
            
            # Clean up the long-running command
            try:
                # Cancel the task to avoid waiting for it to complete
                long_command_task.cancel()
                await asyncio.sleep(0.5)  # Give it a moment to cancel
            except asyncio.CancelledError:
                pass
            
            logger.info("SSH busy lock test completed successfully")
        except Exception as e:
            logger.error(f"Error in SSH busy lock test: {e}")
            raise
        finally:
            # Ensure we disconnect even if there was an error
            await disconnect_ssh(client)
            logger.info("SSH connection for busy lock test cleaned up")
            
    print_test_footer()
