import pytest
import asyncio
from test_mcp_fixtures import setup_test_environment, teardown_test_environment, get_mcp_client
from test_utils import print_test_header, print_test_footer

@pytest.mark.asyncio
async def test_ssh_command_history():
    """Test retrieving command history."""
    print_test_header("Testing 'ssh_command_history' tool")
    
    async with await get_mcp_client() as client:
        # First run a few commands to ensure we have history
        for i in range(3):
            run_params = {
                "command": f"echo 'History test {i}'",
                "io_timeout": 5.0
            }
            await client.call_tool("ssh_run", run_params)
        
        # Get command history
        history_params = {
            "limit": 5,
            "include_output": True,
            "output_lines": 2
        }
        
        history_result = await client.call_tool("ssh_command_history", history_params)
        
        print(f"History result: {history_result}")
        
        # Verify the result
        assert isinstance(history_result, list), "History result should be a list"
        assert len(history_result) > 0, "History should contain at least one entry"
        
        # Check the most recent entry
        latest = history_result[-1]
        assert 'command' in latest, "History entry should include command"
        assert 'exit_code' in latest, "History entry should include exit code"
        assert 'output' in latest, "History entry should include output"
    
    print_test_footer()
