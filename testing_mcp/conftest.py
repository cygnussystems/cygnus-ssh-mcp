import pytest
import asyncio
import sys
import os
import logging
import json
import subprocess
import time
from pathlib import Path

# Add project root to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

# Import necessary modules
from mcp_ssh_server import mcp, host_manager # Import host_manager for potential cleanup
from fastmcp import Client
from ssh_client import SshClient

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


# SSH test container management
async def setup_test_environment():
    """
    Set up the test environment by starting an SSH server container.
    Also ensures the test TOML config file is clean for SshHostManager.
    """
    global SSH_TEST_PORT
    logger = logging.getLogger("test_setup")
    logger.info("Setting up test environment")

    # Clean up any existing test TOML config file to ensure a fresh start for SshHostManager
    # This is important because SshHostManager might load an existing file from a previous run.
    test_config_path_project = Path("ssh_hosts.toml")
    test_config_path_home = Path.home() / ".ssh_hosts.toml"
    if test_config_path_project.exists():
        logger.info(f"Removing existing test config: {test_config_path_project}")
        test_config_path_project.unlink()
    if test_config_path_home.exists() and host_manager.config_path == test_config_path_home :
         # Only remove home if it's the one SshHostManager would actually use by default
        logger.info(f"Removing existing test config: {test_config_path_home}")
        test_config_path_home.unlink()
    # Re-initialize host_manager to ensure it creates/loads a fresh config
    # This assumes host_manager is the global instance from mcp_ssh_server
    # The current structure initializes host_manager at import time of mcp_ssh_server.
    # To ensure a fresh state for tests, we can re-initialize it here if needed,
    # or rely on the fact that if its default config file is removed, it will create a new one.
    # For robustness, explicitly re-instantiate or tell SshHostManager to reload.
    # For now, we'll assume file removal + SshHostManager's own _ensure_config_file is enough.
    # If mcp_ssh_server.host_manager is used globally by tests, it might need explicit reloading.
    # The most robust way is for SshHostManager to be instantiated by the test session or for
    # host_manager.config_path to be set to a temporary test-specific file.
    # For now, we rely on the default behavior after cleaning up potential default files.
    
    # Check if the ssh-test container is already running
    try:
        result = subprocess.run(
            ["docker", "ps", "--filter", "name=ssh-test-server", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            check=False # Don't check=True, handle empty output
        )
        
        if "ssh-test-server" in result.stdout:
            logger.info("SSH test container 'ssh-test-server' is already running")
            port_result = subprocess.run(
                ["docker", "port", "ssh-test-server", "22"],
                capture_output=True,
                text=True,
                check=True
            )
            if port_result.stdout.strip():
                port_mapping = port_result.stdout.strip()
                if ":" in port_mapping:
                    SSH_TEST_PORT = int(port_mapping.split(":")[-1])
                    # Update SSH_TEST_CONNECTION_PARAMS if port changed
                    SSH_TEST_CONNECTION_PARAMS["port"] = SSH_TEST_PORT
                    logger.info(f"Using existing container with port {SSH_TEST_PORT}")
            return
    except subprocess.CalledProcessError as e:
        logger.warning(f"Error checking for existing container: {e}")
    except FileNotFoundError:
        logger.error("Docker command not found. Please ensure Docker is installed and in PATH.")
        raise

    # Remove existing stopped container if any
    try:
        subprocess.run(["docker", "rm", "-f", "ssh-test-server"], check=False, capture_output=True)
    except FileNotFoundError:
        logger.error("Docker command not found. Cannot remove old containers.")
        raise # Docker is essential for this setup

    # Find an available port
    import socket
    original_port = SSH_TEST_PORT
    max_port_attempts = 10
    
    for attempt in range(max_port_attempts):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(('127.0.0.1', SSH_TEST_PORT))
            s.close()
            logger.info(f"Port {SSH_TEST_PORT} is available")
            break
        except socket.error:
            s.close()
            logger.warning(f"Port {SSH_TEST_PORT} is not available, trying next port {SSH_TEST_PORT + 1}")
            SSH_TEST_PORT += 1
            if attempt == max_port_attempts - 1:
                raise RuntimeError(f"Could not find an available port after {max_port_attempts} attempts, starting from {original_port}")
    
    if SSH_TEST_PORT != original_port:
        SSH_TEST_CONNECTION_PARAMS["port"] = SSH_TEST_PORT # Update if port changed
        logger.info(f"Using port {SSH_TEST_PORT} instead of {original_port}")
    
    # Start the SSH test container
    try:
        logger.info(f"Starting SSH test container 'ssh-test-server' on port {SSH_TEST_PORT}")
        docker_run_cmd = [
            "docker", "run", "-d",
            "--name", "ssh-test-server",
            "-p", f"{SSH_TEST_PORT}:22",
            "-e", f"USER_NAME={SSH_TEST_USER}",
            "-e", f"USER_PASSWORD={SSH_TEST_PASSWORD}",
            "-e", "SUDO_ACCESS=true",
            "-e", "PASSWORD_ACCESS=true",
            "linuxserver/openssh-server:latest"
        ]
        subprocess.run(docker_run_cmd, check=True)
        
        logger.info("Waiting for SSH server to be ready (approx. 5-10s)")
        time.sleep(10) # Increased wait time for container stability
        
        max_retries = 5
        retry_delay = 3 # Increased retry delay
        
        for attempt_conn in range(max_retries):
            try:
                # Use SshClient directly for initial check, not MCP tools yet
                temp_client = SshClient(
                    host=SSH_TEST_HOST,
                    user=SSH_TEST_USER,
                    port=SSH_TEST_PORT,
                    password=SSH_TEST_PASSWORD
                )
                result = temp_client.run("echo 'SSH connection test successful'")
                temp_client.close()
                if result.exit_code == 0:
                    logger.info("SSH test environment is ready.")
                    return
                else:
                    logger.warning(f"SSH connection test command failed with exit code {result.exit_code}.")
            except Exception as e:
                logger.warning(f"SSH connection attempt {attempt_conn+1}/{max_retries} to container failed: {e}")
            
            if attempt_conn < max_retries - 1:
                time.sleep(retry_delay)
            else:
                # Attempt to get container logs if connection fails
                try:
                    logs_result = subprocess.run(["docker", "logs", "ssh-test-server"], capture_output=True, text=True, check=False)
                    logger.error(f"SSH test server container logs:\n{logs_result.stdout}\n{logs_result.stderr}")
                except Exception as log_e:
                    logger.error(f"Could not retrieve container logs: {log_e}")
                raise RuntimeError(f"Failed to connect to SSH test server in container after {max_retries} attempts.")
    
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to start Docker container: {e}. Command: {' '.join(e.cmd)}. Output: {e.output}. Stderr: {e.stderr}")
        raise
    except FileNotFoundError:
        logger.error("Docker command not found. Please ensure Docker is installed and in PATH.")
        raise
    except Exception as e:
        logger.error(f"Failed to set up test environment: {e}")
        raise

async def teardown_test_environment():
    """
    Clean up the test environment by stopping and removing the SSH server container.
    """
    logger = logging.getLogger("test_teardown")
    logger.info("Tearing down test environment")
    
    try:
        result = subprocess.run(
            ["docker", "ps", "-a", "--filter", "name=ssh-test-server", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            check=False # Don't fail if no container
        )
        
        if "ssh-test-server" in result.stdout:
            logger.info("Stopping SSH test container 'ssh-test-server'")
            subprocess.run(["docker", "stop", "ssh-test-server"], check=False, capture_output=True)
            logger.info("Removing SSH test container 'ssh-test-server'")
            subprocess.run(["docker", "rm", "ssh-test-server"], check=False, capture_output=True)
            logger.info("SSH test container 'ssh-test-server' stopped and removed.")
        else:
            logger.info("SSH test container 'ssh-test-server' not found, no cleanup needed.")
    except FileNotFoundError:
        logger.warning("Docker command not found. Cannot stop/remove container. Manual cleanup might be needed.")
    except Exception as e:
        logger.error(f"Error during test environment teardown: {e}")


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

async def run_mcp_server_tests():
    """
    Run all MCP server tests in sequence.
    This function can be called to run the full MCP test suite.
    """
    logger = logging.getLogger("mcp_tests")
    logger.info("Starting MCP server test suite")
    
    # Set up the test environment (now handled by autouse fixture)
    # await setup_test_environment() 
    
    try:
        # Import test modules (ensure they are compatible with pytest-asyncio)
        # from testing_mcp.test_tool__run import test_ssh_run_basic, test_ssh_run_multiline, test_ssh_run_failure
        # from testing_mcp.test_mcp_status import test_ssh_status # Assuming this is test_tool__status.py
        # from testing_mcp.test_tool__history import test_ssh_command_history
        
        # It's generally better to let pytest discover and run tests.
        # This function might be for a custom test runner.
        # If using pytest, these direct calls are not standard.
        
        # Example of how you might run specific tests if needed, but pytest handles this.
        # logger.info("Running basic command tests (example, pytest usually handles this)")
        # client = Client(...) # Tests should create their own clients or use fixtures
        # await test_ssh_run_basic(client) # Assuming tests take a client
        # await test_ssh_run_multiline(client)
        # await test_ssh_run_failure(client)
        
        # logger.info("Running status tests (example)")
        # await test_ssh_status(client) # Assuming test_ssh_status is an async test function
        
        # logger.info("Running history tests (example)")
        # await test_ssh_command_history(client)
        
        logger.info("MCP server test suite execution function called. Pytest will run actual tests.")
        logger.info("If you intend to run tests programmatically here, ensure test functions are correctly called.")
                
    finally:
        # Clean up the test environment (now handled by autouse fixture)
        # await teardown_test_environment()
        pass
