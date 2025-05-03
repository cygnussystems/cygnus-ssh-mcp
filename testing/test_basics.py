import os
import tempfile
import time
import shlex
import pytest # Import pytest for fixtures
from test_utils import get_client, cleanup_client, print_test_header, print_test_footer, SSH_USER


# --- Test Fixture ---

@pytest.fixture(scope="module") # Use module scope for efficiency if tests don't interfere
def ssh_client():
    """Pytest fixture to provide a connected SshClient instance."""
    print("\n--- Setting up SSH client fixture ---")
    client = get_client(force_new=True) # Ensure a fresh client for the module
    yield client # Provide the client to the tests
    print("\n--- Tearing down SSH client fixture ---")
    cleanup_client(client)


# --- Test Functions ---

def test_connection(ssh_client): # Use the fixture
    """Test basic connection and simple command execution."""
    print_test_header("test_connection")
    client = ssh_client # Get client from fixture
    try:
        # Test basic connection state (already connected by fixture)
        assert client._client is not None, "Client object should exist"
        transport = client._client.get_transport()
        assert transport is not None, "SSH transport should exist"
        # Use the transport's is_active() method correctly
        assert transport.is_active(), "Client connection should be active"
        print("Connection active assertion passed.")
        
        # Test simple command execution with timeout
        test_cmd = "pwd"
        print(f"Testing basic command execution: {test_cmd}")
        handle = client.run(test_cmd, io_timeout=10, runtime_timeout=15)
        assert handle.exit_code == 0, f"Command '{test_cmd}' failed with exit code {handle.exit_code}"
        
        # Verify command output is reasonable
        output = "".join(handle.tail(handle.total_lines))
        assert output.strip() != "", "Command output should not be empty"
        assert "/" in output, "pwd output should contain a path separator"
        print(f"Command output: {output.strip()}")
        
        # Test environment variables
        test_cmd = "echo $USER"
        print(f"Testing environment variable: {test_cmd}")
        handle = client.run(test_cmd, io_timeout=10, runtime_timeout=15)
        assert handle.exit_code == 0, f"Command '{test_cmd}' failed"
        output = "".join(handle.tail(handle.total_lines)).strip()
        if SSH_USER:
            # Handle potential domain\user format from Windows hosts if needed
            remote_user = output.split('\\')[-1] 
            assert remote_user == SSH_USER, f"Expected USER={SSH_USER}, got {output}"
        print(f"USER environment variable: {output}")
        
        print("Basic connection and command execution successful.")
    except Exception as e:
        print(f"Connection test failed: {e}")
        raise # Re-raise the exception to fail the test
    finally:
        # No cleanup_client here, fixture handles it
        print_test_footer()



def test_full_status(ssh_client): # Use the fixture
    """Tests the combined full_status() method."""
    print_test_header("test_full_status")
    client = ssh_client
    print("Calling client.full_status()")
    # Call the new combined status method
    status_info = client.os_ops.full_status() 
    print("Status info received:")
    # Pretty print the dictionary
    import json
    print(json.dumps(status_info, indent=2))

    # Check if the 'errors' key exists and report if it does
    if 'errors' in status_info:
        print(f"WARNING: Status command reported errors: {status_info['errors']}")
        # Depending on strictness, you might want to fail here:
        # assert 'errors' not in status_info, f"Status command returned errors: {status_info['errors']}"
    
    # Check for expected keys from all status components
    expected_keys = [
        # From user_status
        'user', 'cwd', 'time',
        # From hardware_info
        'cpu_count', 'mem_total_mb', 'mem_free_mb', 'mem_available_mb', 'load_avg',
        # From network_info
        'hostname', 'ip_address',
        # From disk_info
        'disk_total', 'disk_free'
    ]
    
    missing_keys = [key for key in expected_keys if key not in status_info]
    assert not missing_keys, f"Missing expected keys in status info: {missing_keys}"

    # Check that values are not None and not 'n/a' (unless an error occurred)
    for key in expected_keys:
        assert status_info[key] is not None, f"Value for key '{key}' should not be None"
        # Only assert not 'n/a' if no errors were reported for the relevant component
        # This requires mapping keys back to components or checking the global 'errors' key
        if 'errors' not in status_info: # Simple check: if no errors at all, values should be valid
             assert status_info[key] != 'n/a', f"Value for key '{key}' is 'n/a' unexpectedly"
        # More specific check (example for hardware):
        # elif 'hardware' not in status_info.get('errors', {}) and key in ['cpu_count', 'mem_total_mb', ...]:
        #      assert status_info[key] != 'n/a', f"Value for key '{key}' is 'n/a' unexpectedly"


    # Check specific values like user and numeric types
    if SSH_USER:
        # Handle potential domain\user format
        remote_user = status_info.get('user', 'n/a').split('\\')[-1]
        assert remote_user == SSH_USER, f"Expected user '{SSH_USER}', got '{status_info.get('user')}'"

    # Check numeric types (if no errors reported)
    if 'errors' not in status_info:
        try:
            assert int(status_info['cpu_count']) > 0
            assert int(status_info['mem_total_mb']) > 0
            assert int(status_info['mem_free_mb']) >= 0
            assert int(status_info['mem_available_mb']) >= 0
        except (ValueError, TypeError) as e:
            pytest.fail(f"Failed to parse numeric status value: {e}. Status info: {status_info}")
        except AssertionError as e:
             pytest.fail(f"Numeric status value out of expected range: {e}. Status info: {status_info}")


    print("Assertions passed.")
    print_test_footer()


def test_user_status(ssh_client): # Use the fixture
    """Tests the user_status() method."""
    print_test_header("test_user_status")
    client = ssh_client
    user_info = client.os_ops.user_status()
    print(f"User status info: {user_info}")
    
    assert 'error' not in user_info, f"user_status returned an error: {user_info.get('error')}"
    assert 'user' in user_info and user_info['user'] != 'n/a', "Missing or invalid 'user' in user status"
    assert 'cwd' in user_info and user_info['cwd'] != 'n/a', "Missing or invalid 'cwd' in user status"
    assert 'time' in user_info and user_info['time'] != 'n/a', "Missing or invalid 'time' in user status"
    
    if SSH_USER:
        remote_user = user_info['user'].split('\\')[-1]
        assert remote_user == SSH_USER, f"Expected user '{SSH_USER}', got '{user_info['user']}'"
    
    print("User status assertions passed.")
    print_test_footer()

def test_hardware_info(ssh_client): # Use the fixture
    """Tests the hardware_info() method."""
    print_test_header("test_hardware_info")
    client = ssh_client
    hw_info = client.os_ops.hardware_info()
    print(f"Hardware info: {hw_info}")
    
    assert 'error' not in hw_info, f"hardware_info returned an error: {hw_info.get('error')}"
    assert 'cpu_count' in hw_info and hw_info['cpu_count'] != 'n/a', "Missing 'cpu_count'"
    assert 'mem_total_mb' in hw_info and hw_info['mem_total_mb'] != 'n/a', "Missing 'mem_total_mb'"
    assert 'mem_free_mb' in hw_info and hw_info['mem_free_mb'] != 'n/a', "Missing 'mem_free_mb'"
    assert 'mem_available_mb' in hw_info and hw_info['mem_available_mb'] != 'n/a', "Missing 'mem_available_mb'"
    assert 'load_avg' in hw_info and hw_info['load_avg'] != 'n/a', "Missing 'load_avg'"
    
    # Basic sanity checks on values
    try:
        assert int(hw_info['cpu_count']) > 0, "CPU count should be positive"
        assert int(hw_info['mem_total_mb']) > 0, "Total memory should be positive"
        assert int(hw_info['mem_free_mb']) >= 0, "Free memory should be non-negative"
        assert int(hw_info['mem_available_mb']) >= 0, "Available memory should be non-negative"
    except (ValueError, TypeError) as e:
        pytest.fail(f"Failed to parse numeric hardware value: {e}. Info: {hw_info}")
    except AssertionError as e:
        pytest.fail(f"Hardware value out of expected range: {e}. Info: {hw_info}")

    print("Hardware info assertions passed.")
    print_test_footer()

def test_network_info(ssh_client): # Use the fixture
    """Tests the network_info() method."""
    print_test_header("test_network_info")
    client = ssh_client
    net_info = client.os_ops.network_info()
    print(f"Network info: {net_info}")
    
    assert 'error' not in net_info, f"network_info returned an error: {net_info.get('error')}"
    assert 'hostname' in net_info and net_info['hostname'] != 'n/a', "Missing 'hostname'"
    assert 'interfaces' in net_info, "Missing 'interfaces' list"
    
    # Validate at least one interface exists
    assert len(net_info['interfaces']) > 0, "No network interfaces found"
    
    # Validate each interface has required fields
    for iface in net_info['interfaces']:
        assert 'name' in iface, "Interface missing 'name'"
        assert 'ip_addresses' in iface, "Interface missing 'ip_addresses'"
        # Validate IP addresses if present
        if iface['ip_addresses']:
            import re
            ip_pattern = re.compile(r'^\d{1,3}(\.\d{1,3}){3}(/\d{1,2})?$')
            for ip in iface['ip_addresses']:
                assert ip_pattern.match(ip), f"Invalid IP address format: {ip}"
    
    print("Network info assertions passed.")
    print_test_footer()

def test_disk_info(ssh_client): # Use the fixture
    """Tests the disk_info() method."""
    print_test_header("test_disk_info")
    client = ssh_client
    disk_info = client.os_ops.disk_info()
    print(f"Disk info: {disk_info}")
    
    assert 'error' not in disk_info, f"disk_info returned an error: {disk_info.get('error')}"
    assert 'disk_total' in disk_info and disk_info['disk_total'] != 'n/a', "Missing 'disk_total'"
    assert 'disk_free' in disk_info and disk_info['disk_free'] != 'n/a', "Missing 'disk_free'"
    
    # Basic validation of disk values (expecting human-readable format from df -h)
    if disk_info['disk_total'] != 'n/a':
        assert disk_info['disk_total'][-1].isalpha(), \
            f"Disk total '{disk_info['disk_total']}' should end with a unit (G, M, K, etc.)"
    if disk_info['disk_free'] != 'n/a':
        assert disk_info['disk_free'][-1].isalpha(), \
            f"Disk free '{disk_info['disk_free']}' should end with a unit (G, M, K, etc.)"
    
    print("Disk info assertions passed.")
    print_test_footer()


# Keep the __main__ block for running tests directly if needed,
# but primary execution should be via pytest.
if __name__ == "__main__":
    print("Running basic tests directly (pytest recommended)...")
    # Manually create and cleanup client if not using pytest runner
    main_client = None
    try:
        main_client = get_client(force_new=True)
        # Create a temporary fixture-like object for tests needing it
        class MockFixtureClient:
            def __init__(self, client):
                self.client = client
            def __enter__(self): return self.client
            def __exit__(self, *args): pass # Cleanup handled in finally

        with MockFixtureClient(main_client) as client_for_tests:
             test_connection(client_for_tests) # Pass the client
             test_full_status(client_for_tests)
             test_user_status(client_for_tests)
             test_hardware_info(client_for_tests)
             test_network_info(client_for_tests)
             test_disk_info(client_for_tests)
        print("\nAll basic tests completed.")
    except Exception as e:
        print(f"\n*** Test run failed: {e} ***")
    finally:
        if main_client:
            cleanup_client(main_client)
