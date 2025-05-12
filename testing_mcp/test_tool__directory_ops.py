import pytest
import json
from conftest import print_test_header, print_test_footer, make_connection, disconnect_ssh
from mcp_ssh_server import mcp
from fastmcp import Client

@pytest.mark.asyncio
async def test_ssh_search_files(mcp_test_environment):
    """Test searching for files in directories."""
    print_test_header("Testing 'ssh_search_files' tool")
    
    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Connection failed"
            test_dir = "/tmp/ssh_test_search"
            
            # Setup test files
            await client.call_tool("ssh_run", {
                "command": f"""
                rm -rf {test_dir}
                mkdir -p {test_dir}/{{dir1,dir2,dir3}}
                touch {test_dir}/{{file1.txt,file2.log}}
                touch {test_dir}/dir1/file3.txt {test_dir}/dir2/file4.log
                """,
                "io_timeout": 10.0
            })

            # Test .txt files search
            result = await client.call_tool("ssh_search_files", {
                "path": test_dir,
                "pattern": "*.txt",
                "max_depth": None,
                "include_dirs": False
            })
            files = json.loads(result[0].text)
            paths = [f['path'] for f in files]
            assert all(f"{test_dir}/{p}" in paths for p in ["file1.txt", "dir1/file3.txt"])

            # Test .log files search
            result = await client.call_tool("ssh_search_files", {
                "path": test_dir,
                "pattern": "*.log",
                "max_depth": None,
                "include_dirs": False
            })
            assert len(json.loads(result[0].text)) >= 2
            
        finally:
            await client.call_tool("ssh_run", {"command": f"rm -rf {test_dir}", "io_timeout": 5.0})
            await disconnect_ssh(client)
    
    print_test_footer()

@pytest.mark.asyncio
async def test_ssh_directory_size(mcp_test_environment):
    """Test calculating directory size."""
    print_test_header("Testing 'ssh_directory_size' tool")
    
    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Connection failed"
            test_dir = "/tmp/ssh_test_size"
            
            await client.call_tool("ssh_run", {
                "command": f"""
                rm -rf {test_dir}
                mkdir -p {test_dir}
                dd if=/dev/zero of={test_dir}/file1.bin bs=1M count=1
                dd if=/dev/zero of={test_dir}/file2.bin bs=1M count=2
                sync
                """,
                "io_timeout": 10.0
            })

            result = await client.call_tool("ssh_directory_size", {"path": test_dir})
            size_data = json.loads(result[0].text)
            
            assert 'size_bytes' in size_data and 'size_human' in size_data
            assert size_data['size_bytes'] >= 3 * 1024 * 1024  # 3MB minimum
            
        finally:
            await client.call_tool("ssh_run", {"command": f"rm -rf {test_dir}", "io_timeout": 5.0})
            await disconnect_ssh(client)
    
    print_test_footer()

@pytest.mark.asyncio
async def test_ssh_list_directory(mcp_test_environment):
    """Test recursive directory listing."""
    print_test_header("Testing 'ssh_list_directory' tool")
    
    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Connection failed"
            test_dir = "/tmp/ssh_test_list"
            
            await client.call_tool("ssh_run", {
                "command": f"""
                rm -rf {test_dir}
                mkdir -p {test_dir}/dir1/subdir1 {test_dir}/dir2
                touch {test_dir}/{{file1.txt,dir1/file2.txt,dir1/subdir1/file3.txt,dir2/file4.txt}}
                """,
                "io_timeout": 10.0
            })

            # Test full recursive list
            result = await client.call_tool("ssh_list_directory", {"path": test_dir})
            entries = json.loads(result[0].text)
            paths = [e['path'] for e in entries]
            assert all(p in paths for p in [
                f"{test_dir}/dir1/subdir1/file3.txt",
                f"{test_dir}/dir1",
                f"{test_dir}/dir2"
            ])

            # Test depth-limited list
            result = await client.call_tool("ssh_list_directory", {
                "path": test_dir,
                "max_depth": 1
            })
            assert len(json.loads(result[0].text)) <= 4  # dir1, dir2, file1.txt
            
        finally:
            await client.call_tool("ssh_run", {"command": f"rm -rf {test_dir}", "io_timeout": 5.0})
            await disconnect_ssh(client)
    
    print_test_footer()
