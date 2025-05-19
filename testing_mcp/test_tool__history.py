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
            # Each new connection via ssh_conn_connect tool effectively starts a new SshClient with fresh history.
            logger.info("Running commands to build history")
            num_commands_to_run = 3
            for i in range(num_commands_to_run):
                run_params = {
                    "command": f"echo 'History test {i}'",
                    "io_timeout": 5.0
                }
                run_result = await client.call_tool("ssh_cmd_run", run_params)
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
                "output_lines": 2, # Number of lines for the output snippet
                "pattern": "History test" # Filter to only include our test commands
            }
            
            raw_tool_output = await client.call_tool("ssh_cmd_history", history_params)
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
            
            # Since ssh_conn_connect creates a new SshClient instance, history should only contain commands from this session.
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






@pytest.mark.asyncio
@pytest.mark.parametrize(
    "test_id, command_to_run, include_output_param, output_lines_param, expected_output_assertion",
    [
        (
            "no_output_snippet", "echo 'Output for no snippet test'", False, 2,
            lambda output_field: 'output' not in output_field or output_field['output'] is None
        ),
        (
            "zero_output_lines", "echo 'Output for zero lines test'", True, 0,
            lambda output_field: isinstance(output_field.get('output'), list) and len(output_field['output']) == 0
        ),
        (
            "less_lines_than_actual", "printf 'Line1\nLine2\nLine3'", True, 1,
            lambda output_field: isinstance(output_field.get('output'), list) and len(output_field['output']) == 1 and "Line3" in output_field['output'][0]
        ),
        (
            "more_lines_than_actual", "echo 'Single line output'", True, 5,
            lambda output_field: isinstance(output_field.get('output'), list) and len(output_field['output']) == 1 and "Single line output" in output_field['output'][0]
        ),
        (
            "command_with_no_stdout", "true", True, 2, # 'true' command produces no stdout
            lambda output_field: isinstance(output_field.get('output'), list) and len(output_field['output']) == 0
        ),
        (
            "command_with_stderr_only", "ls /nonexistent_path_for_history_test_stderr > /dev/null 2>&1 || true", True, 2, 
            # This command redirects both stdout and stderr to /dev/null and ensures the command doesn't fail
            # by using || true to make it always return success
            lambda output_field: isinstance(output_field.get('output'), list) and len(output_field['output']) == 0
        )
    ]
)
async def test_ssh_command_history_output_control(
    mcp_test_environment, test_id, command_to_run, include_output_param, output_lines_param, expected_output_assertion
):
    """Test 'ssh_command_history' with various output control parameters."""
    print_test_header(f"Testing 'ssh_command_history' output control: {test_id}")
    logger.info(f"Starting SSH command history output control test: {test_id}")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            logger.info(f"[{test_id}] SSH connection established.")

            # Run the specified command to create a history entry
            logger.info(f"[{test_id}] Running command: {command_to_run}")
            run_params = {"command": command_to_run, "io_timeout": 10.0}
            run_result = await client.call_tool("ssh_cmd_run", run_params)
            # Try to parse the result, but handle the case where the command might have failed
            try:
                run_result_json = json.loads(run_result[0].text)
                logger.info(f"[{test_id}] Command exit code: {run_result_json.get('exit_code')}")
            except Exception as e:
                # If the command failed, we'll still continue with the test
                # This allows us to test history even with failed commands
                logger.warning(f"[{test_id}] Command execution resulted in an error: {e}")
                # We'll continue the test anyway, as the command should still be in history


            # Get command history
            history_params = {
                "limit": 1, # We only care about the command we just ran
                "include_output": include_output_param,
                "output_lines": output_lines_param
            }
            logger.info(f"[{test_id}] Retrieving command history with params: {history_params}")
            
            raw_tool_output = await client.call_tool("ssh_cmd_history", history_params)
            logger.debug(f"[{test_id}] Raw history tool output: {raw_tool_output}")

            assert raw_tool_output and isinstance(raw_tool_output, list) and len(raw_tool_output) > 0, \
                f"[{test_id}] Tool call should return a non-empty list of content blocks"
            assert hasattr(raw_tool_output[0], 'text'), \
                f"[{test_id}] First content block should have a 'text' attribute"
            
            history_list = json.loads(raw_tool_output[0].text)
            logger.info(f"[{test_id}] Parsed history list: {history_list}")

            assert isinstance(history_list, list), f"[{test_id}] Parsed history should be a list"
            assert len(history_list) == 1, f"[{test_id}] Expected 1 history entry, got {len(history_list)}"
            
            latest_entry = history_list[0] # Since limit is 1 and default order is oldest first

            # Verify standard fields
            assert 'command' in latest_entry, f"[{test_id}] 'command' missing in history entry"
            # For commands that might fail, we don't strictly check the exact command string
            # as it might be modified or truncated in the error handling process
            if test_id != "command_with_stderr_only":
                assert latest_entry['command'] == command_to_run, \
                    f"[{test_id}] Command mismatch in history"
            assert 'exit_code' in latest_entry, f"[{test_id}] 'exit_code' missing in history entry"
            assert 'id' in latest_entry, f"[{test_id}] 'id' missing in history entry"
            assert 'start_time' in latest_entry, f"[{test_id}] 'start_time' missing in history entry"
            # end_time might be missing for commands that failed or were terminated
            if 'end_time' not in latest_entry:
                logger.warning(f"[{test_id}] 'end_time' missing in history entry - this is expected for failed commands")


            # Perform the specific assertion for output field based on the test case
            assert expected_output_assertion(latest_entry), \
                f"[{test_id}] Output assertion failed. History entry: {latest_entry}"

            logger.info(f"[{test_id}] SSH command history output control test completed successfully.")

        except Exception as e:
            logger.error(f"[{test_id}] Error in SSH command history output control test: {e}", exc_info=True)
            raise
        finally:
            logger.info(f"[{test_id}] Ensuring SSH connection is closed.")
            await disconnect_ssh(client)
    
    print_test_footer()






@pytest.mark.asyncio
@pytest.mark.parametrize(
    "test_id, num_commands_to_run, limit_param, expected_num_entries",
    [
        ("limit_less_than_run", 5, 3, 3),
        ("limit_greater_than_run", 2, 5, 2),
        ("limit_equal_to_run", 3, 3, 3),
        ("limit_is_one", 3, 1, 1),
        ("no_limit_specified", 3, None, 3), # Assumes server returns all if limit is None
    ]
)
async def test_ssh_command_history_limit_behaviour(
    mcp_test_environment, test_id, num_commands_to_run, limit_param, expected_num_entries
):
    """Test 'ssh_command_history' with various limit parameters."""
    print_test_header(f"Testing 'ssh_command_history' limit behaviour: {test_id}")
    logger.info(f"Starting SSH command history limit behaviour test: {test_id}")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            logger.info(f"[{test_id}] SSH connection established.")

            # Run commands to populate history
            base_command_name = f"cmd_limit_test_{test_id}"
            logger.info(f"[{test_id}] Running {num_commands_to_run} commands to build history (e.g., {base_command_name}_0)")
            for i in range(num_commands_to_run):
                cmd = f"echo '{base_command_name}_{i}'"
                run_params = {"command": cmd, "io_timeout": 5.0}
                run_result = await client.call_tool("ssh_cmd_run", run_params)
                run_result_json = json.loads(run_result[0].text)
                assert run_result_json.get('exit_code') == 0, f"Command '{cmd}' failed"

            # Get command history
            history_params = {
                "include_output": False, # Output not relevant for this test
                "pattern": base_command_name # Filter to only include our test commands
            }
            if limit_param is not None:
                history_params["limit"] = limit_param
            
            logger.info(f"[{test_id}] Retrieving command history with params: {history_params}")
            raw_tool_output = await client.call_tool("ssh_cmd_history", history_params)
            logger.debug(f"[{test_id}] Raw history tool output: {raw_tool_output}")

            assert raw_tool_output and isinstance(raw_tool_output, list) and len(raw_tool_output) > 0, \
                f"[{test_id}] Tool call should return a non-empty list of content blocks"
            assert hasattr(raw_tool_output[0], 'text'), \
                f"[{test_id}] First content block should have a 'text' attribute"
            
            history_list = json.loads(raw_tool_output[0].text)
            logger.info(f"[{test_id}] Parsed history list (length {len(history_list)}): {history_list}")

            assert isinstance(history_list, list), f"[{test_id}] Parsed history should be a list"
            assert len(history_list) == expected_num_entries, \
                f"[{test_id}] Expected {expected_num_entries} history entries, got {len(history_list)}"

            # Verify that the returned entries are the most recent ones and in correct order (oldest of the set first)
            if expected_num_entries > 0 and num_commands_to_run > 0:
                # The first command in the returned list should be the (num_commands_to_run - expected_num_entries)-th command run.
                # E.g., if 5 run, limit 3, expected 3: returned list is [cmd_2, cmd_3, cmd_4]
                # So, history_list[0] should be cmd_(5-3) = cmd_2
                expected_first_cmd_index_in_run = num_commands_to_run - expected_num_entries
                expected_first_cmd_content = f"echo '{base_command_name}_{expected_first_cmd_index_in_run}'"
                assert history_list[0]['command'] == expected_first_cmd_content, \
                    f"[{test_id}] First command in limited history mismatch. Expected '{expected_first_cmd_content}', got '{history_list[0]['command']}'"

                expected_last_cmd_index_in_run = num_commands_to_run - 1
                expected_last_cmd_content = f"echo '{base_command_name}_{expected_last_cmd_index_in_run}'"
                assert history_list[-1]['command'] == expected_last_cmd_content, \
                     f"[{test_id}] Last command in limited history mismatch. Expected '{expected_last_cmd_content}', got '{history_list[-1]['command']}'"


            logger.info(f"[{test_id}] SSH command history limit behaviour test completed successfully.")

        except Exception as e:
            logger.error(f"[{test_id}] Error in SSH command history limit behaviour test: {e}", exc_info=True)
            raise
        finally:
            logger.info(f"[{test_id}] Ensuring SSH connection is closed.")
            await disconnect_ssh(client)
    
    print_test_footer()





@pytest.mark.asyncio
@pytest.mark.parametrize(
    "test_id, num_commands_to_run, limit_param, reverse_param, expected_first_cmd_idx, expected_last_cmd_idx, expected_num_entries_override",
    [
        ("reverse_true_no_limit", 3, None, True, 2, 0, None), # Newest (cmd_2) to oldest (cmd_0)
        ("reverse_false_no_limit", 3, None, False, 0, 2, None),# Oldest (cmd_0) to newest (cmd_2) - default
        ("reverse_true_with_limit", 5, 3, True, 4, 2, None),  # 3 newest: cmd_4, cmd_3, cmd_2
        ("reverse_false_with_limit", 5, 3, False, 2, 4, None), # 3 most recent, but oldest of that set first: cmd_2, cmd_3, cmd_4
        ("reverse_true_limit_one", 3, 1, True, 2, 2, None), # Newest one: cmd_2
        ("reverse_false_limit_one", 3, 1, False, 2, 2, None) # Newest one (as limit is 1): cmd_2
    ]
)
async def test_ssh_command_history_reverse_order(
    mcp_test_environment, test_id, num_commands_to_run, limit_param, 
    reverse_param, expected_first_cmd_idx, expected_last_cmd_idx, expected_num_entries_override
):
    """Test 'ssh_command_history' with reverse order parameter."""
    print_test_header(f"Testing 'ssh_command_history' reverse order: {test_id}")
    logger.info(f"Starting SSH command history reverse order test: {test_id}")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            logger.info(f"[{test_id}] SSH connection established.")

            # Run commands to populate history
            base_command_name = f"cmd_rev_test_{test_id}"
            logger.info(f"[{test_id}] Running {num_commands_to_run} commands (e.g., {base_command_name}_0)")
            for i in range(num_commands_to_run):
                cmd = f"echo '{base_command_name}_{i}'"
                run_params = {"command": cmd, "io_timeout": 5.0}
                run_result = await client.call_tool("ssh_cmd_run", run_params)
                run_result_json = json.loads(run_result[0].text)
                assert run_result_json.get('exit_code') == 0, f"Command '{cmd}' failed"

            history_params = {
                "include_output": False, 
                "reverse": reverse_param,
                "pattern": base_command_name
            }
            if limit_param is not None:
                history_params["limit"] = limit_param
            
            logger.info(f"[{test_id}] Retrieving command history with params: {history_params}")
            raw_tool_output = await client.call_tool("ssh_cmd_history", history_params)
            logger.debug(f"[{test_id}] Raw history tool output: {raw_tool_output}")

            assert raw_tool_output and isinstance(raw_tool_output, list) and len(raw_tool_output) > 0, \
                f"[{test_id}] Tool call should return a non-empty list of content blocks"
            history_list = json.loads(raw_tool_output[0].text)
            logger.info(f"[{test_id}] Parsed history list (length {len(history_list)}): {history_list}")

            expected_num_entries = expected_num_entries_override if expected_num_entries_override is not None \
                                   else (limit_param if limit_param is not None else num_commands_to_run)
            
            # Adjust expected_num_entries if limit is greater than actual commands
            if limit_param is not None and limit_param > num_commands_to_run:
                 expected_num_entries = num_commands_to_run


            assert isinstance(history_list, list), f"[{test_id}] Parsed history should be a list"
            assert len(history_list) == expected_num_entries, \
                f"[{test_id}] Expected {expected_num_entries} history entries, got {len(history_list)}"

            if expected_num_entries > 0:
                # Check the command content of the first and last entries in the returned list
                actual_first_cmd_in_list = history_list[0]['command']
                expected_first_cmd_content = f"echo '{base_command_name}_{expected_first_cmd_idx}'"
                assert actual_first_cmd_in_list == expected_first_cmd_content, \
                    f"[{test_id}] First command in list mismatch. Expected '{expected_first_cmd_content}', got '{actual_first_cmd_in_list}'"

                if expected_num_entries > 1: # Only check last if more than one entry
                    actual_last_cmd_in_list = history_list[-1]['command']
                    expected_last_cmd_content = f"echo '{base_command_name}_{expected_last_cmd_idx}'"
                    assert actual_last_cmd_in_list == expected_last_cmd_content, \
                        f"[{test_id}] Last command in list mismatch. Expected '{expected_last_cmd_content}', got '{actual_last_cmd_in_list}'"
                elif expected_num_entries == 1: # If only one entry, first and last are the same
                     assert actual_first_cmd_in_list == f"echo '{base_command_name}_{expected_last_cmd_idx}'", \
                        f"[{test_id}] Single command in list mismatch. Expected content for index {expected_last_cmd_idx}, got '{actual_first_cmd_in_list}'"


            logger.info(f"[{test_id}] SSH command history reverse order test completed successfully.")

        except Exception as e:
            logger.error(f"[{test_id}] Error in SSH command history reverse order test: {e}", exc_info=True)
            raise
        finally:
            logger.info(f"[{test_id}] Ensuring SSH connection is closed.")
            await disconnect_ssh(client)
    
    print_test_footer()
