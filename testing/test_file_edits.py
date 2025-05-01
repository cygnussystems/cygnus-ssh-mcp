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

# Helper function to create a test file on the remote host
def create_remote_test_file(client, path, content, sudo=False):
    print(f"Creating remote test file: {path} (sudo={sudo})")
    # Use echo with redirection, handle potential quoting issues
    # Using printf might be safer for arbitrary content
    printf_cmd = f"printf '%s' {shlex.quote(content)} > {shlex.quote(path)}"
    try:
        client.run(printf_cmd, sudo=sudo)
    except Exception as e:
        pytest.fail(f"Failed to create remote test file {path}: {e}")

# Helper function to read a remote file's content
def read_remote_file(client, path, sudo=False):
    print(f"Reading remote file: {path} (sudo={sudo})")
    try:
        handle = client.run(f"cat {shlex.quote(path)}", sudo=sudo)
        if handle.exit_code != 0:
            pytest.fail(f"Failed to read remote file {path}, exit code {handle.exit_code}")
        return "".join(handle.tail(handle.total_lines))
    except Exception as e:
        pytest.fail(f"Failed to read remote file {path}: {e}")

# Helper function for cleanup
def cleanup_remote_file(client, path, sudo=False):
    print(f"Cleaning up remote file: {path} (sudo={sudo})")
    try:
        client.run(f"rm -f {shlex.quote(path)}", sudo=sudo, io_timeout=5, runtime_timeout=10)
    except Exception as e:
        print(f"Warning: Failed to cleanup remote file {path}: {e}")


# --- replace_line Tests ---

def test_replace_line_simple(ssh_client):
    """Tests replacing a single line without sudo."""
    client = ssh_client
    remote_path = f"/tmp/replace_line_test_{int(time.time())}.txt"
    original_content = "Line 1\nLine to replace\nLine 3\nAnother line to replace\n"
    old_line = "Line to replace"
    new_line = "Line has been replaced"
    expected_content = "Line 1\nLine has been replaced\nLine 3\nAnother line to replace\n" # count=1 default

    try:
        create_remote_test_file(client, remote_path, original_content)
        client.replace_line(remote_path, old_line, new_line) # Default count=1
        actual_content = read_remote_file(client, remote_path)
        assert actual_content == expected_content
        print("Simple replace_line (count=1) successful.")
    finally:
        cleanup_remote_file(client, remote_path)

def test_replace_line_multiple(ssh_client):
    """Tests replacing multiple occurrences of a line."""
    client = ssh_client
    remote_path = f"/tmp/replace_line_multi_{int(time.time())}.txt"
    original_content = "Line 1\nReplace Me\nLine 3\nReplace Me\nLine 5\nReplace Me\n"
    old_line = "Replace Me"
    new_line = "Replaced!"
    expected_content = "Line 1\nReplaced!\nLine 3\nReplaced!\nLine 5\nReplace Me\n" # count=2

    try:
        create_remote_test_file(client, remote_path, original_content)
        client.replace_line(remote_path, old_line, new_line, count=2)
        actual_content = read_remote_file(client, remote_path)
        assert actual_content == expected_content
        print("replace_line (count=2) successful.")
    finally:
        cleanup_remote_file(client, remote_path)

def test_replace_line_no_match(ssh_client):
    """Tests replace_line when the old_line doesn't exist."""
    client = ssh_client
    remote_path = f"/tmp/replace_line_nomatch_{int(time.time())}.txt"
    original_content = "Line 1\nLine 2\nLine 3\n"
    old_line = "Nonexistent Line"
    new_line = "Should not appear"

    try:
        create_remote_test_file(client, remote_path, original_content)
        client.replace_line(remote_path, old_line, new_line)
        actual_content = read_remote_file(client, remote_path)
        assert actual_content == original_content # Content should be unchanged
        print("replace_line with no match successful (no change).")
    finally:
        cleanup_remote_file(client, remote_path)

def test_replace_line_sudo(ssh_client):
    """Tests replacing a line in a file requiring sudo."""
    client = ssh_client
    # Use a file owned by root in /tmp for simplicity, assuming testuser can write to /tmp for temp file
    remote_path = f"/tmp/replace_line_sudo_{int(time.time())}.txt"
    original_content = "Root Line 1\nRoot Line to Replace\nRoot Line 3\n"
    old_line = "Root Line to Replace"
    new_line = "Sudo Replaced This"
    expected_content = "Root Line 1\nSudo Replaced This\nRoot Line 3\n"

    try:
        # Create the file as root
        create_remote_test_file(client, remote_path, original_content, sudo=True)
        # Ensure ownership is root (optional check)
        ls_handle = client.run(f"ls -l {shlex.quote(remote_path)}")
        assert "root root" in "".join(ls_handle.tail()), f"File {remote_path} not owned by root"

        # Perform replacement with sudo
        client.replace_line(remote_path, old_line, new_line, sudo=True)

        # Read back the file (can use sudo or check permissions allow testuser read)
        actual_content = read_remote_file(client, remote_path, sudo=True) # Read with sudo to be safe
        assert actual_content == expected_content

        # Verify permissions were likely preserved (basic check: still owned by root)
        ls_handle_after = client.run(f"ls -l {shlex.quote(remote_path)}")
        assert "root root" in "".join(ls_handle_after.tail()), f"File {remote_path} ownership changed after sudo replace"

        print("replace_line with sudo successful.")
    finally:
        cleanup_remote_file(client, remote_path, sudo=True)


# --- replace_block Tests ---

def test_replace_block_simple(ssh_client):
    """Tests replacing a block of text without sudo."""
    client = ssh_client
    remote_path = f"/tmp/replace_block_test_{int(time.time())}.txt"
    old_block = "--- Start Block ---\nLine A\nLine B\n--- End Block ---"
    new_block = "--- Replacement ---\nNew Content\n--- End Replacement ---"
    original_content = f"Preamble\n{old_block}\nPostamble\n{old_block}\nEnd."
    expected_content = f"Preamble\n{new_block}\nPostamble\n{new_block}\nEnd."

    try:
        create_remote_test_file(client, remote_path, original_content)
        client.replace_block(remote_path, old_block, new_block)
        actual_content = read_remote_file(client, remote_path)
        assert actual_content == expected_content
        print("Simple replace_block successful.")
    finally:
        cleanup_remote_file(client, remote_path)

def test_replace_block_no_match(ssh_client):
    """Tests replace_block when the old_block doesn't exist."""
    client = ssh_client
    remote_path = f"/tmp/replace_block_nomatch_{int(time.time())}.txt"
    original_content = "Some existing content.\nAnother line.\n"
    old_block = "--- Nonexistent Block ---"
    new_block = "--- Should Not Appear ---"

    try:
        create_remote_test_file(client, remote_path, original_content)
        client.replace_block(remote_path, old_block, new_block)
        actual_content = read_remote_file(client, remote_path)
        assert actual_content == original_content # Content should be unchanged
        print("replace_block with no match successful (no change).")
    finally:
        cleanup_remote_file(client, remote_path)

def test_replace_block_sudo(ssh_client):
    """Tests replacing a block in a file requiring sudo."""
    client = ssh_client
    remote_path = f"/tmp/replace_block_sudo_{int(time.time())}.txt"
    old_block = "<config>\n  <value>old</value>\n</config>"
    new_block = "<config>\n  <value>new</value>\n  <added/>\n</config>"
    original_content = f"# System Config\n{old_block}\n# End Config"
    expected_content = f"# System Config\n{new_block}\n# End Config"

    try:
        # Create the file as root
        create_remote_test_file(client, remote_path, original_content, sudo=True)
        ls_handle = client.run(f"ls -l {shlex.quote(remote_path)}")
        assert "root root" in "".join(ls_handle.tail()), f"File {remote_path} not owned by root"

        # Perform replacement with sudo
        client.replace_block(remote_path, old_block, new_block, sudo=True)

        # Read back the file with sudo
        actual_content = read_remote_file(client, remote_path, sudo=True)
        assert actual_content == expected_content

        # Verify ownership preserved
        ls_handle_after = client.run(f"ls -l {shlex.quote(remote_path)}")
        assert "root root" in "".join(ls_handle_after.tail()), f"File {remote_path} ownership changed after sudo replace"

        print("replace_block with sudo successful.")
    finally:
        cleanup_remote_file(client, remote_path, sudo=True)


# --- Manual Execution ---
if __name__ == "__main__":
    print("Starting SSH client file editing tests...")
    print("Ensure your SSH test container is running and configured correctly.")
    print(f"Target: {SSH_USER}@{SSH_HOST}:{SSH_PORT}")
    if SSH_KEYFILE: print(f"Using Keyfile: {SSH_KEYFILE}")
    else: print("Using Password authentication.")
    if TEST_SUDO_PASSWORD: print("Sudo password configured for tests.")
    print("-" * 30)

    # It's better to use pytest runner: `pytest testing/test_file_edits.py`
    # Manual sequential execution (less ideal):
    client = None
    try:
        client = get_client(force_new=True) # Get a client for manual runs

        # replace_line tests
        test_replace_line_simple(client)
        test_replace_line_multiple(client)
        test_replace_line_no_match(client)
        test_replace_line_sudo(client)

        # replace_block tests
        test_replace_block_simple(client)
        test_replace_block_no_match(client)
        test_replace_block_sudo(client)

    finally:
        if client:
             client.close() # Close the manually created client

    print("\n" + "=" * 30)
    print("File editing tests finished.")
    print("=" * 30)
