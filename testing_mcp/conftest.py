import pytest
import asyncio
import sys
import os
import logging
import subprocess
import time
from pathlib import Path

# Add project root to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

# Import necessary modules
from mcp_ssh_server import mcp
from fastmcp import Client
from ssh_client import SshClient

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logging.getLogger('paramiko').setLevel(logging.WARNING)

# Test environment configuration
SSH_TEST_PORT = int(os.environ.get('SSH_TEST_PORT', 2222))
SSH_TEST_USER = os.environ.get('SSH_TEST_USER', 'testuser')
SSH_TEST_PASSWORD = os.environ.get('SSH_TEST_PASSWORD', 'testpass')

# Make SSH_TEST_PORT global so it can be modified if needed

# This allows running the tests with pytest
def pytest_configure(config):
    """Configure pytest."""
    # Register the asyncio marker
    config.addinivalue_line("markers", "asyncio: mark test as an asyncio test")
    
    # Set default fixture loop scope to function
    config._inicache["asyncio_default_fixture_loop_scope"] = "function"

# SSH test container management
async def setup_test_environment():
    """
    Set up the test environment by starting an SSH server container.
    """
    global SSH_TEST_PORT
    logger = logging.getLogger("test_setup")
    logger.info("Setting up test environment")
    
    # Check if the ssh-test container is already running
    try:
        result = subprocess.run(
            ["docker", "ps", "--filter", "name=ssh-test", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            check=True
        )
        
        if "ssh-test" in result.stdout:
            logger.info("SSH test container 'ssh-test' is already running")
            # Get the port mapping for the running container
            port_result = subprocess.run(
                ["docker", "port", "ssh-test", "22"],
                capture_output=True,
                text=True,
                check=True
            )
            if port_result.stdout.strip():
                # Extract port from output like "0.0.0.0:2222"
                port_mapping = port_result.stdout.strip()
                if ":" in port_mapping:
                    SSH_TEST_PORT = int(port_mapping.split(":")[-1])
                    logger.info(f"Using existing container with port {SSH_TEST_PORT}")
            return
    except subprocess.CalledProcessError as e:
        logger.warning(f"Error checking for existing container: {e}")
    
    # Check if the ssh-test-server container exists but is stopped
    try:
        result = subprocess.run(
            ["docker", "ps", "-a", "--filter", "name=ssh-test-server", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            check=True
        )
        
        if "ssh-test-server" in result.stdout:
            logger.info("SSH test container 'ssh-test-server' exists but is stopped or has port conflict, removing it")
            subprocess.run(["docker", "rm", "-f", "ssh-test-server"], check=True)
    except subprocess.CalledProcessError as e:
        logger.warning(f"Error checking for existing stopped container: {e}")
    
    # Find an available port
    import socket
    original_port = SSH_TEST_PORT
    max_port_attempts = 10
    
    for attempt in range(max_port_attempts):
        # Check if the current port is available
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(('127.0.0.1', SSH_TEST_PORT))
            s.close()
            logger.info(f"Port {SSH_TEST_PORT} is available")
            break
        except socket.error:
            s.close()
            logger.warning(f"Port {SSH_TEST_PORT} is not available, trying next port")
            SSH_TEST_PORT += 1
            
            # If we've tried too many ports, give up
            if attempt == max_port_attempts - 1:
                raise RuntimeError(f"Could not find an available port after {max_port_attempts} attempts")
    
    if SSH_TEST_PORT != original_port:
        logger.info(f"Using port {SSH_TEST_PORT} instead of {original_port}")
    
    # Start the SSH test container
    try:
        logger.info(f"Starting SSH test container on port {SSH_TEST_PORT}")
        subprocess.run(
            [
                "docker", "run", "-d",
                "--name", "ssh-test-server",
                "-p", f"{SSH_TEST_PORT}:22",
                "-e", f"USER_NAME={SSH_TEST_USER}",
                "-e", f"USER_PASSWORD={SSH_TEST_PASSWORD}",
                "-e", "SUDO_ACCESS=true",
                "-e", "PASSWORD_ACCESS=true",
                "linuxserver/openssh-server:latest"
            ],
            check=True
        )
        
        # Wait for the container to be ready
        logger.info("Waiting for SSH server to be ready")
        time.sleep(5)  # Give the container time to initialize
        
        # Test the SSH connection
        max_retries = 5
        retry_delay = 2
        
        for attempt in range(max_retries):
            try:
                client = SshClient(
                    host='localhost',
                    user=SSH_TEST_USER,
                    port=SSH_TEST_PORT,
                    password=SSH_TEST_PASSWORD
                )
                
                # Run a simple command to verify the connection
                result = client.run("echo 'SSH connection test'")
                logger.info(f"SSH connection test result: {result.exit_code}")
                client.close()
                
                logger.info("SSH test environment is ready")
                return
            except Exception as e:
                logger.warning(f"SSH connection attempt {attempt+1}/{max_retries} failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                else:
                    raise RuntimeError(f"Failed to connect to SSH test server after {max_retries} attempts")
    
    except Exception as e:
        logger.error(f"Failed to set up test environment: {e}")
        raise

async def teardown_test_environment():
    """
    Clean up the test environment by stopping and removing the SSH server container.
    """
    logger = logging.getLogger("test_teardown")
    logger.info("Tearing down test environment")
    
    # Stop and remove the container
    try:
        # Check if the container exists
        result = subprocess.run(
            ["docker", "ps", "-a", "--filter", "name=ssh-test-server", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            check=True
        )
        
        if "ssh-test-server" in result.stdout:
            # Stop the container
            logger.info("Stopping SSH test container")
            subprocess.run(["docker", "stop", "ssh-test-server"], check=True)
            
            # Remove the container
            logger.info("Removing SSH test container")
            subprocess.run(["docker", "rm", "ssh-test-server"], check=True)
            
            logger.info("SSH test environment cleaned up")
        else:
            logger.info("SSH test container not found, nothing to clean up")
    
    except Exception as e:
        logger.error(f"Error during test environment teardown: {e}")
        # Don't raise the exception to allow tests to complete

async def get_mcp_client():
    """
    Get a client connected to the MCP server.
    
    Returns:
        A connected MCP client
    """
    logger = logging.getLogger("test_client")
    logger.info("Creating MCP client")
    
    # Create a client connected to the MCP server
    client = Client(mcp)
    await client.connect()
    
    # Set up the SSH connection if not already established
    try:
        # First check if there's already an active connection
        try:
            await client.call_tool("ssh_status", {})
            logger.info("SSH connection already established")
        except Exception as e:
            if "No active SSH connection" in str(e):
                # Connect to the test SSH server
                logger.info("Establishing SSH connection")
                await client.call_tool("ssh_connect", {
                    "host_name": "test_server"
                })
                logger.info("SSH connection established")
            else:
                raise
    except Exception as e:
        logger.error(f"Failed to set up SSH connection: {e}")
        # Add the test server configuration
        try:
            logger.info("Adding test server configuration")
            await client.call_tool("ssh_add_host", {
                "name": "test_server",
                "host": "localhost",
                "user": SSH_TEST_USER,
                "password": SSH_TEST_PASSWORD,
                "port": SSH_TEST_PORT
            })
            
            # Now connect to the test server
            logger.info("Connecting to test server")
            await client.call_tool("ssh_connect", {
                "host_name": "test_server"
            })
            logger.info("SSH connection established")
        except Exception as e2:
            logger.error(f"Failed to add and connect to test server: {e2}")
            raise
    
    return client

@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for each test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

@pytest.fixture(scope="session")
async def mcp_test_environment():
    """Session-wide test environment setup and teardown."""
    await setup_test_environment()
    yield
    await teardown_test_environment()

@pytest.fixture(scope="function")
async def mcp_client(mcp_test_environment):
    """Fixture to provide a connected MCP client."""
    client = await get_mcp_client()
    yield client
    # Clean up after the test
    if hasattr(client, 'close'):
        await client.close()

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
    
    # Set up the test environment
    await setup_test_environment()
    
    try:
        # Import test modules
        from testing_mcp.test_mcp_run_commands import test_ssh_run_basic, test_ssh_run_multiline, test_ssh_run_failure
        from testing_mcp.test_mcp_status import test_ssh_status
        from testing_mcp.test_mcp_history import test_ssh_command_history
        from testing_mcp.test_mcp_server_tools import test_tool_listing, test_ssh_add_host, test_ssh_connect_parameters
        
        # Create a client
        client = await get_mcp_client()
        
        try:
            # Run the tests
            logger.info("Running basic command tests")
            await test_ssh_run_basic(client)
            await test_ssh_run_multiline(client)
            await test_ssh_run_failure(client)
            
            logger.info("Running status tests")
            await test_ssh_status({"client": client})
            
            logger.info("Running history tests")
            await test_ssh_command_history()
            
            logger.info("Running tool tests")
            await test_tool_listing(client)
            await test_ssh_add_host(None, client)
            await test_ssh_connect_parameters(client)
            
            logger.info("All MCP server tests completed successfully")
            
        finally:
            # Close the client
            if hasattr(client, 'close'):
                await client.close()
                
    finally:
        # Clean up the test environment
        await teardown_test_environment()
