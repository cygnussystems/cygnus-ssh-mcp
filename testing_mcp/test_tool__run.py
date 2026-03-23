import pytest
import json
import asyncio # Retained as pytest.mark.asyncio might use it or for general async context
import logging
import time
from conftest import print_test_header, print_test_footer, make_connection, disconnect_ssh, mcp_test_environment, extract_result_text
# Import necessary modules
from cygnus_ssh_mcp.server import mcp
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
            run_result = await client.call_tool("ssh_cmd_run", run_params)
            
            # Verify the result
            assert run_result is not None, "Expected non-empty result"

            # Parse the JSON response
            result_json = json.loads(extract_result_text(run_result))
            logger.info(f"Command result: {result_json}")
            
            assert result_json['status'] == 'success', f"Expected status 'success', got {result_json.get('status')}"
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
            run_result = await client.call_tool("ssh_cmd_run", run_params)
            
            # Verify the result
            assert run_result is not None, "Expected non-empty result"

            # Parse the JSON response
            result_json = json.loads(extract_result_text(run_result))
            logger.info(f"Multi-line command result: {result_json}")
            
            # Verify the result
            assert result_json['status'] == 'success', f"Expected status 'success', got {result_json.get('status')}"
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
            run_result = await client.call_tool("ssh_cmd_run", run_params)
            
            # Verify the result
            assert run_result is not None, "Expected non-empty result"
            result_json = json.loads(extract_result_text(run_result))
            logger.info(f"Command failure result: {result_json}")
            
            # Verify the failure status
            assert result_json['status'] == 'command_failed', f"Expected status 'command_failed', got {result_json.get('status')}"
            assert result_json['exit_code'] == 42, f"Expected exit code 42, got {result_json.get('exit_code')}"
            assert "exit code 42" in result_json.get('error', ''), "Error message should mention exit code 42"
            
            logger.info("SSH run failure test completed successfully")
        except Exception as e:
            logger.error(f"Error in SSH run failure test: {e}")
            raise
        finally:
            await disconnect_ssh(client)
            logger.info("SSH connection for failure test cleaned up")
            
    print_test_footer()




@pytest.mark.asyncio
async def test_ssh_cmd_check(mcp_test_environment):
    """Test waiting and checking command status."""
    print_test_header("Testing 'ssh_cmd_check_status' tool")
    logger.info("Starting SSH command check test")
    
    async with Client(mcp) as client:
        try:
            # Ensure connection is established
            assert await make_connection(client), "Failed to establish SSH connection"
            logger.info("SSH connection established for command check test")
            
            # First run a long-running command
            run_params = {
                "command": "sleep 3 && echo 'Command completed'",
                "io_timeout": 10.0
            }
            
            # Run the command
            run_result = await client.call_tool("ssh_cmd_run", run_params)
            assert run_result is not None, "Expected non-empty result"
            result_json = json.loads(extract_result_text(run_result))

            # The handle ID might be in different fields depending on implementation
            # Try to find it in various possible locations
            handle_id = None
            if 'id' in result_json:
                handle_id = result_json['id']
            elif 'handle_id' in result_json:
                handle_id = result_json['handle_id']
            else:
                # If we can't find a specific ID field, we can use command history to get the latest command
                history_result = await client.call_tool("ssh_cmd_history", {"limit": 1, "reverse": True})
                history_json = json.loads(extract_result_text(history_result))
                if history_json and len(history_json) > 0:
                    handle_id = history_json[0]['id']
            
            assert handle_id is not None, "Could not determine handle ID from result or history"
            logger.info(f"Command executed with handle ID: {handle_id}")
            
            # Now test the cmd_check tool with a short wait
            check_params = {
                "handle_id": handle_id,
                "wait_seconds": 1.0  # Wait for 1 second
            }
            
            # Call the cmd_check tool
            check_result = await client.call_tool("ssh_cmd_check_status", check_params)
            assert check_result is not None, "Expected non-empty result"
            check_json = json.loads(extract_result_text(check_result))
            logger.info(f"Command check result: {check_json}")
            
            # Verify the result
            assert check_json['handle_id'] == handle_id, "Handle ID should match"
            assert check_json['waited_seconds'] == 1.0, "Wait time should be 1.0 seconds"
            assert 'status' in check_json, "Status should be included in result"
            assert 'timestamp' in check_json, "Timestamp should be included in result"
            
            # Wait for the command to complete
            check_params = {
                "handle_id": handle_id,
                "wait_seconds": 3.0  # Wait for 3 seconds (should be enough for the command to complete)
            }
            
            # Call the cmd_check tool again
            check_result = await client.call_tool("ssh_cmd_check_status", check_params)
            check_json = json.loads(extract_result_text(check_result))
            logger.info(f"Second command check result: {check_json}")
            
            # Verify the command completed
            assert check_json['status'] == 'completed', "Command should be completed after waiting"
            assert check_json['exit_code'] == 0, "Exit code should be 0"
            
            logger.info("SSH command check test completed successfully")
        except Exception as e:
            logger.error(f"Error in SSH command check test: {e}")
            raise
        finally:
            await disconnect_ssh(client)
            logger.info("SSH connection for command check test cleaned up")
            
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
            long_command_task = asyncio.create_task(client.call_tool("ssh_cmd_run", long_command_params))
            
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
                await client.call_tool("ssh_cmd_run", second_command_params)
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


@pytest.mark.asyncio
async def test_ssh_runtime_timeout(mcp_test_environment):
    """Test that a command is automatically killed when it exceeds runtime_timeout."""
    print_test_header("Testing SSH runtime timeout mechanism")
    logger.info("Starting SSH runtime timeout test")
    
    async with Client(mcp) as client:
        try:
            # Ensure connection is established
            assert await make_connection(client), "Failed to establish SSH connection"
            logger.info("SSH connection established for runtime timeout test")
            
            # Run a command that should take 10 seconds, but set a 3 second runtime timeout
            run_params = {
                "command": "echo 'Starting long process'; sleep 10; echo 'This should never be printed'",
                "io_timeout": 15.0,
                "runtime_timeout": 3.0  # This should cause the command to be killed after 3 seconds
            }
            
            # The command should be killed automatically due to runtime_timeout
            start_time = time.time()
            logger.info("Running command with runtime_timeout=3.0s")
            
            run_result = await client.call_tool("ssh_cmd_run", run_params)
            end_time = time.time()
            elapsed_time = end_time - start_time
            
            # Verify the result
            assert run_result is not None, "Expected non-empty result"
            result_json = json.loads(extract_result_text(run_result))
            logger.info(f"Runtime timeout result: {result_json}")

            # Verify the timeout status
            assert result_json['status'] == 'runtime_timeout', f"Expected status 'runtime_timeout', got {result_json.get('status')}"
            assert 'id' in result_json, "Handle ID should be included in result"
            assert 'timeout_seconds' in result_json, "Timeout seconds should be included in result"
            assert result_json['timeout_seconds'] == 3.0, f"Expected timeout of 3.0s, got {result_json.get('timeout_seconds')}"
            
            # Check that the command was killed within a reasonable time of the timeout
            # Allow for some overhead in the timeout mechanism
            assert 2.5 <= elapsed_time <= 5.0, \
                f"Command should have been killed after ~3s, but took {elapsed_time:.2f}s"
            
            logger.info(f"Command was correctly terminated after {elapsed_time:.2f}s")
            
            # Verify we can run another command now that the previous one was killed
            logger.info("Verifying we can run another command after timeout")
            verify_params = {
                "command": "echo 'System is responsive again'",
                "io_timeout": 5.0
            }
            
            verify_result = await client.call_tool("ssh_cmd_run", verify_params)
            verify_json = json.loads(extract_result_text(verify_result))

            assert verify_json['status'] == 'success', "Follow-up command should succeed"
            assert verify_json['exit_code'] == 0, "Follow-up command should have exit code 0"
            assert "System is responsive again" in verify_json['output'], "Expected output not found"

            logger.info("SSH runtime timeout test completed successfully")
        except Exception as e:
            logger.error(f"Error in SSH runtime timeout test: {e}")
            raise
        finally:
            # Ensure we disconnect even if there was an error
            await disconnect_ssh(client)
            logger.info("SSH connection for runtime timeout test cleaned up")
            
    print_test_footer()


@pytest.mark.asyncio
async def test_ssh_manual_interrupt(mcp_test_environment):
    """Test manually interrupting a running command after a runtime timeout."""
    print_test_header("Testing manual interruption of a running command with runtime timeout")
    logger.info("Starting SSH manual interrupt test")
    
    async with Client(mcp) as client:
        try:
            # Ensure connection is established
            assert await make_connection(client), "Failed to establish SSH connection"
            logger.info("SSH connection established for manual interrupt test")
            
            # Start a long-running command with a short runtime timeout
            # This will timeout but the process will still be running in the background
            run_params = {
                "command": "echo 'Starting long process'; sleep 30; echo 'This should never be printed'",
                "io_timeout": 60.0,  # Standard IO timeout
                "runtime_timeout": 3.0  # Short runtime timeout to trigger quickly
            }
            
            # The command should timeout due to exceeding runtime
            start_time = time.time()
            logger.info("Running command with runtime_timeout=3.0s")
            
            run_result = await client.call_tool("ssh_cmd_run", run_params)
            end_time = time.time()
            elapsed_time = end_time - start_time
            
            # Verify the result
            assert run_result is not None, "Expected non-empty result"
            result_json = json.loads(extract_result_text(run_result))
            logger.info(f"Runtime timeout result: {result_json}")

            # Verify the timeout status
            assert result_json['status'] == 'runtime_timeout', f"Expected status 'runtime_timeout', got {result_json.get('status')}"

            # Get the handle ID directly from the result
            handle_id = result_json.get('id')
            assert handle_id is not None, "Handle ID should be included in result"
            logger.info(f"Got handle ID from result: {handle_id}")
            
            logger.info(f"Command timed out as expected after {elapsed_time:.2f}s")
            
            # Verify the handle ID was found
            assert handle_id is not None, "Could not determine handle ID of the command"
            
            # Check if the command is still running using ssh_cmd_check_status
            check_params = {
                "handle_id": handle_id,
                "wait_seconds": 1.0  # Short wait
            }
            check_result = await client.call_tool("ssh_cmd_check_status", check_params)
            check_json = json.loads(extract_result_text(check_result))

            logger.info(f"Command status before kill: {check_json}")
            
            # The process might still be running even though the command timed out
            # Now manually kill the command using ssh_cmd_kill
            kill_params = {
                "handle_id": handle_id,
                "signal": 15,  # SIGTERM
                "force": True
            }
            
            kill_result = await client.call_tool("ssh_cmd_kill", kill_params)
            kill_json = json.loads(extract_result_text(kill_result))

            logger.info(f"Kill result: {kill_json}")
            assert kill_json['result'] in ['killed', 'terminated', 'not_running'], \
                f"Command should be successfully killed, got: {kill_json['result']}"
            
            # Verify the command is no longer running
            await asyncio.sleep(1)  # Give it a moment to update
            check_result = await client.call_tool("ssh_cmd_check_status", check_params)
            check_json = json.loads(extract_result_text(check_result))

            logger.info(f"Command status after kill: {check_json}")
            assert check_json['status'] in ['completed', 'not_found'], \
                f"Command should no longer be running, status: {check_json['status']}"
            
            # Verify we can run another command now
            logger.info("Verifying we can run another command after manual kill")
            verify_params = {
                "command": "echo 'System is responsive after manual kill'",
                "io_timeout": 5.0
            }
            
            verify_result = await client.call_tool("ssh_cmd_run", verify_params)
            verify_json = json.loads(extract_result_text(verify_result))

            assert verify_json['exit_code'] == 0, "Follow-up command should succeed"
            assert "System is responsive after manual kill" in verify_json['output'], "Expected output not found"

            logger.info("SSH manual interrupt test completed successfully")
        except Exception as e:
            logger.error(f"Error in SSH manual interrupt test: {e}")
            raise
        finally:
            # Ensure we disconnect even if there was an error
            await disconnect_ssh(client)
            logger.info("SSH connection for manual interrupt test cleaned up")
            
    print_test_footer()
