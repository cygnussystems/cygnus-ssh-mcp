import sys
import os
import time
import tempfile
import pytest # Using pytest features like raises
import threading # Needed for busy test (though busy test moved)
import shlex # Import shlex module
import logging # Added for testing log output (optional)

# Add project root to path to import SshClient
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

# Ensure ssh_client module can be found
try:
    # Import necessary exceptions used in this file
    from ssh_client import (
        SshClient, CommandFailed, CommandTimeout, CommandRuntimeTimeout,
        BusyError, OutputPurged, TaskNotFound, SshError, SudoRequired
    )
except ImportError as e:
    print(f"Error importing SshClient: {e}")
    print(f"Project root added to path: {project_root}")
    print(f"Current sys.path: {sys.path}")
    sys.exit(1)

# Configure logging for tests (optional, useful for debugging)
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logging.getLogger('paramiko').setLevel(logging.WARNING) # Quieten paramiko's verbose logs

# --- Configuration (Adjust as per your Docker setup) ---
SSH_HOST = os.environ.get('SSH_TEST_HOST', 'localhost')
SSH_PORT = int(os.environ.get('SSH_TEST_PORT', 2222)) # Example port mapping
SSH_USER = os.environ.get('SSH_TEST_USER', 'testuser') # Assumed to have passwordless sudo in Dockerfile

# --- Choose ONE authentication method ---

# Option 1: Password Authentication
SSH_PASSWORD = os.environ.get('SSH_TEST_PASSWORD', 'testpass') # Use environment variable or default
SSH_KEYFILE = None
# Option 1.1: Sudo Password (if testing passworded sudo)
# Ensure the user in Docker needs a password for *some* sudo commands for this test
TEST_SUDO_PASSWORD = os.environ.get('SSH_TEST_SUDO_PASSWORD', 'testpass') # Or specific sudo pass if different

# Option 2: Keyfile Authentication (Recommended)
# Comment out SSH_PASSWORD above if using this option
# SSH_PASSWORD = None
# SSH_KEYFILE = os.environ.get('SSH_TEST_KEYFILE', os.path.expanduser('~/.ssh/id_rsa_docker_test')) # Example key path
# TEST_SUDO_PASSWORD = None # Typically no sudo password needed if key auth is primary

# --- Helper to create client ---
# Global client variable for potential reuse in some tests (use with caution)
_client_cache = None

def get_client(force_new=False, **kwargs):
    """
    Instantiates and returns a connected SshClient, allowing overrides.
    Caches the client by default unless force_new=True.
    """
    global _client_cache
    if not force_new and _client_cache:
        # Basic check if connection is alive, might need improvement
        try:
             if _client_cache._client.is_active():
                 print("Reusing cached client connection.")
                 return _client_cache
             else:
                 print("Cached client connection inactive, creating new.")
        except Exception:
             print("Error checking cached client, creating new.")
             _client_cache = None # Clear invalid cache

    default_kwargs = dict(
        host=SSH_HOST,
        port=SSH_PORT,
        user=SSH_USER,
        password=SSH_PASSWORD,
        keyfile=SSH_KEYFILE,
        sudo_password=None, # Default to no sudo password
        connect_timeout=15 # Slightly longer timeout for test environments
    )
    default_kwargs.update(kwargs) # Apply overrides
    print(f"Connecting to {default_kwargs['user']}@{default_kwargs['host']}:{default_kwargs['port']}...")
    client = SshClient(**default_kwargs)
    print("Connection successful.")
    if not force_new:
        _client_cache = client
    return client

# --- Fixture for client cleanup ---
@pytest.fixture(scope="function") # Use "module" scope if client can be reused across tests
def ssh_client(request):
    """Pytest fixture to provide and cleanup an SshClient instance."""
    client_instance = get_client(force_new=True) # Force new client for each test function
    yield client_instance
    # Teardown: close connection after test function finishes
    print("\nClosing client connection (fixture teardown)...")
    client_instance.close()


# --- Test Functions ---

# Placeholder for future directory-related tests
# Examples:
# - test_create_directory(ssh_client)
# - test_list_directory(ssh_client)
# - test_remove_directory(ssh_client)
# - test_directory_exists(ssh_client)
# - test_upload_directory(ssh_client)
# - test_download_directory(ssh_client)

@pytest.mark.skip(reason="No directory functions implemented yet")
def test_placeholder_directory(ssh_client):
    """Placeholder test for directory operations."""
    pass


# --- Manual Execution ---
if __name__ == "__main__":
    print("Starting SSH client directory tests (currently placeholders)...")
    print("Ensure your SSH test container is running and configured correctly.")
    print(f"Target: {SSH_USER}@{SSH_HOST}:{SSH_PORT}")
    if SSH_KEYFILE: print(f"Using Keyfile: {SSH_KEYFILE}")
    else: print("Using Password authentication.")
    if TEST_SUDO_PASSWORD: print("Sudo password configured for tests.")
    print("-" * 30)

    # It's better to use pytest runner: `pytest testing/test_directory.py`
    print("No tests to run manually in this file yet.")

    print("\n" + "=" * 30)
    print("Directory tests finished (placeholders).")
    print("=" * 30)
