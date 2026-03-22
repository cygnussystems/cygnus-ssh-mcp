import asyncio
import sys
import os
import pytest
import tempfile
import yaml
from pathlib import Path

# Ensure the main project directory is in the Python path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

try:
    from fastmcp import Client
    from mcp_ssh_server import mcp, SshHostManager
    from ssh_models import SshError
    from test_utils import get_client, cleanup_client, print_test_header, print_test_footer
except ImportError as e:
    print(f"FATAL: Failed to import required modules. Error: {e}", file=sys.stderr)
    print("Make sure fastmcp is installed and you are running from the correct directory.", file=sys.stderr)
    sys.exit(1)

# Global variables to store test state
ssh_client = None
host_manager = None
config_path = None

async def setup_test_environment():
    """Set up the test environment with a real SSH connection."""
    global ssh_client, host_manager, config_path
    
    print("Setting up test environment...")
    
    # Create a temporary config file for testing
    with tempfile.NamedTemporaryFile(mode='w', delete=False) as tmp:
        yaml.safe_dump({'hosts': []}, tmp)
        config_path = Path(tmp.name)
    
    # Initialize host manager with the temp config
    host_manager = SshHostManager(config_path=config_path)
    
    # Get a real SSH client connection to the test container
    ssh_client = get_client()
    
    # Add test host to the host manager (this is just for testing the add_host functionality)
    host_manager.add_host(
        'test_docker', 
        ssh_client.host, 
        ssh_client.port, 
        ssh_client.user, 
        ssh_client.password or ''
    )
    
    # Set the global SSH client in the MCP server
    import mcp_ssh_server
    mcp_ssh_server.ssh_client = ssh_client
    
    print(f"Test environment set up with SSH connection to {ssh_client.host}:{ssh_client.port}")
    return True

async def teardown_test_environment():
    """Clean up the test environment."""
    global ssh_client, config_path
    
    print("Tearing down test environment...")
    
    # Close the SSH client
    if ssh_client:
        cleanup_client(ssh_client)
        ssh_client = None
    
    # Clean up the temporary config file
    if config_path and config_path.exists():
        config_path.unlink()
    
    # Reset the global SSH client in the MCP server
    import mcp_ssh_server
    mcp_ssh_server.ssh_client = None
    
    print("Test environment cleaned up")

async def run_mcp_ssh_integration_tests():
    """Run integration tests for the SSH MCP server tools using a real SSH connection."""
    print("Starting SSH MCP server integration tests...")
    
    try:
        # Set up the test environment
        setup_success = await setup_test_environment()
        if not setup_success:
            print("Failed to set up test environment")
            return
        
        # Use the Client context manager with the imported mcp instance
        async with Client(mcp) as client:
            print("MCP client created. Testing tools with real SSH connection...")

            # --- Test ssh_run tool ---
            try:
                print_test_header("Testing 'ssh_run' tool")
                
                # Simple echo command
                run_params = {
                    "command": "echo 'Hello from MCP SSH!'",
                    "io_timeout": 10.0
                }
                
                print(f"Running command via MCP: {run_params['command']}")
                run_result = await client.call_tool("ssh_cmd_run", run_params)
                
                print(f"Command result: {run_result}")
                
                # Verify the result
                assert run_result['exit_code'] == 0, f"Expected exit code 0, got {run_result['exit_code']}"
                assert "Hello from MCP SSH!" in run_result['output'], "Expected output not found"
                assert 'pid' in run_result, "PID should be included in result"
                assert 'start_time' in run_result, "Start time should be included in result"
                assert 'end_time' in run_result, "End time should be included in result"
                
                print("Basic ssh_run test passed!")
                
                # Test with a command that produces multiple lines
                run_params = {
                    "command": "for i in {1..5}; do echo \"Line $i\"; done",
                    "io_timeout": 10.0
                }
                
                print(f"Running multi-line command via MCP: {run_params['command']}")
                run_result = await client.call_tool("ssh_cmd_run", run_params)
                
                print(f"Command result: {run_result}")
                
                # Verify the result
                assert run_result['exit_code'] == 0, f"Expected exit code 0, got {run_result['exit_code']}"
                assert "Line 1" in run_result['output'], "Expected 'Line 1' not found in output"
                assert "Line 5" in run_result['output'], "Expected 'Line 5' not found in output"
                
                print("Multi-line ssh_run test passed!")
                
                # Test with a failing command
                run_params = {
                    "command": "exit 42",
                    "io_timeout": 10.0
                }
                
                print(f"Running failing command via MCP: {run_params['command']}")
                try:
                    run_result = await client.call_tool("ssh_cmd_run", run_params)
                    print("Command should have failed but didn't")
                    assert False, "Command should have failed with exit code 42"
                except Exception as e:
                    print(f"Got expected exception: {e}")
                    assert "exit code 42" in str(e), "Exception should mention exit code 42"
                
                print("Failing command ssh_run test passed!")
                
                print_test_footer()
            except Exception as e:
                print(f"Error testing 'ssh_run': {e}", file=sys.stderr)
                raise

            # --- Test ssh_status tool ---
            try:
                print_test_header("Testing 'ssh_status' tool")
                
                status_result = await client.call_tool("ssh_conn_status", {})
                
                print(f"Status result: {status_result}")
                
                # Verify the result structure
                assert 'connection' in status_result, "Result should include connection info"
                assert 'system' in status_result, "Result should include system info"
                
                # Check connection details
                conn = status_result['connection']
                assert conn['host'] == ssh_client.host, f"Host mismatch: {conn['host']} != {ssh_client.host}"
                
                # Check system details
                system = status_result['system']
                assert 'os_info' in system, "System info should include OS info"
                assert 'hardware_info' in system, "System info should include hardware info"
                
                print("ssh_status test passed!")
                print_test_footer()
            except Exception as e:
                print(f"Error testing 'ssh_status': {e}", file=sys.stderr)
                raise

            # --- Test ssh_command_history tool ---
            try:
                print_test_header("Testing 'ssh_command_history' tool")
                
                # First run a few commands to ensure we have history
                for i in range(3):
                    run_params = {
                        "command": f"echo 'History test {i}'",
                        "io_timeout": 5.0
                    }
                    await client.call_tool("ssh_cmd_run", run_params)
                
                # Get command history
                history_params = {
                    "limit": 5,
                    "include_output": True,
                    "output_lines": 2
                }
                
                history_result = await client.call_tool("ssh_cmd_history", history_params)
                
                print(f"History result: {history_result}")
                
                # Verify the result
                assert isinstance(history_result, list), "History result should be a list"
                assert len(history_result) > 0, "History should contain at least one entry"
                
                # Check the most recent entry
                latest = history_result[-1]
                assert 'command' in latest, "History entry should include command"
                assert 'exit_code' in latest, "History entry should include exit code"
                assert 'output' in latest, "History entry should include output"
                
                print("ssh_command_history test passed!")
                print_test_footer()
            except Exception as e:
                print(f"Error testing 'ssh_command_history': {e}", file=sys.stderr)
                raise

    except Exception as e:
        print(f"\nTest run failed with error: {e}", file=sys.stderr)
        raise
    finally:
        # Clean up the test environment
        await teardown_test_environment()

    print("\nAll SSH MCP server integration tests completed!")

if __name__ == "__main__":
    try:
        asyncio.run(run_mcp_ssh_integration_tests())
    except Exception as e:
        print(f"\nTest run failed with error: {e}", file=sys.stderr)
        sys.exit(1)
