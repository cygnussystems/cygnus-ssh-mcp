import os
import tempfile
import time
from test_utils import get_client, cleanup_client, print_test_header, print_test_footer


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


if __name__ == "__main__":
    print("Running basic tests...")
    test_connection()
    test_file_upload_download()
    test_status_command()
    print("All basic tests completed.")
