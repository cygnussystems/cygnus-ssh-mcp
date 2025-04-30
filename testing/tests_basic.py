import sys
import os
import time
import tempfile
import pytest # Using pytest features like raises
import threading # Needed for busy test
import shlex # Import shlex module

# Add project root to path to import SshClient
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

# Ensure ssh_client module can be found
try:
    from ssh_client import SshClient, CommandFailed, CommandTimeout, BusyError, OutputPurged, TaskNotFound, SshError
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
def get_client(**kwargs):
    """Instantiates and returns a connected SshClient, allowing overrides."""
    default_kwargs = dict(
        host=SSH_HOST,
        port=SSH_PORT,
        user=SSH_USER,
        password=SSH_PASSWORD,
        keyfile=SSH_KEYFILE,
        connect_timeout=15 # Slightly longer timeout for test environments
    )
    default_kwargs.update(kwargs) # Apply overrides
    print(f"Connecting to {default_kwargs['user']}@{default_kwargs['host']}:{default_kwargs['port']}...")
    client = SshClient(**default_kwargs)
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
        with pytest.raises(CommandFailed) as excinfo:
            client.run(cmd)

        # Assertions on the caught exception
        print(f"Caught expected CommandFailed exception.")
        print(f"  Exit code: {excinfo.value.exit_code}")
        # Stderr might be bytes or str depending on exact failure point, handle both
        stderr_str = excinfo.value.stderr
        if isinstance(stderr_str, bytes):
             stderr_str = stderr_str.decode('utf-8', errors='ignore')
        print(f"  Stderr: {stderr_str.strip()}")

        assert excinfo.value.exit_code != 0, f"Expected non-zero exit code, got {excinfo.value.exit_code}"
        # Error message varies slightly between systems ('ls: cannot access...', 'ls: /nonexistent...')
        assert "No such file or directory" in stderr_str or "cannot access" in stderr_str, \
               f"Expected error message not found in stderr: {stderr_str}"
        print("Assertions passed.")

    except Exception as e:
        # Any other exception is unexpected during the test logic
        print(f"ERROR during test execution: {type(e).__name__} - {e}")
        raise
    finally:
        if client:
            print("Closing connection.")
            client.close()

def test_file_upload_download():
    """Tests uploading and downloading a file."""
    print("\n--- test_file_upload_download ---")
    client = None
    local_temp_file_obj = None # Use object to ensure closure
    local_temp_path = None
    local_download_path = None
    remote_path = f'/tmp/ssh_client_test_{int(time.time())}.txt' # Unique remote filename

    try:
        client = get_client()

        # 1. Create a local temporary file
        file_content = f"Test content {time.time()}\nLine 2 with Ümlauts\r\nWindows line ending.\n"
        # Use tempfile.NamedTemporaryFile for better cleanup context management
        local_temp_file_obj = tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8')
        local_temp_path = local_temp_file_obj.name
        local_temp_file_obj.write(file_content)
        local_temp_file_obj.close() # Close the file before upload/verification
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
        # Use cat -A to see line endings and special chars if needed: client.run(f"cat -A {remote_path}")
        cat_handle = client.run(f"cat {remote_path}")
        assert cat_handle.exit_code == 0, f"'cat {remote_path}' failed"
        # Get all lines, assuming it's short. Join handles potential \r\n split across buffer reads.
        remote_content_lines = cat_handle.tail(cat_handle.total_lines)
        remote_content = "".join(remote_content_lines)
        print(f"Remote content via cat:\n{remote_content!r}") # Use !r for clarity

        # Normalize line endings for cross-platform compatibility before comparing cat output
        # The file was written with mixed endings, cat preserves them.
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
        # Read the downloaded file in text mode, letting Python handle line endings based on OS
        with open(local_download_path, 'r', encoding='utf-8') as f:
            downloaded_content = f.read()
        print(f"Downloaded content:\n{downloaded_content!r}") # Use !r for clarity

        # Normalize BOTH original and downloaded content for robust comparison
        normalized_downloaded_content = downloaded_content.replace('\r\n', '\n')
        # Re-normalize original just in case (though done above)
        normalized_original_content_for_download_check = file_content.replace('\r\n', '\n')

        assert normalized_original_content_for_download_check == normalized_downloaded_content, \
            f"Downloaded content doesn't match original content after normalizing line endings.\n" \
            f"Original   (normalized): {normalized_original_content_for_download_check!r}\n" \
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
        # Use local_temp_path for existence check and unlink
        if local_temp_path and os.path.exists(local_temp_path):
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
        missing_keys = [key for key in expected_keys if key not in status_info]
        assert not missing_keys, f"Missing expected keys in status info: {missing_keys}"

        for key in expected_keys:
             assert status_info[key] is not None, f"Value for key '{key}' should not be None"
             # Allow 'n/a' as it depends on remote system state/tools
             # assert status_info[key] != 'n/a', f"Value for key '{key}' is 'n/a', command might have failed"

        # Specific check for user if possible
        if SSH_USER:
             # Handle potential domain\user format if applicable, though unlikely in test env
             remote_user = status_info['user'].split('\\')[-1]
             assert remote_user == SSH_USER, f"Expected user '{SSH_USER}', got '{status_info['user']}'"

        print("Assertions passed (basic structure and user check).")

    except Exception as e:
        print(f"ERROR: {e}")
        raise
    finally:
        if client:
            print("Closing connection.")
            client.close()

# --- Threading Helper for Busy Test ---
def run_command_in_thread(client, cmd, results):
    """Helper function to run a command in a separate thread and store result/exception."""
    thread_id = threading.get_ident()
    print(f"\n[Thread-{thread_id}] Starting command: {cmd}")
    try:
        handle = client.run(cmd)
        print(f"[Thread-{thread_id}] Command finished: {cmd}, Exit Code: {handle.exit_code}")
        results[thread_id] = {'handle': handle, 'exception': None}
    except Exception as e:
        print(f"[Thread-{thread_id}] Command failed: {cmd}, Error: {type(e).__name__} - {e}")
        results[thread_id] = {'handle': None, 'exception': e}

def test_busy_error_on_concurrent_run():
    """Tests that BusyError is raised if run() is called while another run() is active."""
    print("\n--- test_busy_error_on_concurrent_run ---")
    client = None
    thread = None
    thread_results = {}

    try:
        client = get_client()
        long_cmd = "sleep 2" # A command that takes some time

        # Start the first command in a separate thread
        print(f"Starting '{long_cmd}' in a background thread...")
        thread = threading.Thread(target=run_command_in_thread, args=(client, long_cmd, thread_results))
        thread.start()

        # Give the thread a moment to start and enter the run() method, setting _busy=True
        time.sleep(0.5) # Adjust if needed, ensures the thread likely holds the lock

        # Now, try to run another command from the main thread
        second_cmd = "echo 'Trying to run concurrently'"
        print(f"Attempting to run '{second_cmd}' while the first should be busy...")
        with pytest.raises(BusyError) as excinfo:
            client.run(second_cmd)

        print(f"Caught expected BusyError: {excinfo.value}")
        assert "Another command is currently running" in str(excinfo.value)

        print("BusyError assertion passed.")

    except Exception as e:
        print(f"ERROR during test setup or main thread execution: {e}")
        raise # Re-raise test setup errors
    finally:
        # Cleanup: Ensure the thread finishes
        if thread and thread.is_alive():
            print("Waiting for background thread to complete...")
            thread.join(timeout=5) # Wait for the sleep command to finish
            if thread.is_alive():
                print("Warning: Background thread did not finish in time.")
        if client:
            print("Closing connection.")
            client.close()

        # Check if the thread encountered an unexpected error
        for tid, result in thread_results.items():
            if result['exception'] and not isinstance(result['exception'], BusyError):
                 # Re-raise error from thread if it wasn't the expected BusyError
                 raise Exception(f"Error occurred in background thread {tid}") from result['exception']


def test_command_timeout():
    """Tests that CommandTimeout is raised if run() exceeds its I/O timeout."""
    print("\n--- test_command_timeout ---")
    client = None
    try:
        client = get_client()
        # Command that produces no output and waits longer than the timeout
        cmd = "sleep 5"
        timeout_seconds = 2
        print(f"Running command '{cmd}' with timeout {timeout_seconds}s (expecting CommandTimeout)")

        with pytest.raises(CommandTimeout) as excinfo:
            client.run(cmd, timeout=timeout_seconds)

        print(f"Caught expected CommandTimeout: {excinfo.value}")
        assert f"timed out after {timeout_seconds} seconds" in str(excinfo.value)
        assert excinfo.value.seconds == timeout_seconds

        # Verify client is not busy after timeout exception
        assert not client._busy, "Client should not be busy after CommandTimeout"
        # Try running another command to be sure
        print("Verifying client is responsive after timeout...")
        handle = client.run("echo 'Client responsive'")
        assert handle.exit_code == 0
        assert "Client responsive" in handle.tail()[0]
        print("Client is responsive.")

        print("Assertions passed.")

    except Exception as e:
        print(f"ERROR: {e}")
        raise
    finally:
        if client:
            print("Closing connection.")
            client.close()


def test_history_trimming():
    """Tests that command history is trimmed to the specified limit."""
    print("\n--- test_history_trimming ---")
    history_limit = 5 # Use a small limit for testing
    client = None
    try:
        # Create client with custom history limit
        client = get_client(history_limit=history_limit)

        num_commands = history_limit + 3
        print(f"Running {num_commands} commands with history limit {history_limit}...")

        first_handle_id = -1
        last_handle_id = -1
        for i in range(num_commands):
            cmd = f"echo 'Command {i}'"
            handle = client.run(cmd)
            if i == 0:
                first_handle_id = handle.id
            last_handle_id = handle.id
            # Small delay to ensure timestamps differ slightly if needed
            # time.sleep(0.01)

        print("Finished running commands.")
        history = client.history()
        print(f"History contains {len(history)} entries.")
        history_ids = [h['id'] for h in history]
        print(f"History IDs: {history_ids}")

        # Assertions
        assert len(history) == history_limit, f"History should contain exactly {history_limit} items, but found {len(history)}"
        assert first_handle_id not in history_ids, f"The first handle ID ({first_handle_id}) should have been trimmed"
        assert last_handle_id in history_ids, f"The last handle ID ({last_handle_id}) should be present in history"
        # Check if the IDs are the last 'history_limit' ones
        expected_ids = list(range(last_handle_id - history_limit + 1, last_handle_id + 1))
        assert history_ids == expected_ids, f"History IDs {history_ids} do not match expected IDs {expected_ids}"

        # Test accessing the trimmed handle via output()
        print(f"Attempting to access trimmed handle ID {first_handle_id} via output()...")
        with pytest.raises(TaskNotFound) as excinfo:
            client.output(first_handle_id)
        print(f"Caught expected TaskNotFound: {excinfo.value}")
        assert str(first_handle_id) in str(excinfo.value)

        print("Assertions passed.")

    except Exception as e:
        print(f"ERROR: {e}")
        raise
    finally:
        if client:
            print("Closing connection.")
            client.close()

# --- Tests for Launch, Status, Kill ---

def test_launch_and_status():
    """Tests launching a background command and checking its status."""
    print("\n--- test_launch_and_status ---")
    client = None
    pid = None
    try:
        client = get_client()
        # Command that runs for a few seconds
        sleep_duration = 3
        cmd = f"sleep {sleep_duration}"
        print(f"Launching command in background: {cmd}")
        handle = client.launch(cmd)

        assert handle is not None, "launch() should return a handle"
        assert handle.pid is not None, "Handle should contain a PID"
        assert handle.running is True, "Handle should initially be marked as running"
        assert handle.exit_code is None, "Handle exit code should be None initially"
        pid = handle.pid
        print(f"Command launched with PID: {pid}, Handle ID: {handle.id}")

        # Check status immediately - should be running
        print(f"Checking status for PID {pid} shortly after launch...")
        status = client.task_status(pid)
        print(f"Status: {status}")
        assert status == "running", f"Expected status 'running', got '{status}'"

        # Wait for slightly less than the sleep duration
        print(f"Waiting for {sleep_duration - 1} seconds...")
        time.sleep(sleep_duration - 1)

        # Check status again - should still be running
        print(f"Checking status for PID {pid} again...")
        status = client.task_status(pid)
        print(f"Status: {status}")
        assert status == "running", f"Expected status 'running' before completion, got '{status}'"

        # Wait for the command to definitely finish
        print(f"Waiting for {2} more seconds...")
        time.sleep(2)

        # Check status finally - should be exited
        print(f"Checking status for PID {pid} after expected completion...")
        status = client.task_status(pid)
        print(f"Status: {status}")
        assert status == "exited", f"Expected status 'exited' after completion, got '{status}'"

        # Check status for a non-existent PID
        non_existent_pid = 99999
        print(f"Checking status for non-existent PID {non_existent_pid}...")
        status = client.task_status(non_existent_pid)
        print(f"Status: {status}")
        assert status == "exited", f"Expected status 'exited' for non-existent PID, got '{status}'"


        print("Assertions passed.")

    except Exception as e:
        print(f"ERROR: {e}")
        # Attempt to clean up the sleep process if it's still running
        if client and pid:
            try:
                print(f"Attempting cleanup: killing PID {pid}")
                client.task_kill(pid, signal=9) # Send SIGKILL
            except Exception as kill_err:
                print(f"Cleanup kill failed: {kill_err}")
        raise
    finally:
        if client:
            print("Closing connection.")
            client.close()

def test_launch_with_redirection():
    """Tests launching with stdout/stderr redirection."""
    print("\n--- test_launch_with_redirection ---")
    client = None
    pid = None
    remote_out_log = f"/tmp/launch_test_out_{int(time.time())}.log"
    remote_err_log = f"/tmp/launch_test_err_{int(time.time())}.log"
    local_out_log = None
    local_err_log = None

    try:
        client = get_client()
        # Command that produces known stdout and stderr
        cmd = "echo 'Standard Output Message'; >&2 echo 'Standard Error Message'; sleep 1"
        print(f"Launching command with redirection: {cmd}")
        print(f"  stdout -> {remote_out_log}")
        print(f"  stderr -> {remote_err_log}")

        handle = client.launch(cmd, stdout_log=remote_out_log, stderr_log=remote_err_log)
        pid = handle.pid
        print(f"Launched with PID: {pid}")

        # Wait for command to finish
        print("Waiting for launched command to finish...")
        time.sleep(2) # Wait longer than the sleep in the command

        # Verify process exited
        status = client.task_status(pid)
        assert status == "exited", f"Launched process {pid} should have exited, status: {status}"

        # Download log files
        local_out_log = tempfile.mktemp()
        local_err_log = tempfile.mktemp()
        print(f"Downloading logs: {remote_out_log} -> {local_out_log}, {remote_err_log} -> {local_err_log}")
        client.get(remote_out_log, local_out_log)
        client.get(remote_err_log, local_err_log)

        # Verify log contents
        with open(local_out_log, 'r') as f:
            out_content = f.read().strip()
        with open(local_err_log, 'r') as f:
            err_content = f.read().strip()

        print(f"Stdout log content: '{out_content}'")
        print(f"Stderr log content: '{err_content}'")

        assert out_content == "Standard Output Message", "Stdout log content mismatch"
        assert err_content == "Standard Error Message", "Stderr log content mismatch"

        print("Assertions passed.")

    except Exception as e:
        print(f"ERROR: {e}")
        if client and pid:
            try: client.task_kill(pid, signal=9)
            except: pass
        raise
    finally:
        # Cleanup remote and local logs
        if client:
            try: client.run(f"rm -f {shlex.quote(remote_out_log)} {shlex.quote(remote_err_log)}")
            except: pass
            print("Closing connection.")
            client.close()
        if local_out_log and os.path.exists(local_out_log): os.unlink(local_out_log)
        if local_err_log and os.path.exists(local_err_log): os.unlink(local_err_log)


def test_task_kill():
    """Tests killing a launched background command."""
    print("\n--- test_task_kill ---")
    client = None
    pid = None
    try:
        client = get_client()
        # Launch a command that runs for a while
        cmd = "sleep 30"
        print(f"Launching long-running command: {cmd}")
        handle = client.launch(cmd)
        pid = handle.pid
        print(f"Launched with PID: {pid}")

        # Verify it's running
        time.sleep(1) # Give it time to start
        status = client.task_status(pid)
        assert status == "running", f"Process {pid} should be running initially, status: {status}"
        print(f"Process {pid} confirmed running.")

        # Send SIGTERM (default signal 15)
        print(f"Sending SIGTERM (15) to PID {pid}...")
        killed = client.task_kill(pid)
        assert killed is True, "task_kill should return True for successful signal delivery"
        print("SIGTERM sent.")

        # Wait a moment and check status - should be exited
        time.sleep(1)
        print(f"Checking status for PID {pid} after SIGTERM...")
        status = client.task_status(pid)
        print(f"Status: {status}")
        assert status == "exited", f"Expected status 'exited' after SIGTERM, got '{status}'"

        # --- Test killing a non-existent process ---
        non_existent_pid = 99998
        print(f"Attempting to kill non-existent PID {non_existent_pid}...")
        killed_non_existent = client.task_kill(non_existent_pid)
        # task_kill returns False if the kill command fails (e.g., "No such process")
        assert killed_non_existent is False, "task_kill should return False when killing non-existent PID"
        print("Attempt to kill non-existent PID handled correctly.")

        # --- Test killing with sudo (requires passwordless sudo for testuser) ---
        # Launch another process
        cmd_sudo = "sleep 30"
        print(f"Launching another process for sudo kill test: {cmd_sudo}")
        handle_sudo = client.launch(cmd_sudo)
        pid_sudo = handle_sudo.pid
        print(f"Launched with PID: {pid_sudo}")
        time.sleep(1)
        assert client.task_status(pid_sudo) == "running", f"Process {pid_sudo} for sudo test should be running"

        # Kill with sudo
        print(f"Sending SIGKILL (9) with sudo to PID {pid_sudo}...")
        killed_sudo = client.task_kill(pid_sudo, signal=9, sudo=True)
        assert killed_sudo is True, "task_kill with sudo should return True"
        print("sudo kill command sent.")
        time.sleep(1)
        status_sudo = client.task_status(pid_sudo)
        assert status_sudo == "exited", f"Expected status 'exited' after sudo kill, got '{status_sudo}'"
        print(f"Process {pid_sudo} confirmed exited after sudo kill.")


        print("Assertions passed.")

    except Exception as e:
        print(f"ERROR: {e}")
        # Cleanup attempts
        if client and pid and client.task_status(pid) == 'running':
            try: client.task_kill(pid, signal=9)
            except: pass
        if client and 'pid_sudo' in locals() and pid_sudo and client.task_status(pid_sudo) == 'running':
             try: client.task_kill(pid_sudo, signal=9, sudo=True)
             except: pass
        raise
    finally:
        if client:
            print("Closing connection.")
            client.close()


# --- Manual Execution ---
if __name__ == "__main__":
    print("Starting SSH client tests...")
    print("Ensure your SSH test container is running and configured correctly.")
    print(f"Target: {SSH_USER}@{SSH_HOST}:{SSH_PORT}")
    if SSH_KEYFILE:
        print(f"Using Keyfile: {SSH_KEYFILE}")
    else:
        print("Using Password authentication.")
    print("-" * 30)

    # Call test functions sequentially
    # Use pytest.main() for better test discovery and execution if pytest is installed
    # pytest.main([__file__]) # Uncomment to run with pytest runner

    # Manual sequential execution:
    test_connection_and_simple_run()
    test_run_failure()
    test_file_upload_download()
    test_status_command()
    test_busy_error_on_concurrent_run()
    test_command_timeout()
    test_history_trimming()
    test_launch_and_status()
    test_launch_with_redirection()
    test_task_kill()
    # Add calls to other test functions here as they are created
    # test_replace_line()
    # test_replace_block()
    # test_output_retrieval() # Needs update for launched commands

    print("\n" + "=" * 30)
    print("All specified tests finished.")
    print("=" * 30)

