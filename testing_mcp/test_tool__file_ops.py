import pytest
import json
import os
import tempfile
from conftest import print_test_header, print_test_footer, make_connection, disconnect_ssh
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
            upload_json = json.loads(upload_result[0].text)
            assert upload_json['success'], "Upload failed"
            
            # Test download
            download_path = os.path.join(tempfile.gettempdir(), "ssh_test_download.txt")
            download_result = await client.call_tool("ssh_file_transfer", {
                "direction": "download", 
                "local_path": download_path,
                "remote_path": remote_path
            })
            download_json = json.loads(download_result[0].text)
            assert download_json['success'], "Download failed"
            
            # Verify content
            with open(download_path, 'r') as f:
                assert "This is a test file for SSH transfer" in f.read()
                
        finally:
            # Cleanup
            for path in [local_path, download_path]:
                if path is not None and os.path.exists(path):
                    os.unlink(path)
            await client.call_tool("ssh_run", {
                "command": f"rm -f {remote_path}",
                "io_timeout": 5.0
            })
            await disconnect_ssh(client)
    
    print_test_footer()




@pytest.mark.asyncio
async def test_ssh_mkdir_rmdir(mcp_test_environment):
    """Test directory creation and removal operations."""
    print_test_header("Testing 'ssh_mkdir' and 'ssh_rmdir' tools")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            test_dir = "/tmp/ssh_test_dir"
            
            # Cleanup any existing dir
            await client.call_tool("ssh_rmdir", {
                "path": test_dir,
                "recursive": True
            })
            
            # Test create directory
            mkdir_result = await client.call_tool("ssh_mkdir", {
                "path": test_dir,
                "mode": 0o755
            })
            assert json.loads(mkdir_result[0].text)['status'] == 'success'
            
            # Skip the listdir check and directly verify directory exists using ssh_stat
            stat_result = await client.call_tool("ssh_stat", {"path": test_dir})
            stat_response = stat_result[0].text
                
            # Always treat the response as a string and parse it
            try:
                stat_info = json.loads(stat_response)
            except json.JSONDecodeError:
                # If it's not valid JSON, the test should fail
                assert False, f"Invalid JSON response from ssh_stat: {stat_response}"
                
            assert stat_info.get('exists', False), f"Directory {test_dir} should exist"
            assert stat_info.get('type') == 'directory', f"Path {test_dir} should be a directory"
            
            # Test remove directory
            rmdir_result = await client.call_tool("ssh_rmdir", {
                "path": test_dir,
                "recursive": False
            })
            assert json.loads(rmdir_result[0].text)['status'] == 'success'
            
        finally:
            await disconnect_ssh(client)
    
    print_test_footer()




@pytest.mark.asyncio
async def test_ssh_replace_line(mcp_test_environment):
    """Test file content replacement operations."""
    print_test_header("Testing 'ssh_replace_line' tool")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            test_file = "/tmp/ssh_test_file.txt"
            file_content = """Line 1: This is a test file
Line 2: This line will be replaced
Line 3: This is the last line"""
            
            # Create test file
            await client.call_tool("ssh_run", {
                "command": f"echo '{file_content}' > {test_file}",
                "io_timeout": 5.0
            })
            
            # Replace line
            replace_result = await client.call_tool("ssh_replace_line", {
                "path": test_file,
                "old_line": "Line 2: This line will be replaced",
                "new_line": "Line 2: This line has been replaced",
                "count": 1
            })
            assert json.loads(replace_result[0].text)['status'] == 'success'
            
            # Verify content
            cat_result = await client.call_tool("ssh_run", {
                "command": f"cat {test_file}",
                "io_timeout": 5.0
            })
            assert "Line 2: This line has been replaced" in json.loads(cat_result[0].text)['output']
            
        finally:
            await client.call_tool("ssh_run", {
                "command": f"rm -f {test_file}",
                "io_timeout": 5.0
            })
            await disconnect_ssh(client)
    
    print_test_footer()
