import pytest
import json
import asyncio

@pytest.mark.asyncio
async def test_ssh_run_basic(mcp_client):
    """Test basic command execution with ssh_run."""
    # Simple echo command
    run_params = {
        "command": "echo 'Hello from MCP SSH!'",
        "io_timeout": 10.0
    }
    
    # Run the command via MCP
    run_result = await mcp_client.call_tool("ssh_run", run_params)
    
    # Verify the result
    assert run_result[0].text is not None, "Expected non-empty result"
    
    # Parse the JSON response
    result_json = json.loads(run_result[0].text)
    
    assert result_json['exit_code'] == 0, f"Expected exit code 0, got {result_json['exit_code']}"
    assert "Hello from MCP SSH!" in result_json['output'], "Expected output not found"
    assert 'pid' in result_json, "PID should be included in result"
    assert 'start_time' in result_json, "Start time should be included in result"
    assert 'end_time' in result_json, "End time should be included in result"

@pytest.mark.asyncio
async def test_ssh_run_multiline(mcp_client):
    """Test command execution with multiple output lines."""
    # Test with a command that produces multiple lines
    run_params = {
        "command": "for i in {1..5}; do echo \"Line $i\"; done",
        "io_timeout": 10.0
    }
    
    # Run the multi-line command via MCP
    run_result = await mcp_client.call_tool("ssh_run", run_params)
    
    # Parse the JSON response
    result_json = json.loads(run_result[0].text)
    
    # Verify the result
    assert result_json['exit_code'] == 0, f"Expected exit code 0, got {result_json['exit_code']}"
    assert "Line 1" in result_json['output'], "Expected 'Line 1' not found in output"
    assert "Line 5" in result_json['output'], "Expected 'Line 5' not found in output"

@pytest.mark.asyncio
async def test_ssh_run_failure(mcp_client):
    """Test command execution with a failing command."""
    # Test with a failing command
    run_params = {
        "command": "exit 42",
        "io_timeout": 10.0
    }
    
    # Run the failing command via MCP
    with pytest.raises(Exception) as excinfo:
        await mcp_client.call_tool("ssh_run", run_params)
    
    # Verify the exception
    assert "exit code 42" in str(excinfo.value), "Exception should mention exit code 42"
