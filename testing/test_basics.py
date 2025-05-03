import os
import tempfile
import time
import shlex
from test_utils import get_client, cleanup_client, print_test_header, print_test_footer, SSH_USER


# --- Test Functions ---

def test_connection():
    """Test basic connection and simple command execution."""
    print_test_header("test_connection")
    client = get_client(force_new=True)
    try:
        # Test basic connection state
        assert client._client is not None, "Client object should exist"
        assert client._client.get_transport() is not None, "SSH transport should exist"
        assert client._client.get_transport().is_active(), "Client connection should be active"
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
            assert output == SSH_USER, f"Expected USER={SSH_USER}, got {output}"
        print(f"USER environment variable: {output}")
        
        print("Basic connection and command execution successful.")
    except Exception as e:
        print(f"Connection test failed: {e}")
        raise
    finally:
        cleanup_client(client)
        print_test_footer()



def test_full_status(ssh_client):
    """Tests the combined status() method."""
    print("\n--- test_full_status ---")
    client = ssh_client
    print("Calling client.status()")
    status_info = client.full_status()
    print("Status info received:")
    for key, value in status_info.items():
        print(f"  {key:<12}: {value}")

    assert 'error' not in status_info, f"Status command returned an error: {status_info.get('error')}"
    
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

    for key in expected_keys:
        assert status_info[key] is not None, f"Value for key '{key}' should not be None"
        assert status_info[key] != 'n/a', f"Value for key '{key}' should not be 'n/a'"

    if SSH_USER:
        remote_user = status_info['user'].split('\\')[-1]
        assert remote_user == SSH_USER, f"Expected user '{SSH_USER}', got '{status_info['user']}'"

    print("Assertions passed.")

def test_user_status(ssh_client):
    """Tests the user_status() method."""
    print("\n--- test_user_status ---")
    client = ssh_client
    user_info = client.os_ops.user_status()
    
    assert 'user' in user_info, "Missing 'user' in user status"
    assert 'cwd' in user_info, "Missing 'cwd' in user status"
    assert 'time' in user_info, "Missing 'time' in user status"
    
    if SSH_USER:
        remote_user = user_info['user'].split('\\')[-1]
        assert remote_user == SSH_USER, f"Expected user '{SSH_USER}', got '{user_info['user']}'"
    
    print("User status assertions passed.")

def test_hardware_info(ssh_client):
    """Tests the hardware_info() method."""
    print("\n--- test_hardware_info ---")
    client = ssh_client
    hw_info = client.os_ops.hardware_info()
    
    assert 'cpu_count' in hw_info, "Missing 'cpu_count' in hardware info"
    assert 'mem_total_mb' in hw_info, "Missing 'mem_total_mb' in hardware info"
    assert 'mem_free_mb' in hw_info, "Missing 'mem_free_mb' in hardware info"
    assert 'load_avg' in hw_info, "Missing 'load_avg' in hardware info"
    
    # Basic sanity checks on values
    assert int(hw_info['cpu_count']) > 0, "CPU count should be positive"
    assert int(hw_info['mem_total_mb']) > 0, "Total memory should be positive"
    assert int(hw_info['mem_free_mb']) >= 0, "Free memory should be non-negative"
    
    print("Hardware info assertions passed.")

def test_network_info(ssh_client):
    """Tests the network_info() method."""
    print("\n--- test_network_info ---")
    client = ssh_client
    net_info = client.os_ops.network_info()
    
    assert 'hostname' in net_info, "Missing 'hostname' in network info"
    assert 'ip_address' in net_info, "Missing 'ip_address' in network info"
    
    # Basic validation of IP address format
    if net_info['ip_address'] != 'n/a':
        import re
        ip_pattern = re.compile(r'^\d{1,3}(\.\d{1,3}){3}$')
        assert ip_pattern.match(net_info['ip_address']), f"Invalid IP address format: {net_info['ip_address']}"
    
    print("Network info assertions passed.")

def test_disk_info(ssh_client):
    """Tests the disk_info() method."""
    print("\n--- test_disk_info ---")
    client = ssh_client
    disk_info = client.os_ops.disk_info()
    
    assert 'disk_total' in disk_info, "Missing 'disk_total' in disk info"
    assert 'disk_free' in disk_info, "Missing 'disk_free' in disk info"
    
    # Basic validation of disk values
    assert disk_info['disk_total'].endswith('G') or disk_info['disk_total'].endswith('M'), \
        "Disk total should end with G or M"
    assert disk_info['disk_free'].endswith('G') or disk_info['disk_free'].endswith('M'), \
        "Disk free should end with G or M"
    
    print("Disk info assertions passed.")


if __name__ == "__main__":
    print("Running basic tests...")
    client = get_client(force_new=True)
    try:
        test_connection()
        test_full_status(client)
        test_user_status(client)
        test_hardware_info(client)
        test_network_info(client)
        test_disk_info(client)
        print("All basic tests completed.")
    finally:
        cleanup_client(client)
