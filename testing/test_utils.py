import os
import sys
import logging
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
    """
    Get a connected SSH client instance.
    Caches the client by default unless force_new=True.
    """
    global _client_cache
    
    if not force_new and _client_cache:
        try:
            if _client_cache._client.is_active():
                print("Reusing cached client connection.")
                return _client_cache
        except Exception:
            print("Error checking cached client, creating new.")
            _client_cache = None

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
    
    print(f"Connecting to {default_kwargs['user']}@{default_kwargs['host']}:{default_kwargs['port']}...")
    client = SshClient(**default_kwargs)
    print("Connection successful.")
    
    if not force_new:
        _client_cache = client
        
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
