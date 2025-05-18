import pytest
import json
import logging
import time
import asyncio
from conftest import print_test_header, print_test_footer, make_connection, disconnect_ssh, mcp_test_environment
from mcp_ssh_server import mcp
from fastmcp import Client

# Configure logging
logger = logging.getLogger(__name__)

@pytest.mark.asyncio
async def test_task_not_in_history(mcp_test_environment):
    """Test that background tasks don't appear in command history."""
    print_test_header("Testing task not appearing in command history")
    logger.info("Starting test to verify tasks don't appear in command history")

    async with Client(mcp) as client:
        try:
            # Ensure connection is established
            assert await make_connection(client), "Failed to establish SSH connection"
            logger.info("SSH connection established for task history test")
            
            # Get initial command history
            history_before = await client.call_tool("ssh_cmd_history", {})
            history_before_json = json.loads(history_before[0].text)
            initial_history_count = len(history_before_json)
            logger.info(f"Initial command history count: {initial_history_count}")
            
            # Launch a background task
            task_cmd = "sleep 5"
            task_result = await client.call_tool("ssh_task_launch", {
                "command": task_cmd
            })
            task_json = json.loads(task_result[0].text)
            task_pid = task_json.get('pid')
            logger.info(f"Launched task with PID: {task_pid}")
            
            # Wait a moment to ensure any history updates would have occurred
            await asyncio.sleep(1)
            
            # Get command history after launching task
            history_after = await client.call_tool("ssh_cmd_history", {})
            history_after_json = json.loads(history_after[0].text)
            after_history_count = len(history_after_json)
            logger.info(f"Command history count after task launch: {after_history_count}")
            
            # Verify task command is not in history
            assert after_history_count == initial_history_count, "Task should not appear in command history"
            
            # Run a regular command to verify history still works
            run_result = await client.call_tool("ssh_cmd_run", {
                "command": "echo 'This is a regular command'"
            })
            
            # Get history again
            history_final = await client.call_tool("ssh_cmd_history", {})
            history_final_json = json.loads(history_final[0].text)
            final_history_count = len(history_final_json)
            logger.info(f"Final command history count: {final_history_count}")
            
            # Verify regular command appears in history
            assert final_history_count == initial_history_count + 1, "Regular command should appear in history"
            
            # Wait for task to complete
            await asyncio.sleep(5)
            
            logger.info("Task history test completed successfully")
        except Exception as e:
            logger.error(f"Error in task history test: {e}")
            raise
        finally:
            await disconnect_ssh(client)
            logger.info("SSH connection for task history test cleaned up")
    
    print_test_footer()
