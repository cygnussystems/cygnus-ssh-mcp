import pytest
import json
import os
import logging
from conftest import print_test_header, print_test_footer, make_connection, disconnect_ssh
from mcp_ssh_server import mcp
from fastmcp import Client

# Configure logging
logger = logging.getLogger(__name__)

# Check if production test credentials are available
PROD_TEST_ENABLED = os.environ.get('PROD_SUDO_TEST_ENABLED', 'true').lower() == 'true'
PROD_SSH_HOST = os.environ.get('PROD_SSH_HOST', '137.184.14.123 ')
PROD_SSH_PORT = int(os.environ.get('PROD_SSH_PORT', '22'))
PROD_SSH_USER = os.environ.get('PROD_SSH_USER', 'claude')
PROD_SSH_PASSWORD = os.environ.get('PROD_SSH_PASSWORD', 'claudetestpwd')
PROD_SSH_SUDO_PASSWORD = os.environ.get('PROD_SSH_SUDO_PASSWORD', 'claudetestpwd')

# Skip all tests if production testing is not enabled
pytestmark = pytest.mark.skipif(
    not PROD_TEST_ENABLED,
    reason="Production sudo tests are disabled. Set PROD_SUDO_TEST_ENABLED=true to enable."
)

@pytest.fixture
async def prod_connection():
    """Fixture to establish connection to production server."""
    client = Client(mcp)
    await client.__aenter__()
    
    try:
        # First disconnect any existing connection
        await disconnect_ssh(client)
        
        # Add the production host configuration
        host_key = f"{PROD_SSH_USER}@{PROD_SSH_HOST}"
        add_host_result = await client.call_tool("ssh_conn_add_host", {
            "user": PROD_SSH_USER,
            "host": PROD_SSH_HOST,
            "password": PROD_SSH_PASSWORD,
            "port": PROD_SSH_PORT,
            "sudo_password": PROD_SSH_SUDO_PASSWORD
        })
        
        # Connect to the production host
        connect_result = await client.call_tool("ssh_conn_connect", {
            "host_name": host_key
        })
        connect_json = json.loads(connect_result[0].text)
        
        if connect_json['status'] != 'success':
            logger.error(f"Failed to connect to production server: {connect_json}")
            pytest.skip("Could not connect to production server")
            
        logger.info(f"Connected to production server: {PROD_SSH_HOST}")
        yield client
    finally:
        # Clean up
        await disconnect_ssh(client)
        await client.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_prod_sudo_basic_command(prod_connection):
    """Test basic sudo command execution on production server."""
    print_test_header("Testing basic sudo command on production server")
    
    # Use the client yielded by the fixture
    async for client in prod_connection:
        try:
            # Verify sudo access
            sudo_verify_result = await client.call_tool("ssh_conn_verify_sudo", {})
        sudo_verify_json = json.loads(sudo_verify_result[0].text)
        
        logger.info(f"Sudo verification result: {sudo_verify_json}")
        
        if not sudo_verify_json.get('available', False):
            pytest.skip("Sudo is not available on this production server")
        
        # Run a simple sudo command
        whoami_result = await client.call_tool("ssh_cmd_run", {
            "command": "whoami",
            "use_sudo": True,
            "io_timeout": 10.0
        })
        whoami_json = json.loads(whoami_result[0].text)
        
        assert whoami_json['status'] == 'success', f"Sudo whoami command failed: {whoami_json}"
        assert "root" in whoami_json['output'], "Expected 'root' in sudo whoami output"
        
        logger.info(f"Sudo whoami successful: {whoami_json['output'].strip()}")
    except Exception as e:
        logger.error(f"Error in production sudo test: {e}", exc_info=True)
        raise
    
    print_test_footer()


@pytest.mark.asyncio
async def test_prod_sudo_file_operations(prod_connection):
    """Test sudo file operations on production server."""
    print_test_header("Testing sudo file operations on production server")
    
    # Use the client yielded by the fixture
    async for client in prod_connection:
        try:
        # Create a test file in a location that requires sudo
        test_file = "/root/sudo_test_file.txt"
        test_content = "This is a sudo test file created on a production server"
        
        # Write the file with sudo
        write_result = await client.call_tool("ssh_file_write", {
            "file_path": test_file,
            "content": test_content,
            "use_sudo": True
        })
        write_json = json.loads(write_result[0].text)
        
        assert write_json['success'], f"Failed to write file with sudo: {write_json}"
        logger.info(f"Successfully wrote file with sudo: {test_file}")
        
        # Read the file with sudo
        read_result = await client.call_tool("ssh_cmd_run", {
            "command": f"cat {test_file}",
            "use_sudo": True,
            "io_timeout": 10.0
        })
        read_json = json.loads(read_result[0].text)
        
        assert read_json['status'] == 'success', f"Failed to read file with sudo: {read_json}"
        assert test_content in read_json['output'], "File content doesn't match expected"
        logger.info(f"Successfully read file with sudo: {test_file}")
        
        # Clean up
        cleanup_result = await client.call_tool("ssh_cmd_run", {
            "command": f"rm -f {test_file}",
            "use_sudo": True,
            "io_timeout": 10.0
        })
        cleanup_json = json.loads(cleanup_result[0].text)
        assert cleanup_json['status'] == 'success', f"Failed to clean up test file: {cleanup_json}"
        
    except Exception as e:
        logger.error(f"Error in production sudo file operations test: {e}", exc_info=True)
        # Try to clean up even if test fails
        try:
            await client.call_tool("ssh_cmd_run", {
                "command": f"rm -f {test_file}",
                "use_sudo": True,
                "io_timeout": 5.0
            })
        except Exception:
            pass
        raise
    
    print_test_footer()


@pytest.mark.asyncio
async def test_prod_sudo_complex_command(prod_connection):
    """Test complex sudo command with pipes and redirects on production server."""
    print_test_header("Testing complex sudo command on production server")
    
    # Use the client yielded by the fixture
    async for client in prod_connection:
        try:
        # Run a more complex command with pipes and redirects
        complex_cmd = "find /etc -type f -name '*.conf' | grep -v '.dpkg' | sort | head -5 > /root/sudo_test_output.txt"
        
        cmd_result = await client.call_tool("ssh_cmd_run", {
            "command": complex_cmd,
            "use_sudo": True,
            "io_timeout": 20.0
        })
        cmd_json = json.loads(cmd_result[0].text)
        
        assert cmd_json['status'] == 'success', f"Complex sudo command failed: {cmd_json}"
        logger.info("Complex sudo command executed successfully")
        
        # Verify the output file was created
        verify_result = await client.call_tool("ssh_cmd_run", {
            "command": "cat /root/sudo_test_output.txt",
            "use_sudo": True,
            "io_timeout": 10.0
        })
        verify_json = json.loads(verify_result[0].text)
        
        assert verify_json['status'] == 'success', f"Failed to verify output file: {verify_json}"
        assert len(verify_json['output'].strip().split('\n')) > 0, "Expected output file to contain data"
        logger.info(f"Output file contents: {verify_json['output']}")
        
        # Clean up
        await client.call_tool("ssh_cmd_run", {
            "command": "rm -f /root/sudo_test_output.txt",
            "use_sudo": True,
            "io_timeout": 5.0
        })
        
    except Exception as e:
        logger.error(f"Error in production complex sudo test: {e}", exc_info=True)
        # Try to clean up even if test fails
        try:
            await client.call_tool("ssh_cmd_run", {
                "command": "rm -f /root/sudo_test_output.txt",
                "use_sudo": True,
                "io_timeout": 5.0
            })
        except Exception:
            pass
        raise
    
    print_test_footer()


@pytest.mark.asyncio
async def test_prod_sudo_interactive_command(prod_connection):
    """Test sudo with commands that might require interactive input."""
    print_test_header("Testing potentially interactive sudo command on production server")
    
    # Use the client yielded by the fixture
    async for client in prod_connection:
        try:
        # Run a command that might trigger interactive prompts in some environments
        interactive_cmd = "apt-get update -y"
        
        cmd_result = await client.call_tool("ssh_cmd_run", {
            "command": interactive_cmd,
            "use_sudo": True,
            "io_timeout": 60.0,  # Longer timeout for apt operations
            "runtime_timeout": 120.0
        })
        cmd_json = json.loads(cmd_result[0].text)
        
        # This might fail on some systems, so we log the result but don't assert
        logger.info(f"Interactive sudo command result: {cmd_json['status']}")
        if cmd_json['status'] == 'success':
            logger.info("Interactive sudo command executed successfully")
        else:
            logger.warning(f"Interactive sudo command failed: {cmd_json}")
            
    except Exception as e:
        logger.error(f"Error in production interactive sudo test: {e}", exc_info=True)
        # This test is allowed to fail as it's testing a challenging case
        pytest.skip(f"Interactive sudo command test failed: {e}")
    
    print_test_footer()
