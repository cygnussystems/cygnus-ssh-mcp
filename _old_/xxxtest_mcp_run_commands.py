import pytest
from conftest import print_test_header, print_test_footer

@pytest.mark.asyncio
async def test_ssh_run_basic(mcp_client):
    """Test basic command execution with ssh_run."""
    print_test_header("Testing 'ssh_run' tool - Basic")
    
    # Debug the client type
    print(f"Client type: {type(mcp_client)}")
    print(f"Client dir: {dir(mcp_client)}")
    
    # Simple echo command
    run_params = {
        "command": "echo 'Hello from MCP SSH!'",
        "io_timeout": 10.0
    }
    
    print(f"Running command via MCP: {run_params['command']}")
    # Use the correct method name for the FastMCP client
    run_result = await mcp_client.call_tool("ssh_cmd_run", run_params)
    
    print(f"Command result: {run_result}")
    
    # Verify the result
    assert run_result['exit_code'] == 0, f"Expected exit code 0, got {run_result['exit_code']}"
    assert "Hello from MCP SSH!" in run_result['output'], "Expected output not found"
    assert 'pid' in run_result, "PID should be included in result"
    assert 'start_time' in run_result, "Start time should be included in result"
    assert 'end_time' in run_result, "End time should be included in result"
    
    print_test_footer()

@pytest.mark.asyncio
async def test_ssh_run_multiline(mcp_client):
    """Test command execution with multiple output lines."""
    print_test_header("Testing 'ssh_run' tool - Multi-line")
    
    # Test with a command that produces multiple lines
    run_params = {
        "command": "for i in {1..5}; do echo \"Line $i\"; done",
        "io_timeout": 10.0
    }
    
    print(f"Running multi-line command via MCP: {run_params['command']}")
    run_result = await mcp_client.call_tool("ssh_cmd_run", run_params)
    
    print(f"Command result: {run_result}")
    
    # Verify the result
    assert run_result['exit_code'] == 0, f"Expected exit code 0, got {run_result['exit_code']}"
    assert "Line 1" in run_result['output'], "Expected 'Line 1' not found in output"
    assert "Line 5" in run_result['output'], "Expected 'Line 5' not found in output"
    
    print_test_footer()

@pytest.mark.asyncio
async def test_ssh_run_failure(mcp_client):
    """Test command execution with a failing command."""
    print_test_header("Testing 'ssh_run' tool - Failure")
    
    # Test with a failing command
    run_params = {
        "command": "exit 42",
        "io_timeout": 10.0
    }
    
    print(f"Running failing command via MCP: {run_params['command']}")
    try:
        run_result = await mcp_client.call_tool("ssh_cmd_run", run_params)
        print("Command should have failed but didn't")
        assert False, "Command should have failed with exit code 42"
    except Exception as e:
        print(f"Got expected exception: {e}")
        assert "exit code 42" in str(e), "Exception should mention exit code 42"
    
    print_test_footer()
