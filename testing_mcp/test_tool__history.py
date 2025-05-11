import pytest
import json
import logging
from conftest import print_test_header, print_test_footer, make_connection, disconnect_ssh
from mcp_ssh_server import mcp
from fastmcp import Client

# Configure logging
logger = logging.getLogger(__name__)

@pytest.mark.asyncio
async def test_ssh_command_history(mcp_test_environment): # Added mcp_test_environment fixture
    """Test retrieving command history."""
    print_test_header("Testing 'ssh_command_history' tool")
    logger.info("Starting SSH command history test")
    
    async with Client(mcp) as client:
        try:
            # Ensure we have a connection
            assert await make_connection(client), "Failed to establish SSH connection"
            logger.info("SSH connection established for command history test")
            
            # Run a few commands to ensure we have history for the current SshClient instance
            # Each new connection via ssh_connect tool effectively starts a new SshClient with fresh history.
            logger.info("Running commands to build history")
            num_commands_to_run = 3
            for i in range(num_commands_to_run):
                run_params = {
                    "command": f"echo 'History test {i}'",
                    "io_timeout": 5.0
                }
                run_result = await client.call_tool("ssh_run", run_params)
                # Log the result of ssh_run for debugging if needed
                logger.debug(f"Ran command 'echo History test {i}', result: {run_result}")
                # Basic check that the command succeeded
                run_result_json = json.loads(run_result[0].text)
                assert run_result_json.get('exit_code') == 0, f"Command 'echo History test {i}' failed"

            # Get command history
            logger.info("Retrieving command history")
            history_params = {
                "limit": 5,  # Request up to 5 entries
                "include_output": True,
                "output_lines": 2 # Number of lines for the output snippet
            }
            
            raw_tool_output = await client.call_tool("ssh_command_history", history_params)
            logger.info(f"Raw history tool output: {raw_tool_output}")

            # Verify and parse the raw tool output
            assert raw_tool_output, "Tool call should return a result"
            assert isinstance(raw_tool_output, list) and len(raw_tool_output) > 0, \
                "Tool call should return a non-empty list of content blocks"
            assert hasattr(raw_tool_output[0], 'text'), \
                "First content block should have a 'text' attribute"
            
            history_list = json.loads(raw_tool_output[0].text)
            logger.info(f"Parsed history list: {history_list}")
            
            # Verify the structure and content of the parsed history
            assert isinstance(history_list, list), "Parsed history should be a list of dictionaries"
            
            # Since ssh_connect creates a new SshClient instance, history should only contain commands from this session.
            assert len(history_list) == num_commands_to_run, \
                f"Expected {num_commands_to_run} history entries, got {len(history_list)}"
            
            # Check the most recent entry (tool returns oldest to newest by default)
            latest_entry = history_list[-1]
            expected_last_command = f"echo 'History test {num_commands_to_run - 1}'"
            
            assert 'command' in latest_entry, "History entry should include 'command'"
            assert latest_entry['command'] == expected_last_command, \
                f"Unexpected last command: got '{latest_entry['command']}', expected '{expected_last_command}'"
            
            assert 'exit_code' in latest_entry, "History entry should include 'exit_code'"
            assert latest_entry['exit_code'] == 0, \
                f"Last command '{expected_last_command}' should have succeeded (exit code 0)"
            
            assert 'output' in latest_entry, "History entry should include 'output' snippet"
            assert isinstance(latest_entry['output'], list), "Output snippet should be a list of strings"
            # The output from "echo 'History test X'" is "History test X\n".
            # The snippet should contain this.
            assert len(latest_entry['output']) > 0 and f"History test {num_commands_to_run - 1}" in latest_entry['output'][0], \
                f"Output snippet incorrect for the last command. Got: {latest_entry['output']}"
            
            logger.info("SSH command history test completed successfully")
        except Exception as e:
            logger.error(f"Error in SSH command history test: {e}", exc_info=True)
            raise
        finally:
            logger.info("Ensuring SSH connection is closed after command history test")
            await disconnect_ssh(client)
    
    print_test_footer()
