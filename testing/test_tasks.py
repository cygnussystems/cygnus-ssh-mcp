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
        # Use run() to check for the log file, handle potential failure
        try:
            ls_handle = client.run(f"ls {shlex.quote(default_log_path)}")
            assert ls_handle.exit_code == 0, f"Default log file {default_log_path} not found"
            # Cleanup log file
            client.run(f"rm -f {shlex.quote(default_log_path)}")
        except CommandFailed as e:
            # This might happen if the rename failed in launch() due to timing/busy
            pytest.fail(f"Failed to find or cleanup default log file {default_log_path}: {e}")
        except BusyError as e:
             pytest.fail(f"Client was busy during log file check/cleanup: {e}")


        print("Assertions passed.")

    finally:
        # Ensure process is killed if test failed mid-run
        if client and pid:
            try:
                if client.task_status(pid) == 'running':
                    print(f"Attempting cleanup: killing PID {pid}")
                    client.task_kill(pid, force_kill_signal=9)
            except Exception as final_cleanup_err:
                 print(f"Error during final PID cleanup: {final_cleanup_err}")


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
        time.sleep(2) # Give time for sleep 1 and file writes
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
            if pid:
                try:
                    if client.task_status(pid) == 'running': client.task_kill(pid, force_kill_signal=9)
                except Exception as final_cleanup_err:
                    print(f"Error during final PID cleanup: {final_cleanup_err}")
            try:
                # Use run() for cleanup, handle potential errors
                client.run(f"rm -f {shlex.quote(remote_out_log)} {shlex.quote(remote_err_log)}", io_timeout=5, runtime_timeout=10)
            except Exception as log_cleanup_err:
                print(f"Warning: Failed to cleanup remote log files: {log_cleanup_err}")
        if local_out_log and os.path.exists(local_out_log): os.unlink(local_out_log)
        if local_err_log and os.path.exists(local_err_log): os.unlink(local_err_log)


def test_task_kill(ssh_client):
    """Tests killing a launched background command using task_kill."""
    print("\n--- test_task_kill ---")
    client = ssh_client
    pid = None
    pid_kill = None # Define pid_kill outside the block
    try:
        cmd = "sleep 30"
        print(f"Launching long-running command: {cmd}")
        handle = client.launch(cmd)
        pid = handle.pid
        print(f"Launched with PID: {pid}")
        time.sleep(1) # Allow process to start
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
        time.sleep(1) # Allow process to start
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
            if pid:
                try:
                    if client.task_status(pid) == 'running': client.task_kill(pid, force_kill_signal=9)
                except Exception as final_cleanup_err:
                    print(f"Error during final PID cleanup (pid={pid}): {final_cleanup_err}")
            if pid_kill:
                try:
                    if client.task_status(pid_kill) == 'running': client.task_kill(pid_kill, force_kill_signal=9)
                except Exception as final_cleanup_err:
                    print(f"Error during final PID cleanup (pid_kill={pid_kill}): {final_cleanup_err}")


def test_launch_sudo(ssh_client):
    """Tests launching a background command with sudo."""
    print("\n--- test_launch_sudo ---")
    client = ssh_client
    pid = None
    test_file = f"/tmp/launch_sudo_test_{int(time.time())}.txt"
    try:
        # Command that requires sudo to write to /root (or use a simpler sudo command)
        # Let's use `sleep` but run it via `sudo` to test the mechanism
        sleep_duration = 3
        cmd = f"sleep {sleep_duration}"
        print(f"Launching command with sudo: {cmd}")

        handle = client.launch(cmd, sudo=True, log_output=True)

        assert handle and handle.pid, "launch(sudo=True) should return a handle with a PID"
        pid = handle.pid
        print(f"Command launched with PID: {pid}, Handle ID: {handle.id}")

        print(f"Checking status for PID {pid} shortly after launch...")
        status = client.task_status(pid)
        print(f"Status: {status}")
        # Note: task_status runs 'kill -0 PID' which doesn't require sudo itself
        assert status == "running", f"Expected 'running', got '{status}'"

        print(f"Waiting for {sleep_duration + 1} seconds...")
        time.sleep(sleep_duration + 1)
        print(f"Checking status for PID {pid} after expected completion...")
        status = client.task_status(pid)
        print(f"Status: {status}")
        assert status == "exited", f"Expected 'exited' after completion, got '{status}'"

        # Check default log file existence (should be owned by root if created by sudo process)
        default_log_path = f"/tmp/task-{pid}.log"
        print(f"Checking for default log file: {default_log_path}")
        try:
            # Use sudo=True to check/remove the log file if launch used sudo
            ls_handle = client.run(f"ls {shlex.quote(default_log_path)}", sudo=True)
            assert ls_handle.exit_code == 0, f"Default log file {default_log_path} not found (checked with sudo)"
            # Cleanup log file using sudo
            client.run(f"rm -f {shlex.quote(default_log_path)}", sudo=True)
        except CommandFailed as e:
            pytest.fail(f"Failed to find or cleanup default log file {default_log_path} using sudo: {e}")
        except BusyError as e:
             pytest.fail(f"Client was busy during log file check/cleanup: {e}")
        except SudoRequired as e:
             pytest.fail(f"Sudo was required unexpectedly during log check/cleanup: {e}")


        print("Assertions passed.")

    finally:
        # Ensure process is killed if test failed mid-run
        if client and pid:
            try:
                if client.task_status(pid) == 'running':
                    print(f"Attempting cleanup: killing PID {pid} (using sudo if launch used it - task_kill handles this)")
                    # task_kill needs sudo=True if the process was launched with sudo=True
                    # However, task_kill itself uses run() which handles sudo.
                    # Let's assume task_kill needs the same sudo flag as launch.
                    client.task_kill(pid, force_kill_signal=9, sudo=True)
            except Exception as final_cleanup_err:
                 print(f"Error during final PID cleanup: {final_cleanup_err}")
        # Cleanup test file if created
        if client:
            try:
                client.run(f"rm -f {shlex.quote(test_file)}", sudo=True)
            except: pass # Ignore cleanup errors


# --- Manual Execution ---
if __name__ == "__main__":
    print("Starting SSH client task management tests...")
    print("Ensure your SSH test container is running and configured correctly.")
    print(f"Target: {SSH_USER}@{SSH_HOST}:{SSH_PORT}")
    if SSH_KEYFILE: print(f"Using Keyfile: {SSH_KEYFILE}")
    else: print("Using Password authentication.")
    if TEST_SUDO_PASSWORD: print("Sudo password configured for tests.")
    print("-" * 30)

    # It's better to use pytest runner: `pytest testing/test_tasks.py`
    # Manual sequential execution (less ideal):
    client = None
    try:
        client = get_client(force_new=True) # Get a client for manual runs

        test_launch_and_status(client)
        test_launch_with_redirection(client)
        test_task_kill(client) # Needs its own client handling internally
        test_launch_sudo(client)

    finally:
        if client:
             client.close() # Close the manually created client

    print("\n" + "=" * 30)
    print("Task management tests finished.")
    print("=" * 30)
