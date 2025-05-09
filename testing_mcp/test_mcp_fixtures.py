import asyncio
import sys
import os
import tempfile
import yaml
from pathlib import Path
import logging

# Ensure the main project directory is in the Python path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

# Configure logging first
logging.basicConfig(level=logging.INFO, 
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("mcp_test")

try:
    from fastmcp import Client
    import mcp_ssh_server
    from mcp_ssh_server import mcp, SshHostManager
    from ssh_models import SshError
    from testing.conftest import get_client, cleanup_client, print_test_header, print_test_footer
except ImportError as e:
    logger.error(f"FATAL: Failed to import required modules. Error: {e}")
    print(f"FATAL: Failed to import required modules. Error: {e}", file=sys.stderr)
    print("Make sure fastmcp is installed and you are running from the correct directory.", file=sys.stderr)
    sys.exit(1)

# Configure logging
logging.basicConfig(level=logging.INFO, 
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("mcp_test")

# Global variables to store test state
ssh_client = None
host_manager = None
config_path = None

async def setup_test_environment():
    """Set up the test environment with a real SSH connection."""
    global ssh_client, host_manager, config_path
    
    logger.info("Setting up test environment...")
    
    # Create a temporary config file for testing
    with tempfile.NamedTemporaryFile(mode='w', delete=False) as tmp:
        yaml.safe_dump({'hosts': []}, tmp)
        config_path = Path(tmp.name)
    
    # Initialize host manager with the temp config
    host_manager = SshHostManager(config_path=config_path)
    
    # Get a real SSH client connection to the test container
    ssh_client = get_client()
    
    # Add test host to the host manager (this is just for testing the add_host functionality)
    host_manager.add_host(
        'test_docker', 
        ssh_client.host, 
        ssh_client.port, 
        ssh_client.user, 
        ssh_client.password or ''
    )
    
    # Set the global SSH client in the MCP server
    import mcp_ssh_server
    mcp_ssh_server.ssh_client = ssh_client
    
    logger.info(f"Test environment set up with SSH connection to {ssh_client.host}:{ssh_client.port}")
    return True

async def teardown_test_environment():
    """Clean up the test environment."""
    global ssh_client, config_path
    
    logger.info("Tearing down test environment...")
    
    # Close the SSH client
    if ssh_client:
        cleanup_client(ssh_client)
        ssh_client = None
    
    # Clean up the temporary config file
    if config_path and config_path.exists():
        config_path.unlink()
    
    # Reset the global SSH client in the MCP server
    import mcp_ssh_server
    mcp_ssh_server.ssh_client = None
    
    logger.info("Test environment cleaned up")

async def get_mcp_client():
    """Get a FastMCP client for testing."""
    client = Client(mcp)
    # Ensure the client is properly initialized
    await client.connect()
    return client
