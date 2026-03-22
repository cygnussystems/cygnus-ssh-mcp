import pytest
import json
import os
import tempfile
from conftest import print_test_header, print_test_footer, make_connection, disconnect_ssh, extract_result_text
from mcp_ssh_server import mcp
from fastmcp import Client






@pytest.mark.asyncio
async def test_ssh_file_replace_line(mcp_test_environment):
    """Test replacing a line in a file with a single new line."""
    print_test_header("Testing 'ssh_file_replace_line' tool")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            test_file = "/tmp/ssh_test_file.txt"
            file_content = """Line 1: This is a test file
Line 2: This line will be replaced
Line 3: This is the last line"""

            # Create test file
            await client.call_tool("ssh_cmd_run", {
                "command": f"echo '{file_content}' > {test_file}",
                "io_timeout": 5.0
            })

            # Replace line with single line
            replace_result = await client.call_tool("ssh_file_replace_line", {
                "file_path": test_file,
                "match_line": "Line 2: This line will be replaced",
                "new_line": "Line 2: This line has been replaced"
            })
            result = json.loads(extract_result_text(replace_result))
            assert result['success'] == True

            # Verify content
            cat_result = await client.call_tool("ssh_cmd_run", {
                "command": f"cat {test_file}",
                "io_timeout": 5.0
            })
            output = json.loads(extract_result_text(cat_result))['output']
            assert "Line 2: This line has been replaced" in output
            assert "Line 2: This line will be replaced" not in output

            # Test non-existent line
            replace_nonexistent = await client.call_tool("ssh_file_replace_line", {
                "file_path": test_file,
                "match_line": "This line does not exist",
                "new_line": "New line"
            })
            nonexistent_result = json.loads(extract_result_text(replace_nonexistent))
            assert nonexistent_result['success'] == False, "Should fail when line doesn't exist"

        finally:
            await client.call_tool("ssh_cmd_run", {
                "command": f"rm -f {test_file}",
                "io_timeout": 5.0
            })
            await disconnect_ssh(client)

    print_test_footer()


@pytest.mark.asyncio
async def test_ssh_file_replace_line_multi(mcp_test_environment):
    """Test replacing a line in a file with multiple new lines."""
    print_test_header("Testing 'ssh_file_replace_line_multi' tool")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            test_file = "/tmp/ssh_test_file_multi.txt"
            file_content = """Line 1: This is a test file
Line 2: This line will be replaced with multiple lines
Line 3: This is the last line"""

            # Create test file
            await client.call_tool("ssh_cmd_run", {
                "command": f"echo '{file_content}' > {test_file}",
                "io_timeout": 5.0
            })

            # Replace line with multiple lines
            replace_result = await client.call_tool("ssh_file_replace_line_multi", {
                "file_path": test_file,
                "match_line": "Line 2: This line will be replaced with multiple lines",
                "new_lines": ["Line 2: First replacement line", "Line 2.1: Second replacement line"]
            })
            result = json.loads(extract_result_text(replace_result))
            assert result['success'] == True

            # Verify content
            cat_result = await client.call_tool("ssh_cmd_run", {
                "command": f"cat {test_file}",
                "io_timeout": 5.0
            })
            output = json.loads(extract_result_text(cat_result))['output']
            assert "Line 2: First replacement line" in output
            assert "Line 2.1: Second replacement line" in output
            assert "Line 2: This line will be replaced with multiple lines" not in output

            # Check order of lines
            lines = output.strip().split('\n')
            assert lines[0] == "Line 1: This is a test file"
            assert lines[1] == "Line 2: First replacement line"
            assert lines[2] == "Line 2.1: Second replacement line"
            assert lines[3] == "Line 3: This is the last line"

        finally:
            await client.call_tool("ssh_cmd_run", {
                "command": f"rm -f {test_file}",
                "io_timeout": 5.0
            })
            await disconnect_ssh(client)

    print_test_footer()


@pytest.mark.asyncio
async def test_ssh_file_operations_with_duplicate_lines(mcp_test_environment):
    """Test file operations with duplicate lines to ensure they fail appropriately."""
    print_test_header("Testing file operations with duplicate lines")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            test_file = "/tmp/ssh_test_duplicate_lines.txt"
            # Define file content explicitly to ensure no ambiguity with whitespace or newlines
            file_content_lines = [
                "Line 1: This is a test file",
                "Line 2: This is a duplicate line",
                "Line 3: Some other content",
                "Line 2: This is a duplicate line",  # Corrected duplicate line
                "Line 5: This is the last line"
            ]
            file_content = "\n".join(file_content_lines)

            # The line we expect to be duplicated
            match_line_for_test = "Line 2: This is a duplicate line"

            # Create test file with duplicate lines
            # Using printf for more robust line handling than echo with here-doc for complex content
            # However, for this simple content, cat with here-doc is fine if file_content is well-defined.
            await client.call_tool("ssh_cmd_run", {
                "command": f"cat > {test_file} << 'EOF'\n{file_content}\nEOF",
                "io_timeout": 5.0
            })

            # Test replace line with duplicate match (single line version)
            replace_result = await client.call_tool("ssh_file_replace_line", {
                "file_path": test_file,
                "match_line": match_line_for_test,
                "new_line": "Line 2: This has been replaced"
            })
            result = json.loads(extract_result_text(replace_result))
            assert result['success'] == False, "Should fail when match line is not unique"

            # Test replace line with duplicate match (multi-line version)
            replace_multi_result = await client.call_tool("ssh_file_replace_line_multi", {
                "file_path": test_file,
                "match_line": match_line_for_test,
                "new_lines": ["Line 2: This has been replaced", "Another line"]
            })
            multi_result = json.loads(extract_result_text(replace_multi_result))
            assert multi_result['success'] == False, "Should fail when match line is not unique"

            # Test insert after line with duplicate match
            insert_result = await client.call_tool("ssh_file_insert_lines_after_match", {
                "file_path": test_file,
                "match_line": match_line_for_test,
                "lines_to_insert": ["New inserted line"]
            })
            insert_json = json.loads(extract_result_text(insert_result))
            assert insert_json['success'] == False, "Should fail when match line is not unique"

            # Test delete line with duplicate match
            delete_result = await client.call_tool("ssh_file_delete_line_by_content", {
                "file_path": test_file,
                "match_line": match_line_for_test
            })
            delete_json = json.loads(extract_result_text(delete_result))
            assert delete_json['success'] == False, "Should fail when match line is not unique"

        finally:
            await client.call_tool("ssh_cmd_run", {
                "command": f"rm -f {test_file}",
                "io_timeout": 5.0
            })
            await disconnect_ssh(client)

    print_test_footer()


@pytest.mark.asyncio
async def test_ssh_file_operations_with_nonexistent_file(mcp_test_environment):
    """Test file operations with a non-existent file to ensure they fail appropriately."""
    print_test_header("Testing file operations with non-existent file")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            nonexistent_file = "/tmp/this_file_does_not_exist.txt"

            # Test find lines in non-existent file
            find_result = await client.call_tool("ssh_file_find_lines_with_pattern", {
                "file_path": nonexistent_file,
                "pattern": "any pattern",
                "regex": False
            })
            find_json = json.loads(extract_result_text(find_result))
            assert find_json['total_matches'] == 0
            assert 'error' in find_json

            # Test get context in non-existent file
            context_result = await client.call_tool("ssh_file_get_context_around_line", {
                "file_path": nonexistent_file,
                "match_line": "any line",
                "context": 1
            })
            context_json = json.loads(extract_result_text(context_result))
            assert context_json['match_found'] == False
            assert 'error' in context_json

            # Test replace line in non-existent file
            replace_result = await client.call_tool("ssh_file_replace_line_multi", {
                "file_path": nonexistent_file,
                "match_line": "any line",
                "new_lines": ["new line"]
            })
            replace_json = json.loads(extract_result_text(replace_result))
            assert replace_json['success'] == False

            # Test insert line in non-existent file
            insert_result = await client.call_tool("ssh_file_insert_lines_after_match", {
                "file_path": nonexistent_file,
                "match_line": "any line",
                "lines_to_insert": ["new line"]
            })
            insert_json = json.loads(extract_result_text(insert_result))
            assert insert_json['success'] == False

            # Test delete line in non-existent file
            delete_result = await client.call_tool("ssh_file_delete_line_by_content", {
                "file_path": nonexistent_file,
                "match_line": "any line"
            })
            delete_json = json.loads(extract_result_text(delete_result))
            assert delete_json['success'] == False

        finally:
            await disconnect_ssh(client)

    print_test_footer()


@pytest.mark.asyncio
async def test_ssh_file_replace_with_empty_content(mcp_test_environment):
    """Test replacing a line with empty content and using the multi-line version for deletion."""
    print_test_header("Testing file replace tools with empty content")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            test_file = "/tmp/ssh_test_replace_empty.txt"
            file_content = """Line 1: This is a test file
Line 2: This line will be modified
Line 3: This is the last line"""

            # Create test file
            await client.call_tool("ssh_cmd_run", {
                "command": f"echo '{file_content}' > {test_file}",
                "io_timeout": 5.0
            })

            # Test 1: Replace with empty string (should replace with empty line)
            replace_result = await client.call_tool("ssh_file_replace_line", {
                "file_path": test_file,
                "match_line": "Line 2: This line will be modified",
                "new_line": ""
            })
            result = json.loads(extract_result_text(replace_result))
            assert result['success'] == True

            # Verify content - line should be replaced with empty line
            cat_result = await client.call_tool("ssh_cmd_run", {
                "command": f"cat {test_file}",
                "io_timeout": 5.0
            })
            output = json.loads(extract_result_text(cat_result))['output']
            assert "Line 2: This line will be modified" not in output

            # Check lines - should have an empty line between Line 1 and Line 3
            lines = output.strip().split('\n')
            assert len(lines) == 3
            assert lines[0] == "Line 1: This is a test file"
            assert lines[1] == ""
            assert lines[2] == "Line 3: This is the last line"

            # Recreate test file for next test
            await client.call_tool("ssh_cmd_run", {
                "command": f"echo '{file_content}' > {test_file}",
                "io_timeout": 5.0
            })

            # Test 2: Replace with empty list using multi tool (should delete the line)
            replace_result = await client.call_tool("ssh_file_replace_line_multi", {
                "file_path": test_file,
                "match_line": "Line 2: This line will be modified",
                "new_lines": []
            })
            result = json.loads(extract_result_text(replace_result))
            assert result['success'] == True

            # Verify content - line should be deleted
            cat_result = await client.call_tool("ssh_cmd_run", {
                "command": f"cat {test_file}",
                "io_timeout": 5.0
            })
            output = json.loads(extract_result_text(cat_result))['output']
            assert "Line 2: This line will be modified" not in output

            # Check remaining lines
            lines = output.strip().split('\n')
            assert len(lines) == 2
            assert lines[0] == "Line 1: This is a test file"
            assert lines[1] == "Line 3: This is the last line"

            # Recreate test file for next test
            await client.call_tool("ssh_cmd_run", {
                "command": f"echo '{file_content}' > {test_file}",
                "io_timeout": 5.0
            })

            # Test 3: Replace with list containing empty string (should replace with empty line)
            replace_result = await client.call_tool("ssh_file_replace_line_multi", {
                "file_path": test_file,
                "match_line": "Line 2: This line will be modified",
                "new_lines": [""]
            })
            result = json.loads(extract_result_text(replace_result))
            assert result['success'] == True

            # Verify content - line should be replaced with empty line
            cat_result = await client.call_tool("ssh_cmd_run", {
                "command": f"cat {test_file}",
                "io_timeout": 5.0
            })
            output = json.loads(extract_result_text(cat_result))['output']
            assert "Line 2: This line will be modified" not in output

            # Check lines - should have an empty line between Line 1 and Line 3
            lines = output.strip().split('\n')
            assert len(lines) == 3
            assert lines[0] == "Line 1: This is a test file"
            assert lines[1] == ""
            assert lines[2] == "Line 3: This is the last line"

            # Test 4: Use delete_line_by_content tool to delete a line
            # Recreate test file for next test
            await client.call_tool("ssh_cmd_run", {
                "command": f"echo '{file_content}' > {test_file}",
                "io_timeout": 5.0
            })
            
            delete_result = await client.call_tool("ssh_file_delete_line_by_content", {
                "file_path": test_file,
                "match_line": "Line 2: This line will be modified"
            })
            delete_json = json.loads(extract_result_text(delete_result))
            assert delete_json['success'] == True

            # Verify content - line should be deleted
            cat_result = await client.call_tool("ssh_cmd_run", {
                "command": f"cat {test_file}",
                "io_timeout": 5.0
            })
            output = json.loads(extract_result_text(cat_result))['output']
            assert "Line 2: This line will be modified" not in output

            # Check remaining lines
            lines = output.strip().split('\n')
            assert len(lines) == 2
            assert lines[0] == "Line 1: This is a test file"
            assert lines[1] == "Line 3: This is the last line"

        finally:
            await client.call_tool("ssh_cmd_run", {
                "command": f"rm -f {test_file}",
                "io_timeout": 5.0
            })
            await disconnect_ssh(client)

    print_test_footer()
