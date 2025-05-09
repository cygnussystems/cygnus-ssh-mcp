import pytest
import json
import logging
from conftest import print_test_header, print_test_footer
import os
import tempfile

# Configure logging
logger = logging.getLogger(__name__)

@pytest.mark.asyncio
async def test_ssh_file_transfer():
    """Test file upload and download operations."""
    print_test_header("Testing 'ssh_file_transfer' tool")
    logger.info("Starting SSH file transfer test")
    
    # Import necessary modules
    from mcp_ssh_server import mcp
    from fastmcp import Client
    
    # Use the Client context manager with the imported mcp instance
    async with Client(mcp) as client:
        # First, add the test server configuration
        from conftest import SSH_TEST_USER, SSH_TEST_PASSWORD, SSH_TEST_PORT
        
        try:
            # Try to run a command first (might fail if no connection)
            try:
                # Simple status check
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
            
            # Create a temporary file for upload
            with tempfile.NamedTemporaryFile(mode='w+', delete=False) as temp_file:
                temp_file.write("This is a test file for SSH transfer")
                local_path = temp_file.name
            
            # Define remote path
            remote_path = "/tmp/ssh_test_upload.txt"
            
            try:
                # Test file upload
                logger.info(f"Uploading file from {local_path} to {remote_path}")
                upload_params = {
                    "direction": "upload",
                    "local_path": local_path,
                    "remote_path": remote_path
                }
                
                upload_result = await client.call_tool("ssh_file_transfer", upload_params)
                logger.info(f"Upload result: {upload_result}")
                
                # Verify upload result
                assert upload_result is not None, "Expected non-empty result"
                assert isinstance(upload_result, list), f"Expected list result, got {type(upload_result)}"
                assert len(upload_result) > 0, "Expected non-empty list result"
                
                # Parse the JSON response
                upload_json = json.loads(upload_result[0].text)
                assert upload_json['success'] is True, "Upload should be successful"
                
                # Create a new temporary file for download
                download_local_path = os.path.join(tempfile.gettempdir(), "ssh_test_download.txt")
                
                # Test file download
                logger.info(f"Downloading file from {remote_path} to {download_local_path}")
                download_params = {
                    "direction": "download",
                    "local_path": download_local_path,
                    "remote_path": remote_path
                }
                
                download_result = await client.call_tool("ssh_file_transfer", download_params)
                logger.info(f"Download result: {download_result}")
                
                # Verify download result
                download_json = json.loads(download_result[0].text)
                assert download_json['success'] is True, "Download should be successful"
                
                # Verify the downloaded file content
                with open(download_local_path, 'r') as f:
                    content = f.read()
                    assert "This is a test file for SSH transfer" in content, "Downloaded file content doesn't match"
                
                logger.info("SSH file transfer test completed successfully")
            finally:
                # Clean up temporary files
                if os.path.exists(local_path):
                    os.unlink(local_path)
                if os.path.exists(download_local_path):
                    os.unlink(download_local_path)
                
                # Clean up remote file
                try:
                    await client.call_tool("ssh_run", {
                        "command": f"rm -f {remote_path}",
                        "io_timeout": 5.0
                    })
                except Exception as e:
                    logger.warning(f"Failed to clean up remote file: {e}")
                
        except Exception as e:
            logger.error(f"Error in SSH file transfer test: {e}")
            raise
    
    print_test_footer()

@pytest.mark.asyncio
async def test_ssh_mkdir_rmdir():
    """Test directory creation and removal operations."""
    print_test_header("Testing 'ssh_mkdir' and 'ssh_rmdir' tools")
    logger.info("Starting SSH directory operations test")
    
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
            
            # Test directory path
            test_dir = "/tmp/ssh_test_dir"
            
            # Clean up any existing directory first
            try:
                await client.call_tool("ssh_rmdir", {
                    "path": test_dir,
                    "recursive": True
                })
                logger.info(f"Cleaned up existing directory: {test_dir}")
            except Exception as e:
                if "No active SSH connection" not in str(e):
                    logger.warning(f"Error during cleanup: {e}")
            
            # Test mkdir
            logger.info(f"Creating directory: {test_dir}")
            mkdir_params = {
                "path": test_dir,
                "mode": 0o755
            }
            
            mkdir_result = await client.call_tool("ssh_mkdir", mkdir_params)
            logger.info(f"mkdir result: {mkdir_result}")
            
            # Verify mkdir result
            assert mkdir_result is not None, "Expected non-empty result"
            mkdir_json = json.loads(mkdir_result[0].text)
            assert mkdir_json['status'] == 'success', "Directory creation should be successful"
            
            # Test listdir
            logger.info(f"Listing directory: {test_dir}")
            listdir_result = await client.call_tool("ssh_listdir", {
                "path": test_dir
            })
            logger.info(f"listdir result: {listdir_result}")
            
            # Verify listdir result
            assert listdir_result is not None, "Expected non-empty result"
            listdir_json = json.loads(listdir_result[0].text)
            assert isinstance(listdir_json, list), "Directory listing should be a list"
            
            # Test rmdir
            logger.info(f"Removing directory: {test_dir}")
            rmdir_params = {
                "path": test_dir,
                "recursive": False
            }
            
            rmdir_result = await client.call_tool("ssh_rmdir", rmdir_params)
            logger.info(f"rmdir result: {rmdir_result}")
            
            # Verify rmdir result
            rmdir_json = json.loads(rmdir_result[0].text)
            assert rmdir_json['status'] == 'success', "Directory removal should be successful"
            
            logger.info("SSH directory operations test completed successfully")
        except Exception as e:
            logger.error(f"Error in SSH directory operations test: {e}")
            raise
    
    print_test_footer()

@pytest.mark.asyncio
async def test_ssh_replace_line():
    """Test file content replacement operations."""
    print_test_header("Testing 'ssh_replace_line' tool")
    logger.info("Starting SSH file content replacement test")
    
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
            
            # Test file path
            test_file = "/tmp/ssh_test_file.txt"
            
            # Create a test file with content
            file_content = """Line 1: This is a test file
Line 2: This line will be replaced
Line 3: This is the last line"""
            
            await client.call_tool("ssh_run", {
                "command": f"echo '{file_content}' > {test_file}",
                "io_timeout": 5.0
            })
            logger.info(f"Created test file: {test_file}")
            
            # Test replace_line
            logger.info(f"Replacing line in file: {test_file}")
            replace_params = {
                "path": test_file,
                "old_line": "Line 2: This line will be replaced",
                "new_line": "Line 2: This line has been replaced",
                "count": 1
            }
            
            replace_result = await client.call_tool("ssh_replace_line", replace_params)
            logger.info(f"replace_line result: {replace_result}")
            
            # Verify replace_line result
            replace_json = json.loads(replace_result[0].text)
            assert replace_json['status'] == 'success', "Line replacement should be successful"
            
            # Verify the file content was changed
            cat_result = await client.call_tool("ssh_run", {
                "command": f"cat {test_file}",
                "io_timeout": 5.0
            })
            
            cat_json = json.loads(cat_result[0].text)
            assert "Line 2: This line has been replaced" in cat_json['output'], "File content wasn't properly replaced"
            
            # Clean up
            await client.call_tool("ssh_run", {
                "command": f"rm -f {test_file}",
                "io_timeout": 5.0
            })
            logger.info(f"Cleaned up test file: {test_file}")
            
            logger.info("SSH file content replacement test completed successfully")
        except Exception as e:
            logger.error(f"Error in SSH file content replacement test: {e}")
            raise
    
    print_test_footer()
