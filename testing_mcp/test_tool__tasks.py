import pytest
import json
import logging
import time
from conftest import print_test_header, print_test_footer

# Configure logging
logger = logging.getLogger(__name__)

@pytest.mark.asyncio
async def test_ssh_launch_task():
    """Test launching background tasks."""
    print_test_header("Testing 'ssh_launch_task' tool")
    logger.info("Starting SSH task launch test")
    
    # Import necessary modules
    from mcp_ssh_server import mcp
    from fastmcp import Client
    
    # Use the Client context manager with the imported mcp instance
    async with Client(mcp) as client:
        # First, add the test server configuration
        from conftest import SSH_TEST_USER, SSH_TEST_PASSWORD, SSH_TEST_PORT
        
        try:
            # Ensure we have a connection
            try:
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
            
            # Test task_status
            logger.info(f"Checking status of task with PID {pid}")
            status_params = {
                "pid": pid
            }
            
            status_result = await client.call_tool("ssh_task_status", status_params)
            logger.info(f"task_status result: {status_result}")
            
            # Verify task_status result
            status_json = json.loads(status_result[0].text)
            assert 'status' in status_json, "Result should include status"
            assert status_json['status'] == 'running', "Task should be running"
            
            # Test task_kill
            logger.info(f"Killing task with PID {pid}")
            kill_params = {
                "pid": pid,
                "signal": 15,  # SIGTERM
                "force": True,
                "wait_seconds": 1.0
            }
            
            kill_result = await client.call_tool("ssh_task_kill", kill_params)
            logger.info(f"task_kill result: {kill_result}")
            
            # Verify task_kill result
            kill_json = json.loads(kill_result[0].text)
            assert 'result' in kill_json, "Result should include result status"
            
            # Check status again to confirm it's not running
            time.sleep(1)  # Give it a moment to process the kill
            status_result = await client.call_tool("ssh_task_status", status_params)
            status_json = json.loads(status_result[0].text)
            assert status_json['status'] != 'running', "Task should not be running after kill"
            
            # Clean up
            await client.call_tool("ssh_run", {
                "command": "rm -f /tmp/task_output.txt /tmp/task_stdout.log /tmp/task_stderr.log",
                "io_timeout": 5.0
            })
            logger.info("Cleaned up task output files")
            
            logger.info("SSH task management test completed successfully")
        except Exception as e:
            logger.error(f"Error in SSH task management test: {e}")
            raise
    
    print_test_footer()

@pytest.mark.asyncio
async def test_ssh_task_with_output():
    """Test launching a task that produces output."""
    print_test_header("Testing task with output")
    logger.info("Starting SSH task with output test")
    
    # Import necessary modules
    from mcp_ssh_server import mcp
    from fastmcp import Client
    
    # Use the Client context manager with the imported mcp instance
    async with Client(mcp) as client:
        # First, add the test server configuration
        from conftest import SSH_TEST_USER, SSH_TEST_PASSWORD, SSH_TEST_PORT
        
        try:
            # Ensure we have a connection
            try:
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
            
            # Create a script that generates output
            script_content = """#!/bin/bash
for i in {1..5}; do
    echo "Line $i of output"
    sleep 1
done
echo "Task completed successfully"
"""
            
            # Create the script file
            script_path = "/tmp/test_script.sh"
            await client.call_tool("ssh_run", {
                "command": f"cat > {script_path} << 'EOF'\n{script_content}\nEOF\nchmod +x {script_path}",
                "io_timeout": 5.0
            })
            logger.info(f"Created test script: {script_path}")
            
            # Launch the script as a background task
            logger.info("Launching script as a background task")
            launch_params = {
                "command": script_path,
                "stdout_log": "/tmp/script_output.log",
                "stderr_log": "/tmp/script_error.log",
                "log_output": True
            }
            
            launch_result = await client.call_tool("ssh_launch_task", launch_params)
            launch_json = json.loads(launch_result[0].text)
            pid = launch_json['pid']
            logger.info(f"Launched task with PID: {pid}")
            
            # Wait for the script to complete (it takes about 5 seconds)
            logger.info("Waiting for script to complete...")
            for _ in range(10):  # Try for up to 10 seconds
                status_result = await client.call_tool("ssh_task_status", {"pid": pid})
                status_json = json.loads(status_result[0].text)
                if status_json['status'] != 'running':
                    logger.info(f"Task completed with status: {status_json['status']}")
                    break
                time.sleep(1)
            
            # Check the output file
            cat_result = await client.call_tool("ssh_run", {
                "command": "cat /tmp/script_output.log",
                "io_timeout": 5.0
            })
            
            cat_json = json.loads(cat_result[0].text)
            logger.info(f"Script output: {cat_json['output']}")
            
            # Verify the output contains expected lines
            assert "Line 1 of output" in cat_json['output'], "Expected output line 1 not found"
            assert "Line 5 of output" in cat_json['output'], "Expected output line 5 not found"
            assert "Task completed successfully" in cat_json['output'], "Task completion message not found"
            
            # Clean up
            await client.call_tool("ssh_run", {
                "command": "rm -f /tmp/test_script.sh /tmp/script_output.log /tmp/script_error.log",
                "io_timeout": 5.0
            })
            logger.info("Cleaned up script and output files")
            
            logger.info("SSH task with output test completed successfully")
        except Exception as e:
            logger.error(f"Error in SSH task with output test: {e}")
            raise
    
    print_test_footer()
