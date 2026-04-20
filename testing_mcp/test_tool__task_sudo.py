import pytest
import json
import time
from conftest import print_test_header, print_test_footer, make_connection, disconnect_ssh, extract_result_text, skip_on_windows

# Skip all tests in this module on Windows (sudo not available)
pytestmark = skip_on_windows
from cygnus_ssh_mcp.server import mcp
from fastmcp import Client


@pytest.mark.asyncio
async def test_ssh_task_operations_with_sudo(mcp_test_environment):
    """Test task operations that require sudo privileges (cross-platform with sudo support)."""
    print_test_header("Testing task operations with sudo")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            
            # Check if we have sudo access
            sudo_check = await client.call_tool("ssh_conn_verify_sudo", {})
            sudo_json = json.loads(extract_result_text(sudo_check))
            
            if not sudo_json['available']:
                print("Skipping sudo tests as sudo is not available")
                return
            
            # Launch a task with sudo that creates a file in a protected location
            protected_file = "/var/tmp/sudo_task_test.txt"
            
            # Launch task with sudo
            launch_result = await client.call_tool("ssh_task_launch", {
                "command": f"echo 'This file was created with sudo' > {protected_file} && sleep 2",
                "use_sudo": True,
                "log_output": True
            })
            launch_json = json.loads(extract_result_text(launch_result))
            assert 'pid' in launch_json, f"Failed to launch task with sudo: {launch_json}"
            pid = launch_json['pid']
            
            # Wait for the task to complete
            await client.call_tool("ssh_cmd_check_status", {
                "handle_id": pid,
                "wait_seconds": 3.0
            })
            
            # Check task status
            status_result = await client.call_tool("ssh_task_status", {
                "pid": pid
            })
            status_json = json.loads(extract_result_text(status_result))
            assert status_json['status'] in ['exited', 'invalid'], f"Task should have completed, status: {status_json['status']}"
            
            # Verify the file was created
            verify_result = await client.call_tool("ssh_cmd_run", {
                "command": f"cat {protected_file}",
                "use_sudo": True
            })
            verify_json = json.loads(extract_result_text(verify_result))
            assert verify_json['status'] == 'success', "Failed to verify file creation"
            assert "This file was created with sudo" in verify_json['output'], "File content doesn't match expected"
            
            # Launch another task to test kill with sudo
            long_task_result = await client.call_tool("ssh_task_launch", {
                "command": "sleep 30",
                "use_sudo": True
            })
            long_task_json = json.loads(extract_result_text(long_task_result))
            long_task_pid = long_task_json['pid']
            
            # Verify task is running
            status_result = await client.call_tool("ssh_task_status", {
                "pid": long_task_pid
            })
            status_json = json.loads(extract_result_text(status_result))
            assert status_json['status'] == 'running', "Long task should be running"
            
            # Kill the task with sudo
            kill_result = await client.call_tool("ssh_task_kill", {
                "pid": long_task_pid,
                "signal": 15,
                "use_sudo": True
            })
            kill_json = json.loads(extract_result_text(kill_result))
            assert kill_json['result'] in ['killed', 'terminated'], f"Failed to kill task with sudo: {kill_json}"
            
            # Verify task was killed
            status_after_kill = await client.call_tool("ssh_task_status", {
                "pid": long_task_pid
            })
            status_after_json = json.loads(extract_result_text(status_after_kill))
            assert status_after_json['status'] != 'running', f"Task should not be running after kill, status: {status_after_json['status']}"
            
        finally:
            # Clean up
            await client.call_tool("ssh_cmd_run", {
                "command": f"rm -f {protected_file}",
                "use_sudo": True,
                "io_timeout": 5.0
            })
            await disconnect_ssh(client)
    
    print_test_footer()
