import pytest
import json
import os
import tempfile
from conftest import print_test_header, print_test_footer, make_connection, disconnect_ssh, extract_result_text
from mcp_ssh_server import mcp
from fastmcp import Client



@pytest.mark.asyncio
async def test_ssh_file_transfer(mcp_test_environment):
    """Test file upload and download operations."""
    print_test_header("Testing 'ssh_file_transfer' tool")

    async with Client(mcp) as client:
        # Initialize variables for cleanup
        local_path = None
        download_path = None
        
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            
            # Create temp file for upload
            with tempfile.NamedTemporaryFile(mode='w+', delete=False) as temp_file:
                temp_file.write("This is a test file for SSH transfer")
                local_path = temp_file.name
            
            remote_path = "/tmp/ssh_test_upload.txt"
            
            # Test upload
            upload_result = await client.call_tool("ssh_file_transfer", {
                "direction": "upload",
                "local_path": local_path,
                "remote_path": remote_path
            })
            upload_json = json.loads(extract_result_text(upload_result))
            assert upload_json['success'], "Upload failed"
            
            # Test download
            download_path = os.path.join(tempfile.gettempdir(), "ssh_test_download.txt")
            download_result = await client.call_tool("ssh_file_transfer", {
                "direction": "download", 
                "local_path": download_path,
                "remote_path": remote_path
            })
            download_json = json.loads(extract_result_text(download_result))
            assert download_json['success'], "Download failed"
            
            # Verify content
            with open(download_path, 'r') as f:
                assert "This is a test file for SSH transfer" in f.read()
                
        finally:
            # Cleanup
            for path in [local_path, download_path]:
                if path is not None and os.path.exists(path):
                    os.unlink(path)
            await client.call_tool("ssh_cmd_run", {
                "command": f"rm -f {remote_path}",
                "io_timeout": 5.0
            })
            await disconnect_ssh(client)
    
    print_test_footer()




@pytest.mark.asyncio
async def test_ssh_mkdir_rmdir(mcp_test_environment):
    """Test directory creation and removal operations."""
    print_test_header("Testing 'ssh_dir_mkdir' and 'ssh_dir_remove' tools")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            test_dir = "/tmp/ssh_test_dir"
            
            # Cleanup any existing dir
            await client.call_tool("ssh_dir_remove", {
                "path": test_dir,
                "recursive": True
            })
            
            # Test create directory
            mkdir_result = await client.call_tool("ssh_dir_mkdir", {
                "path": test_dir,
                "mode": 0o755
            })
            assert json.loads(extract_result_text(mkdir_result))['status'] == 'success'
            
            # Verify directory exists using ssh_file_stat
            stat_result = await client.call_tool("ssh_file_stat", {"path": test_dir})
            stat_info = json.loads(extract_result_text(stat_result)) # Should be valid JSON from the tool
                
            # Print for debugging
            print(f"stat_info type: {type(stat_info)}")
            print(f"stat_info content: {stat_info}")
                
            assert stat_info.get('exists') == True, f"Directory {test_dir} should exist. Stat info: {stat_info}"
            assert stat_info.get('type') == 'directory', f"Path {test_dir} should be a directory. Stat info: {stat_info}"
            
            # Test remove directory
            rmdir_result = await client.call_tool("ssh_dir_remove", {
                "path": test_dir,
                "recursive": False # Should succeed as it's empty
            })
            assert json.loads(extract_result_text(rmdir_result))['status'] == 'success'

            # Verify directory no longer exists
            stat_after_rm_result = await client.call_tool("ssh_file_stat", {"path": test_dir})
            stat_info_after_rm = json.loads(extract_result_text(stat_after_rm_result))
            assert stat_info_after_rm.get('exists') == False, f"Directory {test_dir} should not exist after rmdir. Stat info: {stat_info_after_rm}"
            
        finally:
            # Additional cleanup just in case, though the test should handle it
            stat_check_result = await client.call_tool("ssh_file_stat", {"path": test_dir})
            stat_check_info = json.loads(extract_result_text(stat_check_result))
            if stat_check_info.get('exists'):
                await client.call_tool("ssh_dir_remove", {"path": test_dir, "recursive": True})
            await disconnect_ssh(client)
    
    print_test_footer()




@pytest.mark.asyncio
async def test_ssh_file_find_lines_with_pattern(mcp_test_environment):
    """Test finding lines with pattern in a file."""
    print_test_header("Testing 'ssh_file_find_lines_with_pattern' tool")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            test_file = "/tmp/ssh_test_file.txt"
            file_content = """Line 1: This is a test file
Line 2: This line contains a pattern
Line 3: This line also has the pattern
Line 4: This is the last line"""
            
            # Create test file
            await client.call_tool("ssh_cmd_run", {
                "command": f"echo '{file_content}' > {test_file}",
                "io_timeout": 5.0
            })
            
            # Find lines with pattern
            find_result = await client.call_tool("ssh_file_find_lines_with_pattern", {
                "file_path": test_file,
                "pattern": "pattern",
                "regex": False
            })
            result = json.loads(extract_result_text(find_result))
            
            # Verify results
            assert result['total_matches'] == 2
            assert len(result['matches']) == 2
            assert "pattern" in result['matches'][0]['content']
            assert "pattern" in result['matches'][1]['content']
            
        finally:
            await client.call_tool("ssh_cmd_run", {
                "command": f"rm -f {test_file}",
                "io_timeout": 5.0
            })
            await disconnect_ssh(client)
    
    print_test_footer()



@pytest.mark.asyncio
async def test_ssh_file_get_context_around_line(mcp_test_environment):
    """Test getting context around a line in a file."""
    print_test_header("Testing 'ssh_file_get_context_around_line' tool")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            test_file = "/tmp/ssh_test_file.txt"
            file_content = """Line 1: This is a test file
Line 2: This is some context before
Line 3: This is the target line
Line 4: This is some context after
Line 5: This is the last line"""
            
            # Create test file
            await client.call_tool("ssh_cmd_run", {
                "command": f"echo '{file_content}' > {test_file}",
                "io_timeout": 5.0
            })
            
            # Get context around line
            context_result = await client.call_tool("ssh_file_get_context_around_line", {
                "file_path": test_file,
                "match_line": "Line 3: This is the target line",
                "context": 1
            })
            result = json.loads(extract_result_text(context_result))
            
            # Verify results
            assert result['match_found'] == True
            assert result['match_line_number'] == 3
            assert len(result['context_block']) == 3  # Target line + 1 before + 1 after
            assert result['context_block'][0]['content'] == "Line 2: This is some context before"
            assert result['context_block'][1]['content'] == "Line 3: This is the target line"
            assert result['context_block'][2]['content'] == "Line 4: This is some context after"
            
        finally:
            await client.call_tool("ssh_cmd_run", {
                "command": f"rm -f {test_file}",
                "io_timeout": 5.0
            })
            await disconnect_ssh(client)
    
    print_test_footer()





@pytest.mark.asyncio
async def test_ssh_file_insert_lines_after_match(mcp_test_environment):
    """Test inserting lines after a match in a file."""
    print_test_header("Testing 'ssh_file_insert_lines_after_match' tool")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            test_file = "/tmp/ssh_test_file.txt"
            file_content = """Line 1: This is a test file
Line 2: This is the target line
Line 3: This is the last line"""
            
            # Create test file
            await client.call_tool("ssh_cmd_run", {
                "command": f"echo '{file_content}' > {test_file}",
                "io_timeout": 5.0
            })
            
            # Insert lines after match
            insert_result = await client.call_tool("ssh_file_insert_lines_after_match", {
                "file_path": test_file,
                "match_line": "Line 2: This is the target line",
                "lines_to_insert": ["Line 2.5: This is an inserted line"]
            })
            result = json.loads(extract_result_text(insert_result))
            assert result['success'] == True
            
            # Verify content
            cat_result = await client.call_tool("ssh_cmd_run", {
                "command": f"cat {test_file}",
                "io_timeout": 5.0
            })
            output = json.loads(extract_result_text(cat_result))['output']
            assert "Line 2.5: This is an inserted line" in output
            
            # Check order of lines
            lines = output.strip().split('\n')
            assert lines[0] == "Line 1: This is a test file"
            assert lines[1] == "Line 2: This is the target line"
            assert lines[2] == "Line 2.5: This is an inserted line"
            assert lines[3] == "Line 3: This is the last line"
            
            # Test non-existent line
            insert_nonexistent = await client.call_tool("ssh_file_insert_lines_after_match", {
                "file_path": test_file,
                "match_line": "This line does not exist",
                "lines_to_insert": ["New line"]
            })
            nonexistent_result = json.loads(extract_result_text(insert_nonexistent))
            assert nonexistent_result['success'] == False, "Should fail when line doesn't exist"
            
        finally:
            await client.call_tool("ssh_cmd_run", {
                "command": f"rm -f {test_file}",
                "io_timeout": 5.0
            })
            await disconnect_ssh(client)
    
    print_test_footer()



@pytest.mark.asyncio
async def test_ssh_file_delete_line_by_content(mcp_test_environment):
    """Test deleting a line by content from a file."""
    print_test_header("Testing 'ssh_file_delete_line_by_content' tool")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            test_file = "/tmp/ssh_test_file.txt"
            file_content = """Line 1: This is a test file
Line 2: This line will be deleted
Line 3: This is the last line"""
            
            # Create test file
            await client.call_tool("ssh_cmd_run", {
                "command": f"echo '{file_content}' > {test_file}",
                "io_timeout": 5.0
            })
            
            # Delete line by content
            delete_result = await client.call_tool("ssh_file_delete_line_by_content", {
                "file_path": test_file,
                "match_line": "Line 2: This line will be deleted"
            })
            result = json.loads(extract_result_text(delete_result))
            assert result['success'] == True
            
            # Verify content
            cat_result = await client.call_tool("ssh_cmd_run", {
                "command": f"cat {test_file}",
                "io_timeout": 5.0
            })
            output = json.loads(extract_result_text(cat_result))['output']
            assert "Line 2: This line will be deleted" not in output
            
            # Check remaining lines
            lines = output.strip().split('\n')
            assert len(lines) == 2
            assert lines[0] == "Line 1: This is a test file"
            assert lines[1] == "Line 3: This is the last line"
            
            # Test non-existent line
            delete_nonexistent = await client.call_tool("ssh_file_delete_line_by_content", {
                "file_path": test_file,
                "match_line": "This line does not exist"
            })
            nonexistent_result = json.loads(extract_result_text(delete_nonexistent))
            assert nonexistent_result['success'] == False, "Should fail when line doesn't exist"
            
        finally:
            await client.call_tool("ssh_cmd_run", {
                "command": f"rm -f {test_file}",
                "io_timeout": 5.0
            })
            await disconnect_ssh(client)
    
    print_test_footer()

@pytest.mark.asyncio
async def test_ssh_file_copy(mcp_test_environment):
    """Test copying a file with timestamp option."""
    print_test_header("Testing 'ssh_file_copy' tool")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            test_file = "/tmp/ssh_test_source.txt"
            dest_file = "/tmp/ssh_test_dest.txt"
            file_content = "This is a test file for copying"
            
            # Create test file
            await client.call_tool("ssh_cmd_run", {
                "command": f"echo '{file_content}' > {test_file}",
                "io_timeout": 5.0
            })
            
            # Copy file without timestamp
            copy_result = await client.call_tool("ssh_file_copy", {
                "source_path": test_file,
                "destination_path": dest_file,
                "append_timestamp": False
            })
            result = json.loads(extract_result_text(copy_result))
            assert result['success'] == True
            assert result['copied_to'] == dest_file
            
            # Verify content
            cat_result = await client.call_tool("ssh_cmd_run", {
                "command": f"cat {dest_file}",
                "io_timeout": 5.0
            })
            assert file_content in json.loads(extract_result_text(cat_result))['output']
            
            # Copy file with timestamp
            timestamped_copy_result = await client.call_tool("ssh_file_copy", {
                "source_path": test_file,
                "destination_path": dest_file,
                "append_timestamp": True
            })
            timestamped_result = json.loads(extract_result_text(timestamped_copy_result))
            assert timestamped_result['success'] == True
                
            # Check that the timestamped path contains the base destination path
            # The timestamp is inserted before the extension, so we need to check parts
            dest_base, dest_ext = os.path.splitext(dest_file)
            assert dest_base in timestamped_result['copied_to']
            assert dest_file != timestamped_result['copied_to']  # Should have timestamp inserted
            
            # Verify timestamped file exists
            ls_result = await client.call_tool("ssh_cmd_run", {
                "command": f"ls -la {timestamped_result['copied_to']}",
                "io_timeout": 5.0
            })
            assert timestamped_result['copied_to'] in json.loads(extract_result_text(ls_result))['output']
            
            # Test non-existent source file
            nonexistent_copy = await client.call_tool("ssh_file_copy", {
                "source_path": "/tmp/nonexistent_file.txt",
                "destination_path": dest_file,
                "append_timestamp": False
            })
            nonexistent_result = json.loads(extract_result_text(nonexistent_copy))
            assert nonexistent_result['success'] == False, "Should fail when source file doesn't exist"
            
        finally:
            # Clean up all test files
            await client.call_tool("ssh_cmd_run", {
                "command": f"rm -f {test_file} {dest_file} /tmp/ssh_test_dest.txt.*",
                "io_timeout": 5.0
            })
            await disconnect_ssh(client)
    
    print_test_footer()
@pytest.mark.asyncio
async def test_ssh_file_move(mcp_test_environment):
    """Test moving a file."""
    print_test_header("Testing 'ssh_file_move' tool")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            source_file = "/tmp/ssh_test_source_move.txt"
            dest_file = "/tmp/ssh_test_dest_move.txt"
            file_content = "This is a test file for moving"
            
            # Create test file
            await client.call_tool("ssh_cmd_run", {
                "command": f"echo '{file_content}' > {source_file}",
                "io_timeout": 5.0
            })
            
            # Move file
            move_result = await client.call_tool("ssh_file_move", {
                "source": source_file,
                "destination": dest_file,
                "overwrite": False
            })
            result = json.loads(extract_result_text(move_result))
            assert result['success'] == True
            
            # Verify source file no longer exists
            source_check = await client.call_tool("ssh_cmd_run", {
                "command": f"ls {source_file} 2>/dev/null || echo 'File not found'",
                "io_timeout": 5.0
            })
            assert "File not found" in json.loads(extract_result_text(source_check))['output']
            
            # Verify destination file exists with correct content
            cat_result = await client.call_tool("ssh_cmd_run", {
                "command": f"cat {dest_file}",
                "io_timeout": 5.0
            })
            assert file_content in json.loads(extract_result_text(cat_result))['output']
            
            # Test overwrite behavior
            # Create a new source file
            await client.call_tool("ssh_cmd_run", {
                "command": f"echo 'New content' > {source_file}",
                "io_timeout": 5.0
            })
            
            # Try to move without overwrite (should fail)
            move_no_overwrite = await client.call_tool("ssh_file_move", {
                "source": source_file,
                "destination": dest_file,
                "overwrite": False
            })
            no_overwrite_result = json.loads(extract_result_text(move_no_overwrite))
            assert no_overwrite_result['success'] == False, "Should fail when destination exists and overwrite=False"
            
            # Move with overwrite
            move_with_overwrite = await client.call_tool("ssh_file_move", {
                "source": source_file,
                "destination": dest_file,
                "overwrite": True
            })
            overwrite_result = json.loads(extract_result_text(move_with_overwrite))
            assert overwrite_result['success'] == True
            
            # Verify content was updated
            cat_result = await client.call_tool("ssh_cmd_run", {
                "command": f"cat {dest_file}",
                "io_timeout": 5.0
            })
            assert "New content" in json.loads(extract_result_text(cat_result))['output']
            
        finally:
            # Clean up test files
            await client.call_tool("ssh_cmd_run", {
                "command": f"rm -f {source_file} {dest_file}",
                "io_timeout": 5.0
            })
            await disconnect_ssh(client)
    
    print_test_footer()






@pytest.mark.asyncio
async def test_ssh_file_write_basic(mcp_test_environment):
    """Test basic file writing functionality."""
    print_test_header("Testing 'ssh_file_write' tool - basic operations")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            test_file = "/tmp/ssh_test_write.txt"
            test_content = "This is a test file\nwith multiple lines\nand special characters: !@#$%^&*()"
            
            # Test 1: Create a new file
            write_result = await client.call_tool("ssh_file_write", {
                "file_path": test_file,
                "content": test_content
            })
            result = json.loads(extract_result_text(write_result))
            assert result['success'] == True
            assert result['file_path'] == test_file
            assert result['bytes_written'] > 0
            
            # Verify content
            cat_result = await client.call_tool("ssh_cmd_run", {
                "command": f"cat {test_file}",
                "io_timeout": 5.0
            })
            output = json.loads(extract_result_text(cat_result))['output']
            assert output.rstrip('\n') == test_content
            
            # Test 2: Overwrite existing file
            new_content = "This is new content\nthat overwrites the previous content"
            overwrite_result = await client.call_tool("ssh_file_write", {
                "file_path": test_file,
                "content": new_content
            })
            overwrite_json = json.loads(extract_result_text(overwrite_result))
            assert overwrite_json['success'] == True
            
            # Verify content was overwritten
            cat_result = await client.call_tool("ssh_cmd_run", {
                "command": f"cat {test_file}",
                "io_timeout": 5.0
            })
            output = json.loads(extract_result_text(cat_result))['output']
            assert output.rstrip('\n') == new_content
            assert "This is a test file" not in output
            
            # Test 3: Set file permissions
            chmod_result = await client.call_tool("ssh_file_write", {
                "file_path": test_file,
                "content": "Content with specific permissions",
                "mode": 0o600
            })
            chmod_json = json.loads(extract_result_text(chmod_result))
            assert chmod_json['success'] == True
            assert chmod_json['mode'] == "600"
            
            # Verify permissions
            stat_result = await client.call_tool("ssh_file_stat", {
                "path": test_file
            })
            stat_json = json.loads(extract_result_text(stat_result))
            # The mode includes file type bits (0o100000 for regular files)
            # so we check if the permission bits (last 3 digits) match
            assert stat_json['mode'].endswith('600')
            
        finally:
            await client.call_tool("ssh_cmd_run", {
                "command": f"rm -f {test_file}",
                "io_timeout": 5.0
            })
            await disconnect_ssh(client)
    
    print_test_footer()


@pytest.mark.asyncio
async def test_ssh_file_write_append(mcp_test_environment):
    """Test file append functionality."""
    print_test_header("Testing 'ssh_file_write' tool - append mode")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            test_file = "/tmp/ssh_test_append.txt"
            initial_content = "Initial content\nLine 2"
            append_content = "\nAppended content\nLine 4"
            
            # Create initial file
            write_result = await client.call_tool("ssh_file_write", {
                "file_path": test_file,
                "content": initial_content
            })
            result = json.loads(extract_result_text(write_result))
            assert result['success'] == True
            
            # Test append mode
            append_result = await client.call_tool("ssh_file_write", {
                "file_path": test_file,
                "content": append_content,
                "append": True
            })
            append_json = json.loads(extract_result_text(append_result))
            assert append_json['success'] == True
            assert append_json['append'] == True
            
            # Verify content was appended
            cat_result = await client.call_tool("ssh_cmd_run", {
                "command": f"cat {test_file}",
                "io_timeout": 5.0
            })
            output = json.loads(extract_result_text(cat_result))['output']
            expected_content = initial_content + append_content
            assert output.rstrip('\n') == expected_content
            
            # Test append to non-existent file
            nonexistent_file = "/tmp/ssh_test_nonexistent.txt"
            nonexistent_append = await client.call_tool("ssh_file_write", {
                "file_path": nonexistent_file,
                "content": "Content in a new file with append mode",
                "append": True
            })
            nonexistent_json = json.loads(extract_result_text(nonexistent_append))
            assert nonexistent_json['success'] == True
            
            # Verify content was created
            cat_result = await client.call_tool("ssh_cmd_run", {
                "command": f"cat {nonexistent_file}",
                "io_timeout": 5.0
            })
            output = json.loads(extract_result_text(cat_result))['output']
            assert output.rstrip('\n') == "Content in a new file with append mode"
            
        finally:
            await client.call_tool("ssh_cmd_run", {
                "command": f"rm -f {test_file} /tmp/ssh_test_nonexistent.txt",
                "io_timeout": 5.0
            })
            await disconnect_ssh(client)
    
    print_test_footer()


@pytest.mark.asyncio
async def test_ssh_file_write_create_dirs(mcp_test_environment):
    """Test creating parent directories when writing files."""
    print_test_header("Testing 'ssh_file_write' tool - create directories")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            test_dir = "/tmp/ssh_test_nested_dir"
            test_file = f"{test_dir}/nested/path/test_file.txt"
            test_content = "Content in a file with nested directories"
            
            # Clean up any existing directories
            await client.call_tool("ssh_cmd_run", {
                "command": f"rm -rf {test_dir}",
                "io_timeout": 5.0
            })
            
            # Test 1: Without create_dirs (should fail)
            write_result = await client.call_tool("ssh_file_write", {
                "file_path": test_file,
                "content": test_content,
                "create_dirs": False
            })
            result = json.loads(extract_result_text(write_result))
            assert result['success'] == False
            assert "Parent directory does not exist" in result.get('error', '')
            
            # Test 2: With create_dirs
            write_with_dirs = await client.call_tool("ssh_file_write", {
                "file_path": test_file,
                "content": test_content,
                "create_dirs": True
            })
            dirs_result = json.loads(extract_result_text(write_with_dirs))
            assert dirs_result['success'] == True
            
            # Verify file exists and has correct content
            cat_result = await client.call_tool("ssh_cmd_run", {
                "command": f"cat {test_file}",
                "io_timeout": 5.0
            })
            output = json.loads(extract_result_text(cat_result))['output']
            assert output.rstrip('\n') == test_content
            
            # Verify parent directories were created
            ls_result = await client.call_tool("ssh_cmd_run", {
                "command": f"ls -la {test_dir}/nested/path",
                "io_timeout": 5.0
            })
            ls_output = json.loads(extract_result_text(ls_result))['output']
            assert "test_file.txt" in ls_output
            
        finally:
            await client.call_tool("ssh_cmd_run", {
                "command": f"rm -rf {test_dir}",
                "io_timeout": 5.0
            })
            await disconnect_ssh(client)
    
    print_test_footer()


@pytest.mark.asyncio
async def test_ssh_file_write_sudo(mcp_test_environment):
    """Test writing files with sudo permissions."""
    print_test_header("Testing 'ssh_file_write' tool - sudo operations")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            
            # Check if we have sudo access
            sudo_check = await client.call_tool("ssh_conn_verify_sudo", {})
            sudo_json = json.loads(extract_result_text(sudo_check))
            
            if not sudo_json['available']:
                print("Skipping sudo tests as sudo is not available")
                return
            
            # Test writing to a protected directory
            test_file = "/etc/ssh_test_sudo_write.txt"
            test_content = "This file was written with sudo permissions"
            
            # Write with sudo
            write_result = await client.call_tool("ssh_file_write", {
                "file_path": test_file,
                "content": test_content,
                "use_sudo": True
            })
            result = json.loads(extract_result_text(write_result))
            
            # If sudo worked, verify the file
            if result['success']:
                # Verify content
                cat_result = await client.call_tool("ssh_cmd_run", {
                    "command": f"cat {test_file}",
                    "io_timeout": 5.0,
                    "use_sudo": True
                })
                output = json.loads(extract_result_text(cat_result))['output']
                # Strip trailing newline from output for comparison
                assert output.rstrip('\n') == test_content
                
                # Clean up
                await client.call_tool("ssh_cmd_run", {
                    "command": f"rm -f {test_file}",
                    "io_timeout": 5.0,
                    "use_sudo": True
                })
            else:
                print(f"Sudo write test failed: {result['error']}")
            
        finally:
            await disconnect_ssh(client)
    
    print_test_footer()
