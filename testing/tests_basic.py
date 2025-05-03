import os
import tempfile
import time
import shlex
from test_utils import get_client, cleanup_client, print_test_header, print_test_footer, SSH_USER


# --- Test Functions ---

def test_connection():
    """Test basic connection."""
    print_test_header("test_connection")
    client = get_client(force_new=True)
    try:
        assert client._client is not None, "Client object should exist"
        assert client._client.is_active(), "Client connection should be active"
        print("Connection active assertion passed.")
        
        handle = client.run("pwd")
        assert handle.exit_code == 0
        print("Basic 'pwd' command successful.")
    finally:
        cleanup_client(client)
        print_test_footer()



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


if __name__ == "__main__":
    print("Running basic tests...")
    test_connection()
    #test_file_upload_download()
    test_status_command()
    print("All basic tests completed.")
