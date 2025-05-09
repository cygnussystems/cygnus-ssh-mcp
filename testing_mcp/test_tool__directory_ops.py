import pytest
import json
import logging
from conftest import print_test_header, print_test_footer

# Configure logging
logger = logging.getLogger(__name__)

@pytest.mark.asyncio
async def test_ssh_search_files():
    """Test searching for files in directories."""
    print_test_header("Testing 'ssh_search_files' tool")
    logger.info("Starting SSH file search test")
    
    # Import necessary modules
    from mcp_ssh_server import mcp
    from fastmcp import Client
    
    # Use the Client context manager with the imported mcp instance
    async with Client(mcp) as client:
        # First, add the test server configuration
        from conftest import SSH_TEST_USER, SSH_TEST_PASSWORD, SSH_TEST_PORT
        
        try:
            # Ensure we have a connection
            try:
                await client.call_tool("ssh_status", {})
                logger.info("SSH connection already established")
            except Exception as e:
                if "No active SSH connection" in str(e):
                    # Add the test server configuration
                    logger.info("Adding test server configuration")
                    await client.call_tool("ssh_add_host", {
                        "name": "test_server",
                        "host": "localhost",
                        "user": SSH_TEST_USER,
                        "password": SSH_TEST_PASSWORD,
                        "port": SSH_TEST_PORT
                    })
                    
                    # Connect to the test server
                    logger.info("Connecting to test server")
                    await client.call_tool("ssh_connect", {
                        "host_name": "test_server"
                    })
                else:
                    raise
            
            # Create test directory structure
            test_dir = "/tmp/ssh_test_search"
            
            # Clean up any existing directory first
            try:
                await client.call_tool("ssh_run", {
                    "command": f"rm -rf {test_dir}",
                    "io_timeout": 5.0
                })
            except Exception as e:
                logger.warning(f"Error during cleanup: {e}")
            
            # Create test directory and files
            setup_commands = f"""
            mkdir -p {test_dir}/dir1 {test_dir}/dir2 {test_dir}/dir3
            touch {test_dir}/file1.txt {test_dir}/file2.log {test_dir}/dir1/file3.txt {test_dir}/dir2/file4.log
            """
            
            await client.call_tool("ssh_run", {
                "command": setup_commands,
                "io_timeout": 10.0
            })
            logger.info("Created test directory structure")
            
            # Test search_files
            logger.info(f"Searching for files in: {test_dir}")
            search_params = {
                "path": test_dir,
                "pattern": "*.txt",
                "max_depth": None,
                "include_dirs": False
            }
            
            search_result = await client.call_tool("ssh_search_files", search_params)
            logger.info(f"search_files result: {search_result}")
            
            # Verify search_files result
            search_json = json.loads(search_result[0].text)
            assert isinstance(search_json, list), "Search result should be a list"
            assert len(search_json) >= 2, "Should find at least 2 .txt files"
            
            # Verify file paths in results
            file_paths = [item['path'] for item in search_json]
            assert f"{test_dir}/file1.txt" in file_paths, "Should find file1.txt"
            assert f"{test_dir}/dir1/file3.txt" in file_paths, "Should find dir1/file3.txt"
            
            # Test with different pattern
            logger.info(f"Searching for .log files in: {test_dir}")
            search_params = {
                "path": test_dir,
                "pattern": "*.log",
                "max_depth": None,
                "include_dirs": False
            }
            
            search_result = await client.call_tool("ssh_search_files", search_params)
            search_json = json.loads(search_result[0].text)
            assert len(search_json) >= 2, "Should find at least 2 .log files"
            
            # Clean up
            await client.call_tool("ssh_run", {
                "command": f"rm -rf {test_dir}",
                "io_timeout": 5.0
            })
            logger.info(f"Cleaned up test directory: {test_dir}")
            
            logger.info("SSH file search test completed successfully")
        except Exception as e:
            logger.error(f"Error in SSH file search test: {e}")
            raise
    
    print_test_footer()

@pytest.mark.asyncio
async def test_ssh_directory_size():
    """Test calculating directory size."""
    print_test_header("Testing 'ssh_directory_size' tool")
    logger.info("Starting SSH directory size test")
    
    # Import necessary modules
    from mcp_ssh_server import mcp
    from fastmcp import Client
    
    # Use the Client context manager with the imported mcp instance
    async with Client(mcp) as client:
        # First, add the test server configuration
        from conftest import SSH_TEST_USER, SSH_TEST_PASSWORD, SSH_TEST_PORT
        
        try:
            # Ensure we have a connection
            try:
                await client.call_tool("ssh_status", {})
                logger.info("SSH connection already established")
            except Exception as e:
                if "No active SSH connection" in str(e):
                    # Add the test server configuration
                    logger.info("Adding test server configuration")
                    await client.call_tool("ssh_add_host", {
                        "name": "test_server",
                        "host": "localhost",
                        "user": SSH_TEST_USER,
                        "password": SSH_TEST_PASSWORD,
                        "port": SSH_TEST_PORT
                    })
                    
                    # Connect to the test server
                    logger.info("Connecting to test server")
                    await client.call_tool("ssh_connect", {
                        "host_name": "test_server"
                    })
                else:
                    raise
            
            # Create test directory with known content
            test_dir = "/tmp/ssh_test_size"
            
            # Clean up any existing directory first
            try:
                await client.call_tool("ssh_run", {
                    "command": f"rm -rf {test_dir}",
                    "io_timeout": 5.0
                })
            except Exception as e:
                logger.warning(f"Error during cleanup: {e}")
            
            # Create test directory with files of known size
            setup_commands = f"""
            mkdir -p {test_dir}
            dd if=/dev/zero of={test_dir}/file1.bin bs=1M count=1
            dd if=/dev/zero of={test_dir}/file2.bin bs=1M count=2
            """
            
            await client.call_tool("ssh_run", {
                "command": setup_commands,
                "io_timeout": 10.0
            })
            logger.info("Created test directory with files of known size")
            
            # Test directory_size
            logger.info(f"Calculating size of directory: {test_dir}")
            size_params = {
                "path": test_dir
            }
            
            size_result = await client.call_tool("ssh_directory_size", size_params)
            logger.info(f"directory_size result: {size_result}")
            
            # Verify directory_size result
            size_json = json.loads(size_result[0].text)
            assert 'size_bytes' in size_json, "Result should include size_bytes"
            assert 'size_human' in size_json, "Result should include human-readable size"
            
            # We expect at least 3MB (1MB + 2MB files)
            # But allow for some overhead in the filesystem
            assert size_json['size_bytes'] >= 3 * 1024 * 1024, "Directory should be at least 3MB"
            
            # Clean up
            await client.call_tool("ssh_run", {
                "command": f"rm -rf {test_dir}",
                "io_timeout": 5.0
            })
            logger.info(f"Cleaned up test directory: {test_dir}")
            
            logger.info("SSH directory size test completed successfully")
        except Exception as e:
            logger.error(f"Error in SSH directory size test: {e}")
            raise
    
    print_test_footer()

@pytest.mark.asyncio
async def test_ssh_list_directory():
    """Test recursive directory listing."""
    print_test_header("Testing 'ssh_list_directory' tool")
    logger.info("Starting SSH directory listing test")
    
    # Import necessary modules
    from mcp_ssh_server import mcp
    from fastmcp import Client
    
    # Use the Client context manager with the imported mcp instance
    async with Client(mcp) as client:
        # First, add the test server configuration
        from conftest import SSH_TEST_USER, SSH_TEST_PASSWORD, SSH_TEST_PORT
        
        try:
            # Ensure we have a connection
            try:
                await client.call_tool("ssh_status", {})
                logger.info("SSH connection already established")
            except Exception as e:
                if "No active SSH connection" in str(e):
                    # Add the test server configuration
                    logger.info("Adding test server configuration")
                    await client.call_tool("ssh_add_host", {
                        "name": "test_server",
                        "host": "localhost",
                        "user": SSH_TEST_USER,
                        "password": SSH_TEST_PASSWORD,
                        "port": SSH_TEST_PORT
                    })
                    
                    # Connect to the test server
                    logger.info("Connecting to test server")
                    await client.call_tool("ssh_connect", {
                        "host_name": "test_server"
                    })
                else:
                    raise
            
            # Create test directory structure
            test_dir = "/tmp/ssh_test_list"
            
            # Clean up any existing directory first
            try:
                await client.call_tool("ssh_run", {
                    "command": f"rm -rf {test_dir}",
                    "io_timeout": 5.0
                })
            except Exception as e:
                logger.warning(f"Error during cleanup: {e}")
            
            # Create test directory structure
            setup_commands = f"""
            mkdir -p {test_dir}/dir1/subdir1 {test_dir}/dir2
            touch {test_dir}/file1.txt {test_dir}/dir1/file2.txt {test_dir}/dir1/subdir1/file3.txt {test_dir}/dir2/file4.txt
            """
            
            await client.call_tool("ssh_run", {
                "command": setup_commands,
                "io_timeout": 10.0
            })
            logger.info("Created test directory structure")
            
            # Test list_directory
            logger.info(f"Listing directory recursively: {test_dir}")
            list_params = {
                "path": test_dir,
                "max_depth": None,
                "sudo": False
            }
            
            list_result = await client.call_tool("ssh_list_directory", list_params)
            logger.info(f"list_directory result: {list_result}")
            
            # Verify list_directory result
            list_json = json.loads(list_result[0].text)
            assert isinstance(list_json, list), "Directory listing should be a list"
            
            # Count the number of entries
            # We expect 7 entries: test_dir, dir1, dir2, subdir1, and 4 files
            assert len(list_json) >= 7, f"Should find at least 7 entries, found {len(list_json)}"
            
            # Verify specific paths are in the results
            paths = [item['path'] for item in list_json]
            assert f"{test_dir}/dir1/subdir1/file3.txt" in paths, "Should find nested file3.txt"
            assert f"{test_dir}/dir1" in paths, "Should find dir1"
            assert f"{test_dir}/dir2" in paths, "Should find dir2"
            
            # Test with limited depth
            logger.info(f"Listing directory with depth=1: {test_dir}")
            list_params = {
                "path": test_dir,
                "max_depth": 1,
                "sudo": False
            }
            
            list_result = await client.call_tool("ssh_list_directory", list_params)
            list_json = json.loads(list_result[0].text)
            
            # With depth=1, we should only see test_dir, dir1, dir2, and file1.txt
            # We shouldn't see anything in subdir1 or files in dir1/dir2
            assert len(list_json) <= 4, f"With depth=1, should find at most 4 entries, found {len(list_json)}"
            
            # Clean up
            await client.call_tool("ssh_run", {
                "command": f"rm -rf {test_dir}",
                "io_timeout": 5.0
            })
            logger.info(f"Cleaned up test directory: {test_dir}")
            
            logger.info("SSH directory listing test completed successfully")
        except Exception as e:
            logger.error(f"Error in SSH directory listing test: {e}")
            raise
    
    print_test_footer()
