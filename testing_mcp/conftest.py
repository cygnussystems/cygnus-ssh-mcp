import pytest
import asyncio
import sys
import os
import logging
import json

from mcp_ssh_server import mcp

from docker_manager import setup_test_environment, teardown_test_environment

# Add project root to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)



# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logging.getLogger('paramiko').setLevel(logging.WARNING)
logging.getLogger('PIL').setLevel(logging.WARNING) # To silence potential Pillow debug logs if it's a dependency of something


# Test environment configuration
SSH_TEST_PORT = int(os.environ.get('SSH_TEST_PORT', 2222))
SSH_TEST_USER = os.environ.get('SSH_TEST_USER', 'testuser')
SSH_TEST_PASSWORD = os.environ.get('SSH_TEST_PASSWORD', 'testpass')
SSH_TEST_HOST = "localhost" # Define explicitly for clarity

# Standard SSH connection parameters for tests
# This dictionary is no longer directly passed to add_host tool,
# but its values are used.

SSH_TEST_CONNECTION_PARAMS = {
    "host": SSH_TEST_HOST,
    "user": SSH_TEST_USER,
    "password": SSH_TEST_PASSWORD,
    "port": SSH_TEST_PORT
}


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
        is_connected_json = json.loads(is_connected_result[0].text)
        return is_connected_json
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
        "port": SSH_TEST_PORT
    }
    await client.call_tool("ssh_conn_add_host", add_host_params)
    
    # Connect to the test server using the 'user@host' key
    host_key_for_connection = f"{SSH_TEST_USER}@{SSH_TEST_HOST}"
    logging.info(f"Connecting to test server using key: {host_key_for_connection}")
    connect_params = {
        "host_name": host_key_for_connection
    }
    connect_result = await client.call_tool("ssh_conn_connect", connect_params)
    connect_json = json.loads(connect_result[0].text)
    
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

@pytest.fixture(scope="session", autouse=True) # Autouse to ensure it runs for the session
async def mcp_test_environment():
    """Session-wide test environment setup and teardown."""
    await setup_test_environment()
    yield
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
