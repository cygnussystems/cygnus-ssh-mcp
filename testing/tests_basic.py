import sys
import os
import time
import tempfile

# Add project root to path to import SshClient
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

# Ensure ssh_client module can be found
try:
    from ssh_client import SshClient, CommandFailed, CommandTimeout, BusyError, OutputPurged, TaskNotFound
except ImportError as e:
    print(f"Error importing SshClient: {e}")
    print(f"Project root added to path: {project_root}")
    print(f"Current sys.path: {sys.path}")
    sys.exit(1)

# --- Configuration (Adjust as per your Docker setup) ---
SSH_HOST = os.environ.get('SSH_TEST_HOST', 'localhost')
SSH_PORT = int(os.environ.get('SSH_TEST_PORT', 2222)) # Example port mapping
SSH_USER = os.environ.get('SSH_TEST_USER', 'testuser')

# --- Choose ONE authentication method ---

# Option 1: Password Authentication
SSH_PASSWORD = os.environ.get('SSH_TEST_PASSWORD', 'testpass') # Use environment variable or default
SSH_KEYFILE = None

# Option 2: Keyfile Authentication (Recommended)
# Comment out SSH_PASSWORD above if using this option
# SSH_PASSWORD = None
# SSH_KEYFILE = os.environ.get('SSH_TEST_KEYFILE', os.path.expanduser('~/.ssh/id_rsa_docker_test')) # Example key path

# --- Helper to create client ---
def get_client():
    """Instantiates and returns a connected SshClient."""
    print(f"Connecting to {SSH_USER}@{SSH_HOST}:{SSH_PORT}...")
    client = SshClient(
        host=SSH_HOST,
        port=SSH_PORT,
        user=SSH_USER,
        password=SSH_PASSWORD,
        keyfile=SSH_KEYFILE,
        connect_timeout=15 # Slightly longer timeout for test environments
    )
    print("Connection successful.")
    return client

# --- Test Functions ---

def test_connection_and_simple_run():
    """Tests basic connection and running a simple command."""
    print("\n--- test_connection_and_simple_run ---")
    client = None
    try:
        client = get_client()
        cmd = "echo 'Hello SSH World!'"
        print(f"Running command: {cmd}")
        handle = client.run(cmd)

        print(f"Command finished. Handle ID: {handle.id}, Exit code: {handle.exit_code}")
        output_lines = handle.tail()
        print("Output tail:")
        for line in output_lines:
            print(f"  {line.strip()}")

        assert handle.exit_code == 0, f"Expected exit code 0, got {handle.exit_code}"
        assert not handle.running, "Handle should not be running"
        assert handle.end_ts is not None, "End timestamp should be set"
        assert handle.total_lines > 0, "Should have captured at least one line"
        # Check if the expected output is present (accounting for potential newline)
        assert any('Hello SSH World!' in line for line in output_lines), "Expected output not found"
        print("Assertions passed.")

    except Exception as e:
        print(f"ERROR: {e}")
        raise # Re-raise the exception to make the test failure clear
    finally:
        if client:
            print("Closing connection.")
            client.close()

def test_run_failure():
    """Tests running a command that should fail."""
    print("\n--- test_run_failure ---")
    client = None
    try:
        client = get_client()
        cmd = "ls /nonexistent_directory_xyz_123"
        print(f"Running command expected to fail: {cmd}")
        try:
            client.run(cmd)
            # If run() doesn't raise, the test fails
            assert False, f"Command '{cmd}' should have failed but didn't."
        except CommandFailed as e:
            # This is the expected path
            print(f"Caught expected CommandFailed exception.")
            print(f"  Exit code: {e.exit_code}")
            # Stderr might be bytes, decode for assertion
            stderr_str = e.stderr.decode('utf-8', errors='ignore') if isinstance(e.stderr, bytes) else e.stderr
            print(f"  Stderr: {stderr_str.strip()}")
            assert e.exit_code != 0, f"Expected non-zero exit code, got {e.exit_code}"
            # Error message varies slightly between systems ('ls: cannot access...', 'ls: /nonexistent...')
            assert "No such file or directory" in stderr_str or "cannot access" in stderr_str, \
                   f"Expected error message not found in stderr: {stderr_str}"
            print("Assertions passed.")
        except Exception as e:
            # Any other exception is unexpected
            print(f"Caught unexpected exception type: {type(e).__name__} - {e}")
            raise # Re-raise unexpected exception

    except Exception as e:
        print(f"ERROR during setup or connection: {e}")
        raise
    finally:
        if client:
            print("Closing connection.")
            client.close()

def test_file_upload_download():
    """Tests uploading and downloading a file."""
    print("\n--- test_file_upload_download ---")
    client = None
    local_temp_file = None
    local_download_path = None
    remote_path = f'/tmp/ssh_client_test_{int(time.time())}.txt' # Unique remote filename

    try:
        client = get_client()

        # 1. Create a local temporary file
        file_content = f"Test content {time.time()}\nLine 2\n"
        local_temp_file = tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8')
        local_temp_file.write(file_content)
        local_temp_file.close()
        local_temp_path = local_temp_file.name
        print(f"Created local temp file: {local_temp_path} with content:\n{file_content!r}") # Use !r for clarity

        # 2. Upload the file
        print(f"Uploading {local_temp_path} to {remote_path}")
        client.put(local_temp_path, remote_path)
        print("Upload complete.")

        # 3. Verify upload using 'ls' and 'cat' (optional but good practice)
        print(f"Verifying remote file existence with 'ls {remote_path}'")
        ls_handle = client.run(f"ls -l {remote_path}")
        assert ls_handle.exit_code == 0, f"'ls {remote_path}' failed"
        print(f"Verifying remote file content with 'cat {remote_path}'")
        cat_handle = client.run(f"cat {remote_path}")
        assert cat_handle.exit_code == 0, f"'cat {remote_path}' failed"
        remote_content = "".join(cat_handle.tail(10)) # Get all lines assuming it's short
        print(f"Remote content via cat:\n{remote_content!r}") # Use !r for clarity

        # Normalize line endings for cross-platform compatibility before comparing cat output
        normalized_original_content = file_content.replace('\r\n', '\n')
        normalized_remote_content = remote_content.replace('\r\n', '\n')
        assert normalized_original_content == normalized_remote_content, \
            f"Remote content (from cat) doesn't match original content after normalizing line endings.\n" \
            f"Original (normalized): {normalized_original_content!r}\n" \
            f"Remote   (normalized): {normalized_remote_content!r}"
        print("Remote content via cat matches original (after normalization).")

        # 4. Create a local path for download
        local_download_path = local_temp_path + ".downloaded"
        print(f"Downloading {remote_path} to {local_download_path}")

        # 5. Download the file
        client.get(remote_path, local_download_path)
        print("Download complete.")

        # 6. Verify downloaded file content
        print(f"Reading downloaded file: {local_download_path}")
        # Read the downloaded file in text mode, letting Python handle line endings
        with open(local_download_path, 'r', encoding='utf-8') as f:
            downloaded_content = f.read()
        print(f"Downloaded content:\n{downloaded_content!r}") # Use !r for clarity

        # Normalize line endings for cross-platform compatibility before comparing downloaded file
        # The original content needs normalization. The downloaded content read with 'r'
        # should already be normalized by Python on Windows, but normalize both to be safe.
        normalized_downloaded_content = downloaded_content.replace('\r\n', '\n')
        assert normalized_original_content == normalized_downloaded_content, \
            f"Downloaded content doesn't match original content after normalizing line endings.\n" \
            f"Original   (normalized): {normalized_original_content!r}\n" \
            f"Downloaded (normalized): {normalized_downloaded_content!r}"
        print("Downloaded content matches original (after normalization).")

        print("Assertions passed.")

    except Exception as e:
        print(f"ERROR: {e}")
        raise
    finally:
        # Cleanup
        if client:
            try:
                print(f"Cleaning up remote file: {remote_path}")
                client.run(f"rm -f {remote_path}")
            except Exception as cleanup_err:
                print(f"Warning: Failed to cleanup remote file {remote_path}: {cleanup_err}")
            print("Closing connection.")
            client.close()
        if local_temp_file and os.path.exists(local_temp_path):
            print(f"Cleaning up local temp file: {local_temp_path}")
            os.unlink(local_temp_path)
        if local_download_path and os.path.exists(local_download_path):
            print(f"Cleaning up downloaded file: {local_download_path}")
            os.unlink(local_download_path)

def test_status_command():
    """Tests the status() method."""
    print("\n--- test_status_command ---")
    client = None
    try:
        client = get_client()
        print("Calling client.status()")
        status_info = client.status()
        print("Status info received:")
        # Print nicely formatted status
        for key, value in status_info.items():
            print(f"  {key:<12}: {value}")

        # Basic assertions - check if keys exist and have some value
        expected_keys = ['user', 'cwd', 'time', 'os', 'host', 'uptime', 'load_avg', 'free_disk', 'mem_free']
        for key in expected_keys:
            assert key in status_info, f"Expected key '{key}' not found in status info"
            assert status_info[key] is not None, f"Value for key '{key}' should not be None"
            # Don't assert for 'n/a' as it depends on the remote system state/permissions
            # assert status_info[key] != 'n/a', f"Value for key '{key}' is 'n/a', command might have failed"

        # Specific check for user if possible
        if SSH_USER:
             assert status_info['user'] == SSH_USER, f"Expected user '{SSH_USER}', got '{status_info['user']}'"

        print("Assertions passed (basic structure and user check).")

    except Exception as e:
        print(f"ERROR: {e}")
        raise
    finally:
        if client:
            print("Closing connection.")
            client.close()

# --- Manual Execution ---
if __name__ == "__main__":
    print("Starting basic SSH client tests...")
    print("Ensure your SSH test container is running and configured correctly.")
    print(f"Target: {SSH_USER}@{SSH_HOST}:{SSH_PORT}")
    if SSH_KEYFILE:
        print(f"Using Keyfile: {SSH_KEYFILE}")
    else:
        print("Using Password authentication.")
    print("-" * 30)

    # Call test functions sequentially
    test_connection_and_simple_run()
    test_run_failure()
    test_file_upload_download()
    test_status_command()
    # Add calls to other test functions here as they are created
    # test_replace_line()
    # test_replace_block()
    # test_command_timeout()
    # test_busy_error()
    # test_output_retrieval()

    print("\n" + "=" * 30)
    print("All basic tests finished.")
    print("=" * 30)
