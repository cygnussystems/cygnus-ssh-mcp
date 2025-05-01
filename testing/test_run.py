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
        connect_timeout=15, # Slightly longer timeout for test environments
        history_limit=50,   # Default history limit
        tail_keep=100       # Default tail keep
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
    # Check if the test function needs specific client args (e.g., history_limit)
    marker = request.node.get_closest_marker("client_kwargs")
    kwargs = marker.args[0] if marker else {}
    client_instance = get_client(force_new=True, **kwargs) # Force new client for each test function
    yield client_instance
    # Teardown: close connection after test function finishes
    print("\nClosing client connection (fixture teardown)...")
    client_instance.close()


# --- Test Functions ---

def test_simple_run(ssh_client):
    """Tests running a simple command successfully."""
    print("\n--- test_simple_run ---")
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
        # Wait briefly for kill signal to be processed
        time.sleep(1.0)
        status = client.task_status(target_pid) # Assumes task_status uses a separate channel
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


@pytest.mark.client_kwargs({'history_limit': 5})
def test_history_trimming(ssh_client):
    """Tests that command history is trimmed to the specified limit."""
    print("\n--- test_history_trimming ---")
    # Client is created by fixture with history_limit=5 due to marker
    client = ssh_client
    history_limit = client._history_limit # Get actual limit from client

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


def test_output_tail_and_chunk(ssh_client):
    """Tests retrieving output using tail and chunk modes."""
    print("\n--- test_output_tail_and_chunk ---")
    client = ssh_client
    # Generate predictable output
    num_lines = 15
    cmd = f"for i in $(seq 1 {num_lines}); do echo \"Line $i\"; done"
    print(f"Running command to generate {num_lines} lines: {cmd}")
    handle = client.run(cmd)
    assert handle.exit_code == 0
    assert handle.total_lines == num_lines

    # Test tail
    print("Testing output(mode='tail')")
    tail_5 = client.output(handle.id, mode='tail', n=5)
    assert len(tail_5) == 5
    assert tail_5[0].strip() == f"Line {num_lines - 4}"
    assert tail_5[-1].strip() == f"Line {num_lines}"

    tail_all = client.output(handle.id, mode='tail', n=num_lines + 5) # Ask for more than available
    assert len(tail_all) == num_lines
    assert tail_all[0].strip() == "Line 1"

    # Test chunk
    print("Testing output(mode='chunk')")
    chunk_1 = client.output(handle.id, mode='chunk', start=0, n=5)
    assert len(chunk_1) == 5
    assert chunk_1[0].strip() == "Line 1"
    assert chunk_1[-1].strip() == "Line 5"

    chunk_2 = client.output(handle.id, mode='chunk', start=10, n=10) # Request past end
    assert len(chunk_2) == num_lines - 10 # Should return only available lines
    assert chunk_2[0].strip() == "Line 11"
    assert chunk_2[-1].strip() == f"Line {num_lines}"

    # Test invalid start index
    with pytest.raises(ValueError):
        client.output(handle.id, mode='chunk', start=-1, n=5)
    with pytest.raises(ValueError):
        client.output(handle.id, mode='chunk', start='abc', n=5) # Non-integer start

    print("Assertions passed.")


@pytest.mark.client_kwargs({'tail_keep': 10}) # Keep only 10 lines in buffer
def test_output_purged(ssh_client):
    """Tests that OutputPurged is raised when requesting chunks outside the buffer."""
    print("\n--- test_output_purged ---")
    client = ssh_client # Fixture provides client with tail_keep=10
    tail_keep = client._tail_keep
    num_lines = tail_keep + 5 # Generate more lines than kept in buffer
    cmd = f"for i in $(seq 1 {num_lines}); do echo \"Line $i\"; done"
    print(f"Running command to generate {num_lines} lines (tail_keep={tail_keep})...")
    handle = client.run(cmd)
    assert handle.exit_code == 0
    assert handle.total_lines == num_lines
    assert handle.truncated is True # Should be truncated

    # Verify tail works (gets last tail_keep lines)
    tail_output = client.output(handle.id, mode='tail', n=tail_keep + 2)
    assert len(tail_output) == tail_keep
    assert tail_output[0].strip() == f"Line {num_lines - tail_keep + 1}" # First line in buffer
    assert tail_output[-1].strip() == f"Line {num_lines}" # Last line

    # Try to get chunk starting at line 0 (should be purged)
    print("Attempting to get chunk starting at index 0 (expecting OutputPurged)...")
    with pytest.raises(OutputPurged) as excinfo:
        client.output(handle.id, mode='chunk', start=0, n=5)
    print(f"Caught expected OutputPurged: {excinfo.value}")
    assert str(handle.id) in str(excinfo.value)

    # Try to get chunk starting just before the buffer starts
    buffer_start_index = num_lines - tail_keep
    print(f"Buffer starts at index {buffer_start_index}. Attempting chunk at {buffer_start_index - 1}...")
    with pytest.raises(OutputPurged):
        client.output(handle.id, mode='chunk', start=buffer_start_index - 1, n=1)

    # Try to get chunk starting exactly where the buffer starts (should work)
    print(f"Attempting chunk starting at buffer start index {buffer_start_index}...")
    chunk_at_start = client.output(handle.id, mode='chunk', start=buffer_start_index, n=3)
    assert len(chunk_at_start) == 3
    assert chunk_at_start[0].strip() == f"Line {buffer_start_index + 1}"

    print("Assertions passed.")


# --- Sudo Tests ---

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

@pytest.mark.skip(reason="Requires test environment without passwordless sudo for testuser")
def test_sudo_required_exception(ssh_client):
    """Tests that SudoRequired is raised if passwordless sudo fails and no password provided."""
    print("\n--- test_sudo_required_exception ---")
    # Assumes client is configured WITHOUT sudo_password
    # Assumes the command requires sudo and testuser doesn't have passwordless access for it
    # Fixture needs modification or a separate fixture for this test
    client = get_client(force_new=True, sudo_password=None) # Ensure no sudo password
    cmd = "touch /root/sudorequired_test.txt" # Example command requiring root
    print(f"Running '{cmd}' with sudo=True (expecting SudoRequired)")
    try:
        with pytest.raises(SudoRequired) as excinfo:
            client.run(cmd, sudo=True)
        print(f"Caught expected SudoRequired: {excinfo.value}")
        assert cmd in str(excinfo.value)
        # Verify file was NOT created (best effort check)
        try:
            ls_handle = client.run("ls /root/sudorequired_test.txt")
            assert ls_handle.exit_code != 0, "File should not have been created"
        except CommandFailed:
            pass # Expected if ls fails
        print("Assertions passed.")
    finally:
        client.close()


@pytest.mark.skip(reason="Requires test environment needing sudo password and client configured with it")
def test_sudo_with_password_success(ssh_client):
    """Tests running a command with sudo using a provided password."""
    print("\n--- test_sudo_with_password_success ---")
    # Assumes client is configured WITH sudo_password
    # Assumes the command requires sudo and testuser needs password for it
    # Fixture needs modification or a separate fixture for this test
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


# --- Manual Execution ---
if __name__ == "__main__":
    print("Starting SSH client run command tests...")
    print("Ensure your SSH test container is running and configured correctly.")
    print(f"Target: {SSH_USER}@{SSH_HOST}:{SSH_PORT}")
    if SSH_KEYFILE: print(f"Using Keyfile: {SSH_KEYFILE}")
    else: print("Using Password authentication.")
    if TEST_SUDO_PASSWORD: print("Sudo password configured for tests.")
    print("-" * 30)

    # It's better to use pytest runner: `pytest testing/test_run.py`
    # Manual sequential execution (less ideal):
    client = None
    try:
        client = get_client(force_new=True) # Get a client for manual runs

        test_simple_run(client)
        test_run_failure(client)
        test_busy_error_on_concurrent_run(client) # Needs its own client handling internally
        test_command_io_timeout(client)
        test_command_runtime_timeout(client) # Needs its own client handling internally
        test_history_trimming(get_client(force_new=True, history_limit=5)) # Needs specific client
        test_output_tail_and_chunk(client)
        test_output_purged(get_client(force_new=True, tail_keep=10)) # Needs specific client

        # Sudo tests (may require specific environment setup)
        test_sudo_passwordless_success(client)
        # test_sudo_required_exception(client) # Requires modified env
        # test_sudo_with_password_success(client) # Requires modified env & password

    finally:
        if client:
             client.close() # Close the manually created client

    print("\n" + "=" * 30)
    print("Run command tests finished.")
    print("=" * 30)
