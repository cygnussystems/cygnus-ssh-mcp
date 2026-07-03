import pytest
import json
import asyncio # Retained as pytest.mark.asyncio might use it or for general async context
import logging
import time
from conftest import (
    print_test_header, print_test_footer, make_connection, disconnect_ssh,
    mcp_test_environment, extract_result_text,
    echo_command, multiline_echo_command, failing_command, get_expected_exit_code_error,
    sleep_then_echo, long_running_command, skip_on_windows
)

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

            # Simple echo command (cross-platform)
            run_params = {
                "command": echo_command("Hello from MCP SSH!"),
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

            # Test with a command that produces multiple lines (cross-platform)
            run_params = {
                "command": multiline_echo_command(5),
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
            
            # Now run the failing command (cross-platform)
            run_params = {
                "command": failing_command(),
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
            assert get_expected_exit_code_error() in result_json.get('error', ''), "Error message should mention exit code 42"
            
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
            
            # First run a long-running command (cross-platform)
            run_params = {
                "command": sleep_then_echo(3, "Command completed"),
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
            
            # Run a long-running command (10 seconds) - cross-platform
            long_command_params = {
                "command": sleep_then_echo(10, "Long command completed"),
                "io_timeout": 15.0
            }
            
            # Start the long-running command but don't await it
            long_command_task = asyncio.create_task(client.call_tool("ssh_cmd_run", long_command_params))
            
            # Give the command a moment to start
            await asyncio.sleep(1.0)
            
            # Try to run another command while the first is still running (cross-platform)
            second_command_params = {
                "command": echo_command("This should fail due to busy lock"),
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
            
            # Run a command that should take 10 seconds, but set a 3 second runtime timeout (cross-platform)
            run_params = {
                "command": long_running_command(10, "This should never be printed"),
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
            
            # Verify we can run another command now that the previous one was killed (cross-platform)
            logger.info("Verifying we can run another command after timeout")
            verify_params = {
                "command": echo_command("System is responsive again"),
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

            # Start a long-running command with a short runtime timeout (cross-platform)
            # This will timeout but the process will still be running in the background
            run_params = {
                "command": long_running_command(30, "This should never be printed"),
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
            assert check_json['status'] in ['completed', 'killed', 'not_found'], \
                f"Command should no longer be running, status: {check_json['status']}"
            
            # Verify we can run another command now (cross-platform)
            logger.info("Verifying we can run another command after manual kill")
            verify_params = {
                "command": echo_command("System is responsive after manual kill"),
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


@pytest.mark.asyncio
@skip_on_windows
async def test_ssh_cmd_run_captures_real_remote_pid(mcp_test_environment):
    """Test that ssh_cmd_run's reported 'pid' is the real remote OS PID, not paramiko's
    local channel sequence number.

    Regression test: handle.pid was previously set from paramiko Channel.get_id(), a
    small local counter (0, 1, 2, ...) with no relationship to any process on the
    remote host. This silently broke runtime_timeout's kill and ssh_cmd_kill for every
    command, since 'kill <pid>' targeted a PID that didn't exist remotely - and it went
    undetected because nothing checked the pid's value against the shell's own
    self-reported PID, only that *a* number was present.
    """
    print_test_header("Testing that ssh_cmd_run captures a real remote PID")
    logger.info("Starting real PID capture test")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            # The shell reports its own PID via $$ - this must match the captured pid exactly.
            run_result = await client.call_tool("ssh_cmd_run", {
                "command": "echo SELF_PID:$$",
                "io_timeout": 10.0
            })
            result_json = json.loads(extract_result_text(run_result))
            assert result_json['status'] == 'success', f"Unexpected status: {result_json}"

            self_reported_line = next(
                line for line in result_json['output'].splitlines() if line.startswith('SELF_PID:')
            )
            self_reported_pid = int(self_reported_line.split(':', 1)[1].strip())

            assert result_json['pid'] == self_reported_pid, (
                f"handle pid {result_json['pid']} does not match the shell's own $$ "
                f"({self_reported_pid}) - pid capture may have regressed to a "
                f"paramiko-local channel id instead of the real remote PID"
            )

            # A second command's self-reported pid must independently match too - proves
            # this isn't a coincidental match, and that pid capture is consistent call
            # over call (paramiko channel ids would also be consistent-but-wrong, so the
            # self-reported-$$ comparison, not the raw pid values, is what's decisive here).
            run_result_2 = await client.call_tool("ssh_cmd_run", {
                "command": "echo SELF_PID:$$",
                "io_timeout": 10.0
            })
            result_json_2 = json.loads(extract_result_text(run_result_2))
            self_reported_line_2 = next(
                line for line in result_json_2['output'].splitlines() if line.startswith('SELF_PID:')
            )
            self_reported_pid_2 = int(self_reported_line_2.split(':', 1)[1].strip())
            assert result_json_2['pid'] == self_reported_pid_2, (
                f"Second command's handle pid {result_json_2['pid']} does not match its own "
                f"$$ ({self_reported_pid_2})"
            )

            logger.info("Real PID capture confirmed correct")
        except Exception as e:
            logger.error(f"Error in PID capture test: {e}")
            raise
        finally:
            await disconnect_ssh(client)
            logger.info("SSH connection for PID capture test cleaned up")

    print_test_footer()


@pytest.mark.asyncio
@skip_on_windows
async def test_ssh_runtime_timeout_kills_remote_process(mcp_test_environment):
    """Test that runtime_timeout actually terminates the remote process.

    Regression test: the existing test_ssh_runtime_timeout only checks elapsed time and
    that a *new* ssh_cmd_run call succeeds afterward - both pass whether or not the
    original remote process was ever killed, since each ssh_cmd_run opens its own fresh
    channel/process regardless of what else is running. That test alone did not catch
    runtime_timeout's kill silently failing 100% of the time due to a fake PID (see
    test_ssh_cmd_run_captures_real_remote_pid). This test instead independently asks the
    remote host directly whether the specific killed PID is still alive.
    """
    print_test_header("Testing that runtime_timeout actually kills the remote process")
    logger.info("Starting runtime_timeout actual-kill test")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            run_result = await client.call_tool("ssh_cmd_run", {
                "command": long_running_command(30, "should never print"),
                "io_timeout": 60.0,
                "runtime_timeout": 3.0
            })
            result_json = json.loads(extract_result_text(run_result))
            assert result_json['status'] == 'runtime_timeout', f"Unexpected status: {result_json}"
            pid = result_json['pid']
            assert pid, "No pid captured for the timed-out command"

            # Give the kill signal a moment to actually land on the remote host.
            await asyncio.sleep(1.0)

            # Independent check: ask the remote host directly whether that PID is still alive.
            check_result = await client.call_tool("ssh_cmd_run", {
                "command": f"kill -0 {pid} 2>/dev/null && echo STILL_ALIVE || echo GONE",
                "io_timeout": 10.0
            })
            check_json = json.loads(extract_result_text(check_result))
            assert "GONE" in check_json['output'], (
                f"Remote process {pid} is still alive after runtime_timeout - the kill "
                f"did not actually work: {check_json['output']!r}"
            )

            # ssh_cmd_check_status should also reflect this as a terminal state, not
            # 'unknown_still_running' forever (there's nothing left to poll for).
            status_result = await client.call_tool("ssh_cmd_check_status", {
                "handle_id": result_json['id'],
                "wait_seconds": 1.0
            })
            status_json = json.loads(extract_result_text(status_result))
            assert status_json['status'] == 'killed', (
                f"Expected status 'killed' after a confirmed runtime_timeout kill, got "
                f"{status_json['status']!r}"
            )

            logger.info("runtime_timeout confirmed to actually kill the remote process")
        except Exception as e:
            logger.error(f"Error in runtime_timeout kill test: {e}")
            raise
        finally:
            await disconnect_ssh(client)
            logger.info("SSH connection for runtime_timeout kill test cleaned up")

    print_test_footer()


@pytest.mark.asyncio
async def test_ssh_io_timeout_does_not_misreport_completion(mcp_test_environment):
    """Test that io_timeout does not falsely report a still-running command as completed.

    Regression test for the original Bug 1: ssh_cmd_check_status inferred completion
    from end_ts being set, but end_ts is also set when io_timeout fires (monitoring
    stops without killing the remote command). The existing test_ssh_cmd_check test set
    io_timeout (10s) far longer than its command's runtime (3s), so io_timeout could
    never actually fire - it exercised the already-completed path only, never the exact
    code path Bug 1 lived in. This test deliberately sets io_timeout shorter than the
    command's runtime so io_timeout genuinely fires while the command is still running.
    """
    print_test_header("Testing io_timeout does not misreport a running command as completed")
    logger.info("Starting io_timeout false-completion regression test")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            # io_timeout (2s) is deliberately shorter than the command's runtime (6s),
            # so io_timeout must fire while the remote command is still genuinely running.
            run_result = await client.call_tool("ssh_cmd_run", {
                "command": sleep_then_echo(6, "Command completed"),
                "io_timeout": 2.0
            })
            result_json = json.loads(extract_result_text(run_result))
            assert result_json['status'] == 'io_timeout', f"Unexpected status: {result_json}"
            assert result_json.get('still_running') is True
            handle_id = result_json['id']

            # Checking immediately (command is still running remotely) must NOT report
            # 'completed' - that was the exact false-positive Bug 1 caused.
            status_result = await client.call_tool("ssh_cmd_check_status", {
                "handle_id": handle_id,
                "wait_seconds": 1.0
            })
            status_json = json.loads(extract_result_text(status_result))
            assert status_json['status'] == 'unknown_still_running', (
                f"Expected 'unknown_still_running' while the command is still running "
                f"remotely, got {status_json['status']!r} (this is the exact false "
                f"'completed' misreport Bug 1 caused)"
            )
            assert status_json['exit_code'] is None, "exit_code must not be populated before real completion"

            logger.info("io_timeout correctly avoided misreporting completion")
        except Exception as e:
            logger.error(f"Error in io_timeout false-completion test: {e}")
            raise
        finally:
            await disconnect_ssh(client)
            logger.info("SSH connection for io_timeout false-completion test cleaned up")

    print_test_footer()


@pytest.mark.asyncio
@pytest.mark.xfail(
    reason=(
        "Known gap (2026-07-03): once io_timeout fires, the local channel is closed and "
        "ssh_cmd_check_status never reconnects to observe real completion, so it stays "
        "'unknown_still_running' forever even though the remote command finishes fine on "
        "its own (verified independently via a live kill -0 check). Planned fix, in order: "
        "(1) Windows real PID capture for ssh_cmd_run, then (2) wire ssh_cmd_check_status "
        "to the existing cross-platform SshClient.task_status(pid) liveness check already "
        "used by ssh_task_status/ssh_cmd_kill. See "
        "planning/2026-07-03-session-summary-and-next-steps.md. Remove this xfail once done."
    ),
    strict=True
)
async def test_ssh_io_timeout_eventually_resolves_to_completed(mcp_test_environment):
    """Test that ssh_cmd_check_status eventually reports 'completed' once a command that
    hit io_timeout actually finishes remotely - the promise its own docstring makes
    ("call this repeatedly... until status='completed'"). Currently false: see xfail reason.
    """
    print_test_header("Testing io_timeout eventually resolves to completed once the remote command finishes")
    logger.info("Starting io_timeout eventual-completion test")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            run_result = await client.call_tool("ssh_cmd_run", {
                "command": sleep_then_echo(6, "Command completed"),
                "io_timeout": 2.0
            })
            result_json = json.loads(extract_result_text(run_result))
            assert result_json['status'] == 'io_timeout', f"Unexpected status: {result_json}"
            handle_id = result_json['id']

            # Wait past the command's real remaining runtime, then check - it should
            # now genuinely be reported as completed with the real exit code.
            status_result = await client.call_tool("ssh_cmd_check_status", {
                "handle_id": handle_id,
                "wait_seconds": 6.0
            })
            status_json = json.loads(extract_result_text(status_result))
            assert status_json['status'] == 'completed', (
                f"Expected 'completed' after the command genuinely finished, got {status_json['status']!r}"
            )
            assert status_json['exit_code'] == 0

            logger.info("io_timeout eventually resolved to completed")
        except Exception as e:
            logger.error(f"Error in io_timeout eventual-completion test: {e}")
            raise
        finally:
            await disconnect_ssh(client)
            logger.info("SSH connection for io_timeout eventual-completion test cleaned up")

    print_test_footer()
