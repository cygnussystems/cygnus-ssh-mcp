import pytest
import json
import asyncio # Retained as pytest.mark.asyncio might use it or for general async context
import logging
import time
from conftest import (
    print_test_header, print_test_footer, make_connection, disconnect_ssh,
    mcp_test_environment, extract_result_text,
    echo_command, multiline_echo_command, failing_command, get_expected_exit_code_error,
    sleep_then_echo, long_running_command, skip_on_windows, windows_only, IS_WINDOWS,
    success_with_stderr_command
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

    Linux/macOS-only: relies on $$ self-reporting, a bash/sh builtin with no Windows
    equivalent the wrapped command can read (see test_ssh_cmd_run_windows_pid_is_real_and_live
    for the Windows counterpart, which proves realness/liveness instead of exact self-match).
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
async def test_ssh_runtime_timeout_kills_remote_process(mcp_test_environment):
    """Test that runtime_timeout actually terminates the remote process.

    Regression test: the existing test_ssh_runtime_timeout only checks elapsed time and
    that a *new* ssh_cmd_run call succeeds afterward - both pass whether or not the
    original remote process was ever killed, since each ssh_cmd_run opens its own fresh
    channel/process regardless of what else is running. That test alone did not catch
    runtime_timeout's kill silently failing 100% of the time due to a fake PID (see
    test_ssh_cmd_run_captures_real_remote_pid). This test instead independently asks the
    remote host directly whether the specific killed PID is still alive, via the
    cross-platform ssh_task_status tool (backed by the same SshClient.task_status(pid)
    check ssh_cmd_kill already uses) rather than a platform-specific shell command.

    On Windows this also covers the process-tree-orphan bug found 2026-07-03: the PID
    ssh_cmd_run reports is a cmd.exe *wrapper* process, not the real workload underneath
    it (cmd.exe /c <command> spawns the real work as a child rather than replacing
    itself - there's no exec() on Windows). Stop-Process on just the wrapper PID left
    the real process running as an orphan; runtime_timeout's kill now uses taskkill
    /F /T (tree kill) instead, verified live to actually terminate the whole tree.
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

            # Independent, cross-platform check: ask the remote host directly whether
            # that PID is still alive.
            task_status_result = await client.call_tool("ssh_task_status", {"pid": pid})
            task_status_json = json.loads(extract_result_text(task_status_result))
            assert task_status_json['status'] == 'exited', (
                f"Remote process {pid} is still alive after runtime_timeout - the kill "
                f"did not actually work: {task_status_json}"
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
@windows_only
async def test_ssh_cmd_run_windows_pid_is_real_and_live(mcp_test_environment):
    """Test that ssh_cmd_run's reported 'pid' on Windows is a real Windows PID.

    Windows counterpart to test_ssh_cmd_run_captures_real_remote_pid: Windows has no
    $$-equivalent a wrapped command can read to self-report its own PID (cmd.exe /c
    <command> spawns the command as a child rather than replacing itself), so instead
    of an exact self-match, this proves realness via ssh_task_status: the pid must
    resolve to 'exited' (a process that genuinely existed and finished), not
    'invalid'/'error'. A paramiko-channel-id fake PID (the pre-fix behavior) is a tiny
    sequential local counter with no relationship to any remote process - Get-Process
    on it would essentially never resolve to a real, since-exited Windows process.

    This checks status only AFTER completion, not mid-flight: ssh_cmd_run's handler
    calls the blocking SSH monitoring loop synchronously with no await, which
    monopolizes the single asyncio event loop for the whole command duration -
    verified live (2026-07-03) that no other MCP tool call, even from a second,
    independent Client(mcp) session, gets a response until it returns. Mid-flight
    liveness + live streaming + taskkill tree-kill are already verified independently
    via a real-thread/raw-paramiko script (see planning notes) bypassing this
    constraint entirely; this test covers what's actually achievable through the MCP
    layer's single-flight-per-command behavior.
    """
    print_test_header("Testing that ssh_cmd_run's Windows PID is real")
    logger.info("Starting Windows real-PID test")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            run_result = await client.call_tool("ssh_cmd_run", {
                "command": long_running_command(2, "windows pid liveness done"),
                "io_timeout": 15.0
            })
            result_json = json.loads(extract_result_text(run_result))
            assert result_json['status'] == 'success', f"Unexpected status: {result_json}"
            pid = result_json['pid']
            assert pid, "No pid captured for the command"

            post_status = await client.call_tool("ssh_task_status", {"pid": pid})
            post_json = json.loads(extract_result_text(post_status))
            assert post_json['status'] == 'exited', (
                f"pid {pid} should be reported as exited - a real Windows process that "
                f"existed and finished - got: {post_json} (a fake channel-id pid from "
                f"the pre-fix bug would essentially never satisfy this)"
            )

            logger.info("Windows real PID confirmed to have genuinely existed and exited")
        except Exception as e:
            logger.error(f"Error in Windows PID liveness test: {e}")
            raise
        finally:
            await disconnect_ssh(client)
            logger.info("SSH connection for Windows PID liveness test cleaned up")

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

    Updated 2026-07-04: io_timeout now hands monitoring off to a background thread
    instead of closing the channel (see ops/run.py's _handoff_to_background), so
    end_ts is no longer set at all until the command genuinely finishes - status now
    resolves to 'running' (definite - the background thread is actively watching it),
    not the old 'unknown_still_running' fallback (which existed for the case where
    monitoring had stopped and we only had a live PID-liveness guess to go on).
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
            assert status_json['status'] == 'running', (
                f"Expected 'running' while the command is still running remotely and "
                f"background monitoring is actively watching it, got "
                f"{status_json['status']!r} (this is the exact false 'completed' "
                f"misreport Bug 1 caused)"
            )
            assert status_json['exit_code'] is None, "exit_code must not be populated before real completion"

            logger.info("io_timeout correctly avoided misreporting completion")
        except Exception as e:
            logger.error(f"Error in io_timeout false-completion test: {e}")
            raise
        finally:
            # Let the background monitor thread from the still-running 6s sleep finish
            # naturally before disconnecting, so it doesn't log a spurious "connection
            # dropped" completion after this test's SSH client goes away underneath it.
            await asyncio.sleep(5.0)
            await disconnect_ssh(client)
            logger.info("SSH connection for io_timeout false-completion test cleaned up")

    print_test_footer()


@pytest.mark.asyncio
async def test_ssh_io_timeout_eventually_resolves_to_completed(mcp_test_environment):
    """Test that ssh_cmd_check_status eventually reports a terminal status once a command
    that hit io_timeout actually finishes remotely, instead of 'unknown_still_running'
    forever - originally fixed 2026-07-03 by live-checking task_status(pid) when
    monitoring had stopped without a confirmed exit code.

    Updated 2026-07-04: io_timeout no longer closes the channel at all - it hands
    monitoring off to a background thread (see ops/run.py's _handoff_to_background)
    that keeps draining output and watching for real completion. So the real exit
    code IS now recoverable - this resolves to 'completed' with the genuine exit
    code, not the old 'completed_exit_code_unknown' fallback (which only exists now
    for the rarer case of an unexpected error/lost connection during background
    monitoring).
    """
    print_test_header("Testing io_timeout eventually resolves to a terminal status once the remote command finishes")
    logger.info("Starting io_timeout eventual-resolution test")

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
            # now be reported as a terminal status, not 'unknown_still_running' forever.
            status_result = await client.call_tool("ssh_cmd_check_status", {
                "handle_id": handle_id,
                "wait_seconds": 6.0
            })
            status_json = json.loads(extract_result_text(status_result))
            assert status_json['status'] == 'completed', (
                f"Expected 'completed' with a real exit code after the command genuinely "
                f"finished (background monitoring should have captured it), got "
                f"{status_json['status']!r}"
            )
            assert status_json['exit_code'] == 0, (
                f"Expected the real exit code (0) to be recovered via background "
                f"monitoring, got {status_json['exit_code']!r}"
            )

            logger.info("io_timeout eventually resolved to a terminal status")
        except Exception as e:
            logger.error(f"Error in io_timeout eventual-completion test: {e}")
            raise
        finally:
            await disconnect_ssh(client)
            logger.info("SSH connection for io_timeout eventual-completion test cleaned up")

    print_test_footer()


def chatty_command(seconds: int, message: str) -> str:
    """A command that produces output every second for `seconds` seconds, then echoes
    `message` - unlike sleep_then_echo, this never goes quiet, so io_timeout (silence-
    based) should never fire on it, only wait_timeout (elapsed-based) can.
    """
    if IS_WINDOWS:
        return (
            f"powershell -Command \"for ($i=1; $i -le {seconds}; $i++) "
            f"{{ Write-Output \\\"tick-$i\\\"; Start-Sleep -Seconds 1 }}; Write-Output '{message}'\""
        )
    return f"for i in $(seq 1 {seconds}); do echo tick-$i; sleep 1; done; echo '{message}'"


@pytest.mark.asyncio
async def test_ssh_wait_timeout_fires_despite_active_output(mcp_test_environment):
    """Test that wait_timeout fires even while a command is actively producing output
    (unlike io_timeout, which only fires on silence), and that the remote command
    survives - same non-killing handoff to background monitoring as io_timeout.
    """
    print_test_header("Testing wait_timeout fires despite continuous output and does not kill the command")
    logger.info("Starting wait_timeout test")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            # This command emits output every second for 6s - io_timeout (60s) would
            # never fire on it, but wait_timeout (2s) should fire almost immediately
            # regardless of the ongoing chatter.
            run_result = await client.call_tool("ssh_cmd_run", {
                "command": chatty_command(6, "chatty done"),
                "io_timeout": 60.0,
                "wait_timeout": 2.0
            })
            result_json = json.loads(extract_result_text(run_result))
            assert result_json['status'] == 'wait_timeout', f"Unexpected status: {result_json}"
            assert result_json.get('still_running') is True
            handle_id = result_json['id']

            # Immediately after, the command is still genuinely running remotely -
            # background monitoring should report 'running', not a terminal status.
            status_result = await client.call_tool("ssh_cmd_check_status", {
                "handle_id": handle_id,
                "wait_seconds": 1.0
            })
            status_json = json.loads(extract_result_text(status_result))
            assert status_json['status'] == 'running', f"Unexpected status: {status_json}"

            # Wait past the command's real remaining runtime - background monitoring
            # should have captured the real exit code by now, same as io_timeout does.
            final_status_result = await client.call_tool("ssh_cmd_check_status", {
                "handle_id": handle_id,
                "wait_seconds": 6.0
            })
            final_status_json = json.loads(extract_result_text(final_status_result))
            assert final_status_json['status'] == 'completed', f"Unexpected status: {final_status_json}"
            assert final_status_json['exit_code'] == 0

            output_result = await client.call_tool("ssh_cmd_output", {"handle_id": handle_id})
            output_lines = json.loads(extract_result_text(output_result))
            assert any('chatty done' in line for line in output_lines), (
                f"Expected final output line to be recoverable via background monitoring, got: {output_lines}"
            )

            logger.info("wait_timeout correctly fired despite active output and did not kill the command")
        except Exception as e:
            logger.error(f"Error in wait_timeout test: {e}")
            raise
        finally:
            await disconnect_ssh(client)
            logger.info("SSH connection for wait_timeout test cleaned up")

    print_test_footer()


@pytest.mark.asyncio
async def test_ssh_cmd_kill_after_io_timeout(mcp_test_environment):
    """Test that a command which survived io_timeout can still be killed on purpose via
    ssh_cmd_kill - the LLM should be able to decide to end a backgrounded command early.
    """
    print_test_header("Testing ssh_cmd_kill works on a command that survived io_timeout")
    logger.info("Starting kill-after-io_timeout test")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            run_result = await client.call_tool("ssh_cmd_run", {
                "command": sleep_then_echo(30, "should never print"),
                "io_timeout": 1.0
            })
            result_json = json.loads(extract_result_text(run_result))
            assert result_json['status'] == 'io_timeout', f"Unexpected status: {result_json}"
            handle_id = result_json['id']
            pid = result_json['pid']
            assert pid, "No pid captured for the backgrounded command"

            kill_result = await client.call_tool("ssh_cmd_kill", {"handle_id": handle_id})
            kill_json = json.loads(extract_result_text(kill_result))
            assert kill_json['result'] in ('killed', 'already_exited'), f"Unexpected kill result: {kill_json}"

            # Give the background monitor thread a moment to notice the channel/process
            # is gone and finalize the handle.
            await asyncio.sleep(1.0)

            status_result = await client.call_tool("ssh_cmd_check_status", {
                "handle_id": handle_id,
                "wait_seconds": 0.5
            })
            status_json = json.loads(extract_result_text(status_result))
            assert status_json['status'] == 'killed', f"Unexpected status after kill: {status_json}"

            logger.info("ssh_cmd_kill correctly terminated a command that survived io_timeout")
        except Exception as e:
            logger.error(f"Error in kill-after-io_timeout test: {e}")
            raise
        finally:
            await disconnect_ssh(client)
            logger.info("SSH connection for kill-after-io_timeout test cleaned up")

    print_test_footer()


@pytest.mark.asyncio
@skip_on_windows
async def test_ssh_cwd_not_found_does_not_pollute_history(mcp_test_environment):
    """Test that a cwd_not_found failure doesn't leave a misleading entry in
    ssh_cmd_history. The cwd-validation wrapper is a real remote process with its
    own real PID and sentinel exit code (77) - before this fix, that leaked into
    history looking exactly like the user's command ('pwd') had actually run and
    exited with code 77, even though the response explicitly said it was never
    executed. Not implemented on Windows (raises a plain SshError there instead of
    CwdNotFound - cwd isn't supported on Windows at all), hence skip_on_windows.
    """
    print_test_header("Testing cwd_not_found does not pollute command history")
    logger.info("Starting cwd_not_found history-pollution regression test")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            # This test's connection/history is shared with other tests in the same
            # pytest run (make_connection reuses an already-open connection), so
            # compare exact handle-id sets rather than assuming empty/unique history -
            # a marker command text also avoids colliding with any legitimate prior
            # run of a similarly-named command elsewhere in the suite.
            history_before = await client.call_tool("ssh_cmd_history", {})
            history_before_json = json.loads(extract_result_text(history_before))
            ids_before = {entry['id'] for entry in history_before_json}

            marker_command = "pwd # cwd_not_found_history_test_20260704"
            run_result = await client.call_tool("ssh_cmd_run", {
                "command": marker_command,
                "cwd": "/tmp/definitely_does_not_exist_cwd_test_20260704"
            })
            result_json = json.loads(extract_result_text(run_result))
            assert result_json['status'] == 'cwd_not_found', f"Unexpected status: {result_json}"
            assert 'id' not in result_json, "cwd_not_found response should not hand out a handle id"

            history_after = await client.call_tool("ssh_cmd_history", {})
            history_after_json = json.loads(extract_result_text(history_after))
            ids_after = {entry['id'] for entry in history_after_json}

            assert ids_after == ids_before, (
                f"Expected the exact same set of history handle ids after a "
                f"cwd_not_found failure, got new ids {ids_after - ids_before} - a "
                f"phantom entry was added for the cwd-guard wrapper"
            )
            assert not any(entry['command'] == marker_command for entry in history_after_json), (
                "The cwd-guard wrapper's PID/exit code should not appear in history "
                "as if the marker command actually ran"
            )

            logger.info("cwd_not_found correctly left no trace in command history")
        except Exception as e:
            logger.error(f"Error in cwd_not_found history-pollution test: {e}")
            raise
        finally:
            await disconnect_ssh(client)
            logger.info("SSH connection for cwd_not_found history test cleaned up")

    print_test_footer()


@pytest.mark.asyncio
async def test_ssh_cmd_run_captures_stderr_on_success(mcp_test_environment):
    """Test that a successful (exit 0) command still returns its stderr output.
    Regression test: ssh_cmd_run used to drop stderr entirely on success - a
    command that exits 0 can still have written to stderr (warnings, progress
    meters, non-fatal messages), and before this fix that content was silently
    discarded rather than surfaced in the response's `stderr` field.
    """
    print_test_header("Testing ssh_cmd_run captures stderr on a successful command")
    logger.info("Starting stderr-on-success regression test")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            marker = "stderr_on_success_marker_20260705"
            run_result = await client.call_tool("ssh_cmd_run", {
                "command": success_with_stderr_command(marker),
                "io_timeout": 10.0
            })
            result_json = json.loads(extract_result_text(run_result))
            assert result_json['status'] == 'success', f"Unexpected status: {result_json}"
            assert result_json['exit_code'] == 0, f"Expected exit code 0, got {result_json['exit_code']}"
            assert 'stderr' in result_json, "Successful response should include a 'stderr' field"
            assert marker in result_json['stderr'], (
                f"Expected stderr content to be captured even on a successful command, "
                f"got stderr={result_json['stderr']!r} (this is the exact bug: stderr "
                f"used to be dropped entirely when exit_code was 0)"
            )

            # ssh_cmd_output's stream='stderr' param should retrieve the same content later.
            handle_id = result_json['id']
            output_result = await client.call_tool("ssh_cmd_output", {
                "handle_id": handle_id,
                "stream": "stderr"
            })
            output_lines = json.loads(extract_result_text(output_result))
            assert any(marker in line for line in output_lines), (
                f"Expected ssh_cmd_output(stream='stderr') to retrieve the same stderr "
                f"content, got: {output_lines}"
            )

            logger.info("stderr was correctly captured and retrievable for a successful command")
        except Exception as e:
            logger.error(f"Error in stderr-on-success test: {e}")
            raise
        finally:
            await disconnect_ssh(client)
            logger.info("SSH connection for stderr-on-success test cleaned up")

    print_test_footer()
