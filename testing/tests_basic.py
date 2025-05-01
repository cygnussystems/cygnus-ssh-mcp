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
        SshClient, CommandFailed, SshError
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

def test_connection(ssh_client):
    """Tests basic connection establishment via the fixture."""
    print("\n--- test_connection ---")
    client = ssh_client
    assert client._client is not None, "Client object should exist"
    assert client._client.is_active(), "Client connection should be active"
    print("Connection active assertion passed.")
    # Optionally run a very simple command to be doubly sure
    handle = client.run("pwd")
    assert handle.exit_code == 0
    print("Basic 'pwd' command successful.")


def test_file_upload_download(ssh_client):
    """Tests uploading and downloading a file."""
    print("\n--- test_file_upload_download ---")
    client = ssh_client
    local_temp_file_obj = None # Use object to ensure closure
    local_temp_path = None
    local_download_path = None
    remote_path = f'/tmp/ssh_client_test_{int(time.time())}.txt' # Unique remote filename

    try:
        # 1. Create a local temporary file
        file_content = f"Test content {time.time()}\nLine 2 with Ümlauts\r\nWindows line ending.\n"
        local_temp_file_obj = tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8')
        local_temp_path = local_temp_file_obj.name
        local_temp_file_obj.write(file_content)
        local_temp_file_obj.close() # Close the file before upload/verification
        print(f"Created local temp file: {local_temp_path} with content:\n{file_content!r}")

        # 2. Upload the file
        print(f"Uploading {local_temp_path} to {remote_path}")
        client.put(local_temp_path, remote_path)
        print("Upload complete.")

        # 3. Verify upload using 'ls' and 'cat'
        print(f"Verifying remote file existence with 'ls {remote_path}'")
        ls_handle = client.run(f"ls -l {remote_path}")
        assert ls_handle.exit_code == 0, f"'ls {remote_path}' failed"
        print(f"Verifying remote file content with 'cat {remote_path}'")
        cat_handle = client.run(f"cat {remote_path}")
        assert cat_handle.exit_code == 0, f"'cat {remote_path}' failed"
        remote_content_lines = cat_handle.tail(cat_handle.total_lines)
        remote_content = "".join(remote_content_lines)
        print(f"Remote content via cat:\n{remote_content!r}")

        # Normalize line endings for comparison
        normalized_original_content = file_content.replace('\r\n', '\n')
        normalized_remote_content = remote_content.replace('\r\n', '\n')
        assert normalized_original_content == normalized_remote_content, \
            f"Remote content (cat) mismatch after normalization.\nOriginal: {normalized_original_content!r}\nRemote: {normalized_remote_content!r}"
        print("Remote content via cat matches original (after normalization).")

        # 4. Download the file
        local_download_path = local_temp_path + ".downloaded"
        print(f"Downloading {remote_path} to {local_download_path}")
        client.get(remote_path, local_download_path)
        print("Download complete.")

        # 5. Verify downloaded file content
        print(f"Reading downloaded file: {local_download_path}")
        with open(local_download_path, 'r', encoding='utf-8') as f:
            downloaded_content = f.read()
        print(f"Downloaded content:\n{downloaded_content!r}")

        # Normalize for comparison
        normalized_downloaded_content = downloaded_content.replace('\r\n', '\n')
        assert normalized_original_content == normalized_downloaded_content, \
            f"Downloaded content mismatch after normalization.\nOriginal: {normalized_original_content!r}\nDownloaded: {normalized_downloaded_content!r}"
        print("Downloaded content matches original (after normalization).")

        print("Assertions passed.")

    finally:
        # Cleanup
        if client: # Check if client was successfully created
            try:
                print(f"Cleaning up remote file: {remote_path}")
                # Use short timeout for cleanup command
                client.run(f"rm -f {shlex.quote(remote_path)}", io_timeout=5, runtime_timeout=10)
            except Exception as cleanup_err:
                print(f"Warning: Failed to cleanup remote file {remote_path}: {cleanup_err}")
        # Use local_temp_path for existence check and unlink
        if local_temp_path and os.path.exists(local_temp_path):
            print(f"Cleaning up local temp file: {local_temp_path}")
            os.unlink(local_temp_path)
        if local_download_path and os.path.exists(local_download_path):
            print(f"Cleaning up downloaded file: {local_download_path}")
            os.unlink(local_download_path)


def test_status_command(ssh_client):
    """Tests the status() method."""
    print("\n--- test_status_command ---")
    client = ssh_client
    print("Calling client.status()")
    status_info = client.status()
    print("Status info received:")
    for key, value in status_info.items():
        print(f"  {key:<12}: {value}")

    assert 'error' not in status_info, f"Status command returned an error: {status_info.get('error')}"
    expected_keys = ['user', 'cwd', 'time', 'os', 'host', 'uptime', 'load_avg', 'free_disk', 'mem_free']
    missing_keys = [key for key in expected_keys if key not in status_info]
    assert not missing_keys, f"Missing expected keys in status info: {missing_keys}"

    for key in expected_keys:
         assert status_info[key] is not None, f"Value for key '{key}' should not be None"

    if SSH_USER:
         # Handle potential domain\user format if needed, though unlikely in test container
         remote_user = status_info['user'].split('\\')[-1]
         assert remote_user == SSH_USER, f"Expected user '{SSH_USER}', got '{status_info['user']}'"

    print("Assertions passed.")


# --- Manual Execution ---
if __name__ == "__main__":
    print("Starting SSH client basic tests (connection, file transfer, status)...")
    print("Ensure your SSH test container is running and configured correctly.")
    print(f"Target: {SSH_USER}@{SSH_HOST}:{SSH_PORT}")
    if SSH_KEYFILE: print(f"Using Keyfile: {SSH_KEYFILE}")
    else: print("Using Password authentication.")
    if TEST_SUDO_PASSWORD: print("Sudo password configured for tests.")
    print("-" * 30)

    # It's better to use pytest runner: `pytest testing/tests_basic.py`
    # Manual sequential execution (less ideal):
    client = None # Define client outside try
    try:
        # Use fixture manually if not using pytest runner
        client = get_client(force_new=True) # Get a client for manual runs

        test_connection(client)
        test_file_upload_download(client)
        test_status_command(client)

    finally:
        if client:
             client.close() # Close the manually created client

    print("\n" + "=" * 30)
    print("Basic tests finished.")
    print("=" * 30)
