import pytest
import asyncio
import sys
import os
import logging
import json
import time

from mcp_ssh_server import mcp

# Hardcoded to use VM (Debian 12 at 192.168.1.27) instead of Docker
USE_VM = True

if not USE_VM:
    from docker_manager import docker_test_environment, teardown_test_environment

# Synchronous VM workspace setup (called directly, not via async fixture)
def setup_vm_workspace():
    """Create and clean workspace directory on VM using paramiko directly."""
    import paramiko
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(SSH_TEST_HOST, port=SSH_TEST_PORT, username=SSH_TEST_USER, password=SSH_TEST_PASSWORD)

        # Create workspace directory, ensure correct ownership, and clean it
        workspace = f"/home/{SSH_TEST_USER}/mcp_test_workspace"
        # Use sudo to create and fix ownership if needed
        cmd = f"mkdir -p {workspace} && echo {SSH_TEST_PASSWORD} | sudo -S chown {SSH_TEST_USER}:{SSH_TEST_USER} {workspace} 2>/dev/null; rm -rf {workspace}/* 2>/dev/null; echo 'Workspace ready'"
        stdin, stdout, stderr = client.exec_command(cmd)
        result = stdout.read().decode().strip()
        logging.info(f"VM workspace setup: {result}")
        client.close()
    except Exception as e:
        logging.error(f"Failed to setup VM workspace: {e}")
        raise

# Create a wrapper function that supplies default arguments
async def setup_test_environment():
    """Wrapper around docker_test_environment that supplies default arguments.
    If USE_VM=1, skips Docker setup and creates workspace directory on VM.
    """
    if USE_VM:
        logging.info(f"USE_VM=1: Skipping Docker setup, using VM at {SSH_TEST_HOST}:{SSH_TEST_PORT}")
        # Workspace setup is done synchronously before tests start
        return
    return await docker_test_environment(
        user=SSH_TEST_USER,
        password=SSH_TEST_PASSWORD,
        host=SSH_TEST_HOST,
        base_port=SSH_TEST_PORT
    )

# Add project root to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)



# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logging.getLogger('paramiko').setLevel(logging.INFO)  # Increase Paramiko logging level to help diagnose SSH issues
logging.getLogger('PIL').setLevel(logging.WARNING) # To silence potential Pillow debug logs if it's a dependency of something


# Test environment configuration
# Defaults differ based on whether we're using VM or Docker
if USE_VM:
    # VM defaults (Debian 12 VM at 192.168.1.27)
    SSH_TEST_HOST = os.environ.get('SSH_TEST_HOST', '192.168.1.27')
    SSH_TEST_PORT = int(os.environ.get('SSH_TEST_PORT', 22))
    SSH_TEST_USER = os.environ.get('SSH_TEST_USER', 'test')
    SSH_TEST_PASSWORD = os.environ.get('SSH_TEST_PASSWORD', 'testpwd')
else:
    # Docker defaults
    SSH_TEST_HOST = os.environ.get('SSH_TEST_HOST', 'localhost')
    SSH_TEST_PORT = int(os.environ.get('SSH_TEST_PORT', 2222))
    SSH_TEST_USER = os.environ.get('SSH_TEST_USER', 'testuser')
    SSH_TEST_PASSWORD = os.environ.get('SSH_TEST_PASSWORD', 'testpass')

# Run VM workspace setup at module load time if using VM (after variables are defined)
if USE_VM:
    setup_vm_workspace()

# Test workspace directory (wiped at start of each session for clean slate)
# Path dynamically constructed based on the user
TEST_WORKSPACE = f"/home/{SSH_TEST_USER}/mcp_test_workspace"
# Helper functions for SSH connection management
async def disconnect_ssh(client):
    """
    Disconnect any existing SSH connection.
    Args:
        client: The MCP client instance
    """
    if await is_ssh_connected(client):
        logger = logging.getLogger("test_cleanup")
        logger.info("Disconnecting existing SSH connection")
        try:
            if mcp.ssh_client:
                mcp.ssh_client.close()
                mcp.ssh_client = None
                logger.info("SSH connection closed successfully")
        except Exception as e:
            logger.error(f"Error disconnecting SSH: {e}")



def extract_result_text(result):
    """
    Extract text from a tool result, handling both old list API and new CallToolResult API.
    Args:
        result: The result from client.call_tool()
    Returns:
        str: The text content of the result, or None if not available
    """
    # New API: result has .content attribute
    if hasattr(result, 'content') and len(result.content) > 0:
        return result.content[0].text
    # Old API: result is a list
    elif isinstance(result, list) and len(result) > 0:
        if hasattr(result[0], 'text'):
            return result[0].text
    return None


async def is_ssh_connected(client):
    """
    Check if SSH is connected using the ssh_conn_is_connected tool.
    Args:
        client: The MCP client instance
    Returns:
        bool: True if connected, False otherwise
    """
    try:
        is_connected_result = await client.call_tool("ssh_conn_is_connected", {})
        result_text = extract_result_text(is_connected_result)
        if result_text:
            is_connected_json = json.loads(result_text)
            return is_connected_json
        return False
    except Exception as e:
        logging.error(f"Error checking SSH connection: {e}")
        return False



async def make_connection(client):
    """
    Ensure an SSH connection exists, creating one if needed.
    Uses the new TOML-based host configuration.
    Args:
        client: The MCP client instance
    Returns:
        bool: True if connection was successful
    """
    # Check if already connected
    if await is_ssh_connected(client):
        logging.info("SSH connection already established")
        return True
        
    # Add host configuration using individual parameters
    logging.info(f"Adding test server configuration for {SSH_TEST_USER}@{SSH_TEST_HOST}")
    add_host_params = {
        "user": SSH_TEST_USER,
        "host": SSH_TEST_HOST,
        "password": SSH_TEST_PASSWORD,
        "port": SSH_TEST_PORT,
        "sudo_password": SSH_TEST_PASSWORD  # Use the same password for sudo
    }
    await client.call_tool("ssh_conn_add_host", add_host_params)
    
    # Connect to the test server using the 'user@host' key
    host_key_for_connection = f"{SSH_TEST_USER}@{SSH_TEST_HOST}"
    logging.info(f"Connecting to test server using key: {host_key_for_connection}")
    connect_params = {
        "host_name": host_key_for_connection
    }
    connect_result = await client.call_tool("ssh_conn_connect", connect_params)
    result_text = extract_result_text(connect_result)
    if not result_text:
        logging.error("Failed to get connection result")
        return False
    connect_json = json.loads(result_text)
    
    # Verify connection was successful
    if connect_json.get('status') == 'success':
        logging.info(f"SSH connection established successfully to {connect_json.get('connected_to')}")
        return True
    else:
        logging.error(f"Failed to establish SSH connection: {connect_json}")
        return False



# This allows running the tests with pytest
def pytest_configure(config):
    """Configure pytest."""
    # Register the asyncio marker
    config.addinivalue_line("markers", "asyncio: mark test as an asyncio test")
    
    # Set default fixture loop scope to function
    # Note: pytest-asyncio might handle this differently in newer versions.
    # If using a recent pytest-asyncio, this might not be needed or might be set via pytest.ini.
    if hasattr(config, '_inicache'): # Check for older pytest versions
        config._inicache["asyncio_default_fixture_loop_scope"] = "function"




@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for each test session."""
    # Standard way to get event loop for pytest-asyncio
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    yield loop
    loop.close()

@pytest.fixture(scope="session", autouse=True)
async def mcp_test_environment():
    """Session-wide test environment setup and teardown."""
    global SSH_TEST_PORT  # Access the global variable
    try:
        # Call setup_test_environment
        await setup_test_environment()
        # Wait a bit longer to ensure the container is fully ready
        await asyncio.sleep(5)

        # Log the port being used for debugging
        logging.info(f"Test environment setup complete. Using SSH port: {SSH_TEST_PORT}")

        yield
    except Exception as e:
        logging.error(f"Error setting up test environment: {e}", exc_info=True)
        raise
    finally:
        if not USE_VM:
            await teardown_test_environment()




def print_test_header(test_name):
    """Print a formatted test header."""
    print("\n" + "=" * 40)
    print(f"Running test: {test_name}")
    print("=" * 40)

def print_test_footer():
    """Print a formatted test footer."""
    print("\n" + "=" * 40)
    print("Test completed")
    print("=" * 40 + "\n")


def remote_temp_path(base_name):
    """Generate a temporary path on the remote system with timestamp to avoid collisions."""
    timestamp = int(time.time())
    return f"{TEST_WORKSPACE}/{base_name}_{timestamp}"
