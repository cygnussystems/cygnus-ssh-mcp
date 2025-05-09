import os
import sys
import logging
import pytest
from ssh_client import SshClient

# Add project root to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logging.getLogger('paramiko').setLevel(logging.WARNING)

# Configuration
SSH_HOST = os.environ.get('SSH_TEST_HOST', 'localhost')
SSH_PORT = int(os.environ.get('SSH_TEST_PORT', 2222))
SSH_USER = os.environ.get('SSH_TEST_USER', 'testuser')
SSH_PASSWORD = os.environ.get('SSH_TEST_PASSWORD', 'testpass')
SSH_KEYFILE = None
TEST_SUDO_PASSWORD = os.environ.get('SSH_TEST_SUDO_PASSWORD', 'testpass')

# Client cache
_client_cache = None

def get_client(force_new=False, **kwargs):
    """Get SSH client configured for Linux testing only"""
    # Set default connection parameters
    default_kwargs = dict(
        host=SSH_HOST,
        port=SSH_PORT,
        user=SSH_USER,
        password=SSH_PASSWORD,
        keyfile=SSH_KEYFILE,
        sudo_password=None,
        connect_timeout=15
    )
    default_kwargs.update(kwargs)
    
    # Create and verify client
    client = SshClient(**default_kwargs)
    if client.os_type != 'linux':
        raise RuntimeError(f"Tests must run against Linux systems, but detected {client.os_type}")
    return client

def cleanup_client(client):
    """Close the client connection."""
    if client:
        print("\nClosing client connection...")
        client.close()

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

@pytest.fixture(scope="module")
def ssh_client():
    """Pytest fixture to provide a connected SshClient instance."""
    print("\n--- Setting up SSH client fixture ---")
    client = get_client(force_new=True)
    yield client
    print("\n--- Tearing down SSH client fixture ---")
    cleanup_client(client)
