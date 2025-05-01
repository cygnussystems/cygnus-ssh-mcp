import sys
import os
import time
import tempfile
import pytest # Using pytest features like raises
import threading # Needed for busy test
import shlex # Import shlex module
import logging # Added for testing log output (optional)

# Add project root to path to import SshClient
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

# Ensure ssh_client module can be found
try:
    # Import new exceptions
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

def test_connection_and_simple_run(ssh_client):
    """Tests basic connection and running a simple command."""
    print("\n--- test_connection_and_simple_run ---")
    client = ssh_client
    cmd = "echo 'Hello SSH World!'"
    print(f"Running command: {cmd}")
    handle = client.run(cmd)

    print(f"Command finished. Handle ID: {handle.id}, PID: {handle.pid}, Exit code: {handle.exit_code}")
    output_lines = handle.tail()
    print("Output tail:")
    for line in output_lines:
        print(f"  {line.strip()}")

    assert handle.exit_code == 0, f"Expected exit code 0, got {handle.exit_code}"
    assert not handle.running, "Handle should not be running"
    assert handle.end_ts is not None, "End timestamp should be set"
    assert handle.total_lines > 0, "Should have captured at least one line"
    assert handle.pid is not None, "Handle should have captured a PID"
    assert any('Hello SSH World!' in line for line in output_lines), "Expected output not found"
    print("Assertions passed.")


def test_run_failure(ssh_client):
    """Tests running a command that should fail."""
    print("\n--- test_run_failure ---")
    client = ssh_client
    cmd = "ls /nonexistent_directory_xyz_123 && exit 42" # Ensure specific exit code
    print(f"Running command expected to fail: {cmd}")
    with pytest.raises(CommandFailed) as excinfo:
        client.run(cmd)

    # Assertions on the caught exception
    print(f"Caught expected CommandFailed exception.")
    print(f"  Exit code: {excinfo.value.exit_code}")
    stderr_str = excinfo.value.stderr
    if isinstance(stderr_str, bytes):
         stderr_str = stderr_str.decode('utf-8', errors='ignore')
    print(f"  Stderr: {stderr_str.strip()}")

    assert excinfo.value.exit_code == 42, f"Expected exit code 42, got {excinfo.value.exit_code}"
    assert "No such file or directory" in stderr_str or "cannot access" in stderr_str, \
           f"Expected error message not found in stderr: {stderr_str}"
    print("Assertions passed.")


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
         remote_user = status_info['user'].split('\\')[-1]
         assert remote_user == SSH_USER, f"Expected user '{SSH_USER}', got '{status_info['user']}'"

    print("Assertions passed.")


# --- Threading Helper for Busy Test ---
def run_command_in_thread(client, cmd, results, **kwargs):
    """Helper function to run a command in a separate thread and store result/exception."""
    thread_id = threading.get_ident()
    print(f"\n[Thread-{thread_id}] Starting command: {cmd} with args {kwargs}")
    try:
        handle = client.run(cmd, **kwargs)
        print(f"[Thread-{thread_id}] Command finished: {cmd}, Exit Code: {handle.exit_code}")
        results[thread_id] = {'handle': handle, 'exception': None}
    except Exception as e:
        print(f"[Thread-{thread_id}] Command failed: {cmd}, Error: {type(e).__name__} - {e}")
        results[thread_id] = {'handle': None, 'exception': e}

def test_busy_error_on_concurrent_run(ssh_client):
    """Tests that BusyError is raised if run() is called while another run() is active."""
    print("\n--- test_busy_error_on_concurrent_run ---")
    client = ssh_client
    thread = None
    thread_results = {}

    try:
        long_cmd = "sleep 2"
        print(f"Starting '{long_cmd}' in a background thread...")
        thread = threading.Thread(target=run_command_in_thread, args=(client, long_cmd, thread_results))
        thread.start()
        time.sleep(0.5) # Give thread time to acquire lock

        second_cmd = "echo 'Trying to run concurrently'"
        print(f"Attempting to run '{second_cmd}' while the first should be busy...")
        with pytest.raises(BusyError) as excinfo:
            client.run(second_cmd)

        print(f"Caught expected BusyError: {excinfo.value}")
        assert "currently executing" in str(excinfo.value)
        print("BusyError assertion passed.")

    finally:
        if thread and thread.is_alive():
            print("Waiting for background thread to complete...")
            thread.join(timeout=5)
            if thread.is_alive(): print("Warning: Background thread did not finish.")
        # Check thread results for unexpected errors
        for tid, result in thread_results.items():
            if result['exception'] and not isinstance(result['exception'], BusyError):
                 raise Exception(f"Error occurred in background thread {tid}") from result['exception']


def test_command_io_timeout(ssh_client):
    """Tests that CommandTimeout (I/O inactivity) is raised."""
    print("\n--- test_command_io_timeout ---")
    client = ssh_client
    # Command that produces no output and waits
    cmd = "sleep 5"
    io_timeout_seconds = 2
    print(f"Running command '{cmd}' with io_timeout {io_timeout_seconds}s (expecting CommandTimeout)")

    with pytest.raises(CommandTimeout) as excinfo:
        # Use a runtime_timeout longer than io_timeout to ensure I/O timeout triggers first
        client.run(cmd, io_timeout=io_timeout_seconds, runtime_timeout=10)

    print(f"Caught expected CommandTimeout: {excinfo.value}")
    assert f"inactivity" in str(excinfo.value)
    assert excinfo.value.seconds == io_timeout_seconds

    # Verify client lock is released after timeout exception
    # Try acquiring the lock non-blockingly
    lock_acquired = client._busy_lock.acquire(blocking=False)
    assert lock_acquired, "Client busy lock should be released after CommandTimeout"
    if lock_acquired: client._busy_lock.release()

    print("Verifying client is responsive after timeout...")
    handle = client.run("echo 'Client responsive'")
    assert handle.exit_code == 0
    assert "Client responsive" in handle.tail()[0]
    print("Client is responsive.")
    print("Assertions passed.")


def test_command_runtime_timeout(ssh_client):
    """Tests that CommandRuntimeTimeout is raised and process is killed."""
    print("\n--- test_command_runtime_timeout ---")
    client = ssh_client
    # Command that runs longer than the runtime timeout
    sleep_duration = 5
    runtime_timeout_seconds = 2
    cmd = f"sleep {sleep_duration}"
    print(f"Running command '{cmd}' with runtime_timeout {runtime_timeout_seconds}s (expecting CommandRuntimeTimeout)")

    target_pid = None
    try:
        with pytest.raises(CommandRuntimeTimeout) as excinfo:
            client.run(cmd, io_timeout=1, runtime_timeout=runtime_timeout_seconds) # Short I/O timeout too

        print(f"Caught expected CommandRuntimeTimeout: {excinfo.value}")
        assert f"exceeded runtime timeout of {runtime_timeout_seconds}s" in str(excinfo.value)
        assert excinfo.value.seconds == runtime_timeout_seconds
        assert excinfo.value.handle is not None, "Exception should contain the handle"
        target_pid = excinfo.value.handle.pid
        assert target_pid is not None, "Handle in exception should have the PID"
        print(f"Command timed out as expected. PID was {target_pid}.")

        # Verify client lock is released
        lock_acquired = client._busy_lock.acquire(blocking=False)
        assert lock_acquired, "Client busy lock should be released after CommandRuntimeTimeout"
        if lock_acquired: client._busy_lock.release()

        # Verify the remote process was actually killed
        print(f"Verifying process PID {target_pid} is no longer running...")
        # Need a *new* client instance to check status as the original might be affected?
        # Or assume the original client recovered enough for task_status? Let's try original first.
        # Wait briefly for kill signal to be processed
        time.sleep(1.0)
        status = client.task_status(target_pid)
        print(f"Status of PID {target_pid} after timeout/kill attempt: {status}")
        assert status == "exited", f"Process {target_pid} should have been killed, but status is {status}"
        print(f"Process {target_pid} confirmed exited.")

        print("Assertions passed.")

    except Exception as e:
         # If the test failed, try to ensure the sleep process is killed
         if target_pid:
             print(f"Test failed, attempting cleanup kill for PID {target_pid}")
             try:
                 # Use a new client for cleanup to avoid lock issues
                 cleanup_client = get_client(force_new=True, connect_timeout=5)
                 cleanup_client.task_kill(target_pid, force_kill_signal=9)
                 cleanup_client.close()
             except Exception as cleanup_err:
                 print(f"Cleanup kill failed: {cleanup_err}")
         raise # Re-raise original test failure


def test_history_trimming(ssh_client):
    """Tests that command history is trimmed to the specified limit."""
    print("\n--- test_history_trimming ---")
    history_limit = 5 # Use a small limit for testing
    # Need a new client with specific history limit
    client = get_client(force_new=True, history_limit=history_limit)
    try:
        num_commands = history_limit + 3
        print(f"Running {num_commands} commands with history limit {history_limit}...")

        first_handle_id = -1
        last_handle_id = -1
        for i in range(num_commands):
            cmd = f"echo 'Command {i}'"
            handle = client.run(cmd)
            if i == 0: first_handle_id = handle.id
            last_handle_id = handle.id

        print("Finished running commands.")
        history = client.history()
        print(f"History contains {len(history)} entries.")
        history_ids = [h['id'] for h in history]
        print(f"History IDs: {history_ids}")

        assert len(history) == history_limit, f"History should contain {history_limit} items, found {len(history)}"
        assert first_handle_id not in history_ids, f"First handle ID {first_handle_id} should be trimmed"
        assert last_handle_id in history_ids, f"Last handle ID {last_handle_id} should be present"
        expected_ids = list(range(last_handle_id - history_limit + 1, last_handle_id + 1))
        assert history_ids == expected_ids, f"History IDs {history_ids} != expected {expected_ids}"

        print(f"Attempting to access trimmed handle ID {first_handle_id} via output()...")
        with pytest.raises(TaskNotFound) as excinfo:
            client.output(first_handle_id)
        print(f"Caught expected TaskNotFound: {excinfo.value}")
        assert str(first_handle_id) in str(excinfo.value)

        print("Assertions passed.")
    finally:
        client.close() # Close the specific client used for this test


# --- Tests for Launch, Status, Kill ---

def test_launch_and_status(ssh_client):
    """Tests launching a background command and checking its status."""
    print("\n--- test_launch_and_status ---")
    client = ssh_client
    pid = None
    try:
        sleep_duration = 3
        cmd = f"sleep {sleep_duration}"
        print(f"Launching command in background: {cmd}")
        # Launch with default logging enabled
        handle = client.launch(cmd, log_output=True)

        assert handle and handle.pid, "launch() should return a handle with a PID"
        pid = handle.pid
        print(f"Command launched with PID: {pid}, Handle ID: {handle.id}")

        print(f"Checking status for PID {pid} shortly after launch...")
        status = client.task_status(pid)
        print(f"Status: {status}")
        assert status == "running", f"Expected 'running', got '{status}'"

        print(f"Waiting for {sleep_duration - 1} seconds...")
        time.sleep(sleep_duration - 1)
        print(f"Checking status for PID {pid} again...")
        status = client.task_status(pid)
        print(f"Status: {status}")
        assert status == "running", f"Expected 'running' before completion, got '{status}'"

        print(f"Waiting for {2} more seconds...")
        time.sleep(2)
        print(f"Checking status for PID {pid} after expected completion...")
        status = client.task_status(pid)
        print(f"Status: {status}")
        assert status == "exited", f"Expected 'exited' after completion, got '{status}'"

        # Check default log file existence (name depends on PID)
        default_log_path = f"/tmp/task-{pid}.log"
        print(f"Checking for default log file: {default_log_path}")
        ls_handle = client.run(f"ls {shlex.quote(default_log_path)}")
        assert ls_handle.exit_code == 0, f"Default log file {default_log_path} not found"
        # Cleanup log file
        client.run(f"rm -f {shlex.quote(default_log_path)}")

        print("Assertions passed.")

    finally:
        # Ensure process is killed if test failed mid-run
        if client and pid and client.task_status(pid) == 'running':
            print(f"Attempting cleanup: killing PID {pid}")
            client.task_kill(pid, force_kill_signal=9)


def test_launch_with_redirection(ssh_client):
    """Tests launching with explicit stdout/stderr redirection."""
    print("\n--- test_launch_with_redirection ---")
    client = ssh_client
    pid = None
    remote_out_log = f"/tmp/launch_test_out_{int(time.time())}.log"
    remote_err_log = f"/tmp/launch_test_err_{int(time.time())}.log"
    local_out_log = None
    local_err_log = None

    try:
        cmd = "echo 'Standard Output Message'; >&2 echo 'Standard Error Message'; sleep 1"
        print(f"Launching command with redirection: {cmd}")
        print(f"  stdout -> {remote_out_log}")
        print(f"  stderr -> {remote_err_log}")

        handle = client.launch(cmd, stdout_log=remote_out_log, stderr_log=remote_err_log)
        pid = handle.pid
        print(f"Launched with PID: {pid}")

        print("Waiting for launched command to finish...")
        time.sleep(2)
        status = client.task_status(pid)
        assert status == "exited", f"Launched process {pid} should have exited, status: {status}"

        local_out_log = tempfile.mktemp()
        local_err_log = tempfile.mktemp()
        print(f"Downloading logs...")
        client.get(remote_out_log, local_out_log)
        client.get(remote_err_log, local_err_log)

        with open(local_out_log, 'r') as f: out_content = f.read().strip()
        with open(local_err_log, 'r') as f: err_content = f.read().strip()
        print(f"Stdout log content: '{out_content}'")
        print(f"Stderr log content: '{err_content}'")
        assert out_content == "Standard Output Message", "Stdout log content mismatch"
        assert err_content == "Standard Error Message", "Stderr log content mismatch"

        print("Assertions passed.")

    finally:
        if client:
            if pid and client.task_status(pid) == 'running': client.task_kill(pid, force_kill_signal=9)
            try: client.run(f"rm -f {shlex.quote(remote_out_log)} {shlex.quote(remote_err_log)}")
            except: pass
        if local_out_log and os.path.exists(local_out_log): os.unlink(local_out_log)
        if local_err_log and os.path.exists(local_err_log): os.unlink(local_err_log)


def test_task_kill(ssh_client):
    """Tests killing a launched background command using task_kill."""
    print("\n--- test_task_kill ---")
    client = ssh_client
    pid = None
    try:
        cmd = "sleep 30"
        print(f"Launching long-running command: {cmd}")
        handle = client.launch(cmd)
        pid = handle.pid
        print(f"Launched with PID: {pid}")
        time.sleep(1)
        assert client.task_status(pid) == "running", f"Process {pid} should be running"

        print(f"Sending SIGTERM (15) to PID {pid}...")
        kill_status = client.task_kill(pid, signal=15, wait_seconds=1.0)
        print(f"task_kill result: {kill_status}")
        assert kill_status == "killed", f"Expected 'killed' status after SIGTERM, got '{kill_status}'"
        assert client.task_status(pid) == "exited", "Process should be exited after successful kill"

        # --- Test already exited ---
        print(f"Attempting to kill already exited PID {pid}...")
        kill_status_again = client.task_kill(pid)
        print(f"task_kill result: {kill_status_again}")
        assert kill_status_again == "already_exited", "Expected 'already_exited' status"

        # --- Test force kill (SIGKILL) ---
        print("Launching another process for SIGKILL test...")
        handle_kill = client.launch("sleep 30")
        pid_kill = handle_kill.pid
        print(f"Launched with PID: {pid_kill}")
        time.sleep(1)
        assert client.task_status(pid_kill) == "running", f"Process {pid_kill} should be running"
        # Use a signal that sleep won't catch (like SIGUSR1=10) then fallback to SIGKILL
        print(f"Sending SIGUSR1 (10) then SIGKILL (9) to PID {pid_kill}...")
        kill_status_force = client.task_kill(pid_kill, signal=10, force_kill_signal=9, wait_seconds=1.0)
        print(f"task_kill result: {kill_status_force}")
        assert kill_status_force == "killed", f"Expected 'killed' status after fallback SIGKILL, got '{kill_status_force}'"
        assert client.task_status(pid_kill) == "exited", "Process should be exited after SIGKILL"

        print("Assertions passed.")

    finally:
        # Cleanup any potentially lingering sleep processes
        if client:
            if pid and client.task_status(pid) == 'running': client.task_kill(pid, force_kill_signal=9)
            if 'pid_kill' in locals() and pid_kill and client.task_status(pid_kill) == 'running': client.task_kill(pid_kill, force_kill_signal=9)


# --- Tests for Sudo Handling ---

# Note: These tests require careful setup of the Docker container or test environment
# to have scenarios where passwordless sudo works, fails, and where passworded sudo is needed.
# The default Dockerfile provides passwordless sudo for 'testuser'.
# To test SudoRequired/Passworded Sudo, you'd need to modify the container setup.

def test_sudo_passwordless_success(ssh_client):
    """Tests running a command with sudo when passwordless sudo is configured."""
    print("\n--- test_sudo_passwordless_success ---")
    client = ssh_client
    cmd = "whoami" # Command that shows user
    print(f"Running '{cmd}' with sudo=True (expecting passwordless success)")
    handle = client.run(cmd, sudo=True)
    assert handle.exit_code == 0
    output = "".join(handle.tail()).strip()
    print(f"Output: {output}")
    assert output == "root", f"Expected 'root', got '{output}'"
    print("Assertions passed.")

# @pytest.mark.skip(reason="Requires test environment without passwordless sudo for testuser")
def test_sudo_required_exception(ssh_client):
    """Tests that SudoRequired is raised if passwordless sudo fails and no password provided."""
    print("\n--- test_sudo_required_exception ---")
    # Assumes client is configured WITHOUT sudo_password
    # Assumes the command requires sudo and testuser doesn't have passwordless access for it
    client = get_client(force_new=True, sudo_password=None) # Ensure no sudo password
    cmd = "touch /root/sudorequired_test.txt" # Example command requiring root
    print(f"Running '{cmd}' with sudo=True (expecting SudoRequired)")
    try:
        with pytest.raises(SudoRequired) as excinfo:
            client.run(cmd, sudo=True)
        print(f"Caught expected SudoRequired: {excinfo.value}")
        assert cmd in str(excinfo.value)
        # Verify file was NOT created
        ls_handle = client.run("ls /root/sudorequired_test.txt")
        assert ls_handle.exit_code != 0, "File should not have been created"
        print("Assertions passed.")
    finally:
        client.close()


# @pytest.mark.skip(reason="Requires test environment needing sudo password and client configured with it")
def test_sudo_with_password_success(ssh_client):
    """Tests running a command with sudo using a provided password."""
    print("\n--- test_sudo_with_password_success ---")
    # Assumes client is configured WITH sudo_password
    # Assumes the command requires sudo and testuser needs password for it
    client = get_client(force_new=True, sudo_password=TEST_SUDO_PASSWORD)
    cmd = "touch /root/sudowithpwd_test.txt && echo 'Created'"
    cleanup_cmd = "rm -f /root/sudowithpwd_test.txt"
    print(f"Running '{cmd}' with sudo=True (expecting password success)")
    try:
        handle = client.run(cmd, sudo=True)
        assert handle.exit_code == 0
        output = "".join(handle.tail()).strip()
        assert output == "Created", f"Expected 'Created', got '{output}'"
        print("Command succeeded.")
        # Verify file was created
        ls_handle = client.run("ls /root/sudowithpwd_test.txt", sudo=True) # Use sudo to check
        assert ls_handle.exit_code == 0, "File should have been created"
        print("File creation verified.")
    finally:
        # Cleanup the created file
        try:
            print(f"Cleaning up with: {cleanup_cmd}")
            client.run(cleanup_cmd, sudo=True)
        except Exception as e:
            print(f"Sudo cleanup failed: {e}")
        client.close()
    print("Assertions passed.")


# --- Tests for File Editing ---
# Add tests for replace_line/replace_block, especially the sudo path fixes:
# - test_replace_content_sudo_perms
# - test_replace_content_sudo_noread_fail
# - test_replace_content_no_change


# --- Manual Execution ---
if __name__ == "__main__":
    print("Starting SSH client tests...")
    print("Ensure your SSH test container is running and configured correctly.")
    print(f"Target: {SSH_USER}@{SSH_HOST}:{SSH_PORT}")
    if SSH_KEYFILE: print(f"Using Keyfile: {SSH_KEYFILE}")
    else: print("Using Password authentication.")
    if TEST_SUDO_PASSWORD: print("Sudo password configured for tests.")
    print("-" * 30)

    # It's better to use pytest runner: `pytest testing/tests_basic.py`
    # Manual sequential execution (less ideal):
    try:
        # Use fixture manually if not using pytest runner
        client = get_client(force_new=True) # Get a client for manual runs

        test_connection_and_simple_run(client)
        test_run_failure(client)


        test_file_upload_download(client)
        test_status_command(client)
        test_busy_error_on_concurrent_run(client) # Needs its own client handling internally
        test_command_io_timeout(client)
        test_command_runtime_timeout(client) # Needs its own client handling internally
        test_history_trimming(client) # Needs its own client handling internally
        test_launch_and_status(client)
        test_launch_with_redirection(client)
        test_task_kill(client) # Needs its own client handling internally

        # Sudo tests (may require specific environment setup)
        test_sudo_passwordless_success(client)
        # test_sudo_required_exception(client) # Requires modified env
        # test_sudo_with_password_success(client) # Requires modified env & password

        # Add calls to file editing tests here

    finally:
        if 'client' in locals() and client:
             client.close() # Close the manually created client

    print("\n" + "=" * 30)
    print("All specified tests finished.")
    print("=" * 30)

