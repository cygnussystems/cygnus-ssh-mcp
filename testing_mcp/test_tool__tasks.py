import pytest
import json
import logging
import time
from conftest import print_test_header, print_test_footer, make_connection, disconnect_ssh
from mcp_ssh_server import mcp
from fastmcp import Client

# Configure logging
logger = logging.getLogger(__name__)

@pytest.mark.asyncio
async def test_ssh_launch_task(mcp_test_environment):
    """Test launching background tasks, checking status, and killing tasks."""
    print_test_header("Testing 'ssh_launch_task', 'ssh_task_status', 'ssh_task_kill' tools")
    logger.info("Starting SSH task management test")
    
    async with Client(mcp) as client:
        try:
            # Ensure we have a connection
            assert await make_connection(client), "Failed to establish SSH connection"
            logger.info("SSH connection established for task management test")
            
            # Test launching a background task
            logger.info("Launching a background task")
            launch_params = {
                "command": "sleep 10 && echo 'Task completed' > /tmp/task_output.txt",
                "stdout_log": "/tmp/task_stdout.log",
                "stderr_log": "/tmp/task_stderr.log",
                "log_output": True
            }
            
            launch_result = await client.call_tool("ssh_launch_task", launch_params)
            logger.info(f"launch_task result: {launch_result}")
            
            # Verify launch_task result
            launch_json = json.loads(launch_result[0].text)
            assert 'pid' in launch_json, "Result should include PID"
            pid = launch_json['pid']
            assert isinstance(pid, int) and pid > 0, "PID should be a positive integer"
            
            # Test task_status
            logger.info(f"Checking status of task with PID {pid}")
            status_params = {
                "pid": pid
            }
            
            # Allow a brief moment for the process to be fully registered
            time.sleep(0.5)
            
            status_result = await client.call_tool("ssh_task_status", status_params)
            logger.info(f"task_status result: {status_result}")
            
            # Verify task_status result
            status_json = json.loads(status_result[0].text)
            assert 'status' in status_json, "Result should include status"
            assert status_json['status'] == 'running', f"Task should be running, got {status_json['status']}"
            
            # Test task_kill
            logger.info(f"Killing task with PID {pid}")
            kill_params = {
                "pid": pid,
                "signal": 15,  # SIGTERM
                "force": True, # This implies force_kill_signal=9 will be used if needed
                "wait_seconds": 1.0
            }
            
            kill_result = await client.call_tool("ssh_task_kill", kill_params)
            logger.info(f"task_kill result: {kill_result}")
            
            # Verify task_kill result
            kill_json = json.loads(kill_result[0].text)
            assert 'result' in kill_json, "Result should include result status"
            # Possible results: 'terminated', 'killed', 'not_found', 'error'
            assert kill_json['result'] in ['terminated', 'killed'], f"Kill result unexpected: {kill_json['result']}"
            
            # Check status again to confirm it's not running
            time.sleep(1)  # Give it a moment to process the kill
            status_result_after_kill = await client.call_tool("ssh_task_status", status_params)
            status_json_after_kill = json.loads(status_result_after_kill[0].text)
            assert status_json_after_kill['status'] != 'running', "Task should not be running after kill"
            
            # Clean up files created by the task
            logger.info("Cleaning up task output files")
            await client.call_tool("ssh_run", {
                "command": "rm -f /tmp/task_output.txt /tmp/task_stdout.log /tmp/task_stderr.log",
                "io_timeout": 5.0
            })
            
            logger.info("SSH task management test completed successfully")
        except Exception as e:
            logger.error(f"Error in SSH task management test: {e}", exc_info=True)
            raise
        finally:
            logger.info("Ensuring SSH connection is closed after task management test")
            await disconnect_ssh(client)
    
    print_test_footer()





@pytest.mark.asyncio
async def test_ssh_task_with_output(mcp_test_environment):
    """Test launching a task that produces output and verify that output."""
    print_test_header("Testing task with output")
    logger.info("Starting SSH task with output test")
        
    async with Client(mcp) as client:
        try:
            # Ensure we have a connection
            assert await make_connection(client), "Failed to establish SSH connection"
            logger.info("SSH connection established for task with output test")
            
            # Create a script that generates output
            script_content = """#!/bin/bash
for i in {1..3}; do
    echo "Line $i of output"
    sleep 0.5 # Reduced sleep for faster test
done
echo "Task completed successfully"
"""
            
            script_path = "/tmp/test_script.sh"
            output_log_path = "/tmp/script_output.log"
            error_log_path = "/tmp/script_error.log"

            # Create the script file using ssh_run
            logger.info(f"Creating test script: {script_path}")
            # Use cat with a heredoc to write the script content
            # Ensure the heredoc marker 'EOF' is not indented
            create_script_command = f"""
cat > {script_path} << 'EOF'
{script_content}
EOF
chmod +x {script_path}
"""
            await client.call_tool("ssh_run", {
                "command": create_script_command,
                "io_timeout": 10.0 # Increased timeout for script creation
            })
            
            # Launch the script as a background task
            logger.info(f"Launching script {script_path} as a background task")
            launch_params = {
                "command": script_path,
                "stdout_log": output_log_path,
                "stderr_log": error_log_path,
                "log_output": True
            }
            
            launch_result = await client.call_tool("ssh_launch_task", launch_params)
            launch_json = json.loads(launch_result[0].text)
            pid = launch_json['pid']
            logger.info(f"Launched task with PID: {pid}")
            
            # Wait for the script to complete (it takes about 1.5 seconds + overhead)
            logger.info("Waiting for script to complete...")
            max_wait_time = 10  # seconds
            poll_interval = 0.5 # seconds
            elapsed_time = 0
            task_completed = False
            while elapsed_time < max_wait_time:
                status_result = await client.call_tool("ssh_task_status", {"pid": pid})
                status_json = json.loads(status_result[0].text)
                if status_json['status'] != 'running':
                    logger.info(f"Task completed with status: {status_json['status']}")
                    task_completed = True
                    break
                time.sleep(poll_interval)
                elapsed_time += poll_interval
            
            assert task_completed, f"Task did not complete within {max_wait_time} seconds."
            
            # Check the output file
            logger.info(f"Reading output log: {output_log_path}")
            cat_result = await client.call_tool("ssh_run", {
                "command": f"cat {output_log_path}",
                "io_timeout": 5.0
            })
            
            cat_json = json.loads(cat_result[0].text)
            script_output = cat_json['output']
            logger.info(f"Script output: {script_output}")
            
            # Verify the output contains expected lines
            assert "Line 1 of output" in script_output, "Expected output line 1 not found"
            assert "Line 3 of output" in script_output, "Expected output line 3 not found"
            assert "Task completed successfully" in script_output, "Task completion message not found"
            
            # Clean up script and log files
            logger.info("Cleaning up script and output files")
            cleanup_command = f"rm -f {script_path} {output_log_path} {error_log_path}"
            await client.call_tool("ssh_run", {
                "command": cleanup_command,
                "io_timeout": 5.0
            })
            
            logger.info("SSH task with output test completed successfully")
        except Exception as e:
            logger.error(f"Error in SSH task with output test: {e}", exc_info=True)
            raise
        finally:
            logger.info("Ensuring SSH connection is closed after task with output test")
            await disconnect_ssh(client)
    
    print_test_footer()
