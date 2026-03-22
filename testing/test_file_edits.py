# import os
# import time
# import tempfile
# import pytest
# import shlex
# from test_utils import get_client, cleanup_client, print_test_header, print_test_footer, SSH_USER, TEST_SUDO_PASSWORD
# from ssh_client import (
#     SshClient, CommandFailed, BusyError, TaskNotFound, SshError, SudoRequired
# )
#
#
# # --- Test Functions ---
#
# # Helper function to create a test file on the remote host
# def create_remote_test_file(client, path, content, sudo=False):
#     print(f"Creating remote test file: {path} (sudo={sudo})")
#     # Use echo with redirection, handle potential quoting issues
#     # Using printf might be safer for arbitrary content
#     printf_cmd = f"printf '%s' {shlex.quote(content)} > {shlex.quote(path)}"
#     try:
#         client.run(printf_cmd, sudo=sudo)
#     except Exception as e:
#         pytest.fail(f"Failed to create remote test file {path}: {e}")
#
# # Helper function to read a remote file's content
# def read_remote_file(client, path, sudo=False):
#     print(f"Reading remote file: {path} (sudo={sudo})")
#     try:
#         handle = client.run(f"cat {shlex.quote(path)}", sudo=sudo)
#         if handle.exit_code != 0:
#             pytest.fail(f"Failed to read remote file {path}, exit code {handle.exit_code}")
#         return "".join(handle.tail(handle.total_lines))
#     except Exception as e:
#         pytest.fail(f"Failed to read remote file {path}: {e}")
#
# # Helper function for cleanup
# def cleanup_remote_file(client, path, sudo=False):
#     print(f"Cleaning up remote file: {path} (sudo={sudo})")
#     try:
#         client.run(f"rm -f {shlex.quote(path)}", sudo=sudo, io_timeout=5, runtime_timeout=10)
#     except Exception as e:
#         print(f"Warning: Failed to cleanup remote file {path}: {e}")
#
#
# # --- replace_line Tests ---
#
# def test_replace_line_simple(ssh_client):
#     """Tests replacing a single line without sudo."""
#     print_test_header("test_replace_line_simple")
#     client = ssh_client
#     remote_path = f"/tmp/replace_line_test_{int(time.time())}.txt"
#     original_content = "Line 1\nLine to replace\nLine 3\nAnother line to replace\n"
#     old_line = "Line to replace"
#     new_line = "Line has been replaced"
#     expected_content = "Line 1\nLine has been replaced\nLine 3\nAnother line to replace\n" # count=1 default
#
#     try:
#         create_remote_test_file(client, remote_path, original_content)
#         print(f"Original content: {repr(original_content)}")
#         client.replace_line(remote_path, old_line, new_line) # Default count=1
#         actual_content = read_remote_file(client, remote_path)
#         print(f"Actual content: {repr(actual_content)}")
#         print(f"Expected content: {repr(expected_content)}")
#         # Normalize line endings and trim any extra newlines
#         normalized_actual = actual_content.replace('\r\n', '\n').rstrip('\n')
#         normalized_expected = expected_content.rstrip('\n')
#         assert normalized_actual == normalized_expected, f"Content mismatch:\nExpected: {repr(expected_content)}\nActual: {repr(actual_content)}"
#         print("Simple replace_line (count=1) successful.")
#     finally:
#         cleanup_remote_file(client, remote_path)
#         print_test_footer()
#
#
#
# def test_replace_line_multiple(ssh_client):
#     """Tests replacing multiple occurrences of a line."""
#     print_test_header("test_replace_line_multiple")
#     client = ssh_client
#     remote_path = f"/tmp/replace_line_multi_{int(time.time())}.txt"
#     original_content = "Line 1\nReplace Me\nLine 3\nReplace Me\nLine 5\nReplace Me\n"
#     old_line = "Replace Me"
#     new_line = "Replaced!"
#     expected_content = "Line 1\nReplaced!\nLine 3\nReplaced!\nLine 5\nReplace Me\n" # count=2
#
#     try:
#         create_remote_test_file(client, remote_path, original_content)
#         client.replace_line(remote_path, old_line, new_line, count=2)
#         actual_content = read_remote_file(client, remote_path)
#         # Normalize line endings and trim any extra newlines
#         normalized_actual = actual_content.replace('\r\n', '\n').rstrip('\n')
#         normalized_expected = expected_content.rstrip('\n')
#         assert normalized_actual == normalized_expected, f"Content mismatch:\nExpected: {repr(expected_content)}\nActual: {repr(actual_content)}"
#         print("replace_line (count=2) successful.")
#     finally:
#         cleanup_remote_file(client, remote_path)
#         print_test_footer()
#
#
#
# def test_replace_line_no_match(ssh_client):
#     """Tests replace_line when the old_line doesn't exist."""
#     print_test_header("test_replace_line_no_match")
#     client = ssh_client
#     remote_path = f"/tmp/replace_line_nomatch_{int(time.time())}.txt"
#     original_content = "Line 1\nLine 2\nLine 3\n"
#     old_line = "Nonexistent Line"
#     new_line = "Should not appear"
#
#     try:
#         create_remote_test_file(client, remote_path, original_content)
#         client.replace_line(remote_path, old_line, new_line)
#         actual_content = read_remote_file(client, remote_path)
#         assert actual_content == original_content # Content should be unchanged
#         print("replace_line with no match successful (no change).")
#     finally:
#         cleanup_remote_file(client, remote_path)
#         print_test_footer()
#
#
#
# def test_replace_line_sudo(ssh_client):
#     """Tests replacing a line in a file requiring sudo."""
#     print_test_header("test_replace_line_sudo")
#     client = ssh_client
#     # Use a file owned by root in /tmp for simplicity, assuming testuser can write to /tmp for temp file
#     remote_path = f"/tmp/replace_line_sudo_{int(time.time())}.txt"
#     original_content = "Root Line 1\nRoot Line to Replace\nRoot Line 3\n"
#     old_line = "Root Line to Replace"
#     new_line = "Sudo Replaced This"
#     expected_content = "Root Line 1\nSudo Replaced This\nRoot Line 3\n"
#
#     try:
#         # Create the file as root
#         create_remote_test_file(client, remote_path, original_content, sudo=True)
#         # Ensure ownership is root (optional check)
#         ls_handle = client.run(f"ls -l {shlex.quote(remote_path)}")
#         assert "root root" in "".join(ls_handle.tail()), f"File {remote_path} not owned by root"
#
#         # Perform replacement with sudo
#         client.replace_line(remote_path, old_line, new_line, sudo=True)
#
#         # Read back the file (can use sudo or check permissions allow testuser read)
#         actual_content = read_remote_file(client, remote_path, sudo=True) # Read with sudo to be safe
#         # Normalize line endings and handle trailing newlines
#         normalized_actual = actual_content.replace('\r\n', '\n').rstrip('\n')
#         normalized_expected = expected_content.rstrip('\n')
#         assert normalized_actual == normalized_expected, f"Content mismatch:\nExpected: {repr(expected_content)}\nActual: {repr(actual_content)}"
#
#         # Verify permissions were likely preserved (basic check: still owned by root)
#         ls_handle_after = client.run(f"ls -l {shlex.quote(remote_path)}")
#         assert "root root" in "".join(ls_handle_after.tail()), f"File {remote_path} ownership changed after sudo replace"
#
#         print("replace_line with sudo successful.")
#     finally:
#         cleanup_remote_file(client, remote_path, sudo=True)
#         print_test_footer()
#
#
#
#
# # --- replace_block Tests ---
#
# def test_replace_block_simple(ssh_client):
#     """Tests replacing a block of text without sudo."""
#     print_test_header("test_replace_block_simple")
#     client = ssh_client
#     remote_path = f"/tmp/replace_block_test_{int(time.time())}.txt"
#     old_block = "--- Start Block ---\nLine A\nLine B\n--- End Block ---"
#     new_block = "--- Replacement ---\nNew Content\n--- End Replacement ---"
#     original_content = f"Preamble\n{old_block}\nPostamble\n{old_block}\nEnd."
#     expected_content = f"Preamble\n{new_block}\nPostamble\n{new_block}\nEnd."
#
#     try:
#         create_remote_test_file(client, remote_path, original_content)
#         client.replace_block(remote_path, old_block, new_block)
#         actual_content = read_remote_file(client, remote_path)
#         # Normalize line endings and trim any extra newlines
#         normalized_actual = actual_content.replace('\r\n', '\n').rstrip('\n')
#         normalized_expected = expected_content.rstrip('\n')
#         assert normalized_actual == normalized_expected, f"Content mismatch:\nExpected: {repr(expected_content)}\nActual: {repr(actual_content)}"
#         print("Simple replace_block successful.")
#     finally:
#         cleanup_remote_file(client, remote_path)
#         print_test_footer()
#
#
#
# def test_replace_block_no_match(ssh_client):
#     """Tests replace_block when the old_block doesn't exist."""
#     print_test_header("test_replace_block_no_match")
#     client = ssh_client
#     remote_path = f"/tmp/replace_block_nomatch_{int(time.time())}.txt"
#     original_content = "Some existing content.\nAnother line.\n"
#     old_block = "--- Nonexistent Block ---"
#     new_block = "--- Should Not Appear ---"
#
#     try:
#         create_remote_test_file(client, remote_path, original_content)
#         client.replace_block(remote_path, old_block, new_block)
#         actual_content = read_remote_file(client, remote_path)
#         assert actual_content == original_content # Content should be unchanged
#         print("replace_block with no match successful (no change).")
#     finally:
#         cleanup_remote_file(client, remote_path)
#         print_test_footer()
#
#
#
# def test_replace_block_sudo(ssh_client):
#     """Tests replacing a block in a file requiring sudo."""
#     print_test_header("test_replace_block_sudo")
#     client = ssh_client
#     remote_path = f"/tmp/replace_block_sudo_{int(time.time())}.txt"
#     old_block = "<config>\n  <value>old</value>\n</config>"
#     new_block = "<config>\n  <value>new</value>\n  <added/>\n</config>"
#     original_content = f"# System Config\n{old_block}\n# End Config"
#     expected_content = f"# System Config\n{new_block}\n# End Config"
#
#     try:
#         # Create the file as root
#         create_remote_test_file(client, remote_path, original_content, sudo=True)
#         ls_handle = client.run(f"ls -l {shlex.quote(remote_path)}")
#         assert "root root" in "".join(ls_handle.tail()), f"File {remote_path} not owned by root"
#
#         # Perform replacement with sudo
#         client.replace_block(remote_path, old_block, new_block, sudo=True)
#
#         # Read back the file with sudo
#         actual_content = read_remote_file(client, remote_path, sudo=True)
#         # Normalize line endings and trim any extra newlines
#         normalized_actual = actual_content.replace('\r\n', '\n').rstrip('\n')
#         normalized_expected = expected_content.rstrip('\n')
#         assert normalized_actual == normalized_expected, f"Content mismatch:\nExpected: {repr(expected_content)}\nActual: {repr(actual_content)}"
#
#         # Verify ownership preserved
#         ls_handle_after = client.run(f"ls -l {shlex.quote(remote_path)}")
#         assert "root root" in "".join(ls_handle_after.tail()), f"File {remote_path} ownership changed after sudo replace"
#
#         print("replace_block with sudo successful.")
#     finally:
#         cleanup_remote_file(client, remote_path, sudo=True)
#         print_test_footer()
#
#
#
# def test_file_upload_download(ssh_client):
#     """Tests uploading and downloading a file."""
#     print("\n--- test_file_upload_download ---")
#     client = ssh_client
#     local_temp_file_obj = None # Use object to ensure closure
#     local_temp_path = None
#     local_download_path = None
#     remote_path = f'/tmp/ssh_client_test_{int(time.time())}.txt' # Unique remote filename
#
#     try:
#         # 1. Create a local temporary file
#         file_content = f"Test content {time.time()}\nLine 2 with Ümlauts\r\nWindows line ending.\n"
#         local_temp_file_obj = tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8')
#         local_temp_path = local_temp_file_obj.name
#         local_temp_file_obj.write(file_content)
#         local_temp_file_obj.close() # Close the file before upload/verification
#         print(f"Created local temp file: {local_temp_path} with content:\n{file_content!r}")
#
#         # 2. Upload the file
#         print(f"Uploading {local_temp_path} to {remote_path}")
#         client.put(local_temp_path, remote_path)
#         print("Upload complete.")
#
#         # 3. Verify upload using 'ls' and 'cat'
#         print(f"Verifying remote file existence with 'ls {remote_path}'")
#         ls_handle = client.run(f"ls -l {remote_path}")
#         assert ls_handle.exit_code == 0, f"'ls {remote_path}' failed"
#         print(f"Verifying remote file content with 'cat {remote_path}'")
#         cat_handle = client.run(f"cat {remote_path}")
#         assert cat_handle.exit_code == 0, f"'cat {remote_path}' failed"
#         remote_content_lines = cat_handle.tail(cat_handle.total_lines)
#         remote_content = "".join(remote_content_lines)
#         print(f"Remote content via cat:\n{remote_content!r}")
#
#         # Normalize line endings for comparison
#         normalized_original_content = file_content.replace('\r\n', '\n')
#         normalized_remote_content = remote_content.replace('\r\n', '\n')
#
#         # More aggressive normalization to handle line ending differences between Windows and Linux
#         # 1. Split into lines
#         original_lines = normalized_original_content.split('\n')
#         remote_lines = normalized_remote_content.split('\n')
#
#         # 2. Filter out empty lines that might have been added during transfer
#         # But keep empty lines that are meaningful in the original content
#         meaningful_original_lines = []
#         for i, line in enumerate(original_lines):
#             if line.strip() or (i > 0 and i < len(original_lines) - 1):  # Keep non-empty lines and meaningful empty lines
#                 meaningful_original_lines.append(line)
#
#         meaningful_remote_lines = []
#         for i, line in enumerate(remote_lines):
#             if line.strip() or (i > 0 and i < len(remote_lines) - 1 and i < len(original_lines) and not original_lines[i].strip()):
#                 meaningful_remote_lines.append(line)
#
#         # Compare the meaningful content
#         assert meaningful_original_lines == meaningful_remote_lines, \
#             f"Remote content (cat) mismatch after normalization.\nOriginal: {normalized_original_content!r}\nRemote: {normalized_remote_content!r}"
#         print("Remote content via cat matches original (after normalization).")
#
#         # 4. Download the file
#         local_download_path = local_temp_path + ".downloaded"
#         print(f"Downloading {remote_path} to {local_download_path}")
#         client.get(remote_path, local_download_path)
#         print("Download complete.")
#
#         # 5. Verify downloaded file content
#         print(f"Reading downloaded file: {local_download_path}")
#         with open(local_download_path, 'r', encoding='utf-8') as f:
#             downloaded_content = f.read()
#         print(f"Downloaded content:\n{downloaded_content!r}")
#
#         # Normalize for comparison - apply the same normalization as for remote content
#         normalized_downloaded_content = downloaded_content.replace('\r\n', '\n')
#
#         # Apply the same normalization to downloaded content as we did for remote content
#         downloaded_lines = normalized_downloaded_content.split('\n')
#         meaningful_downloaded_lines = []
#         for i, line in enumerate(downloaded_lines):
#             if line.strip() or (i > 0 and i < len(downloaded_lines) - 1 and i < len(original_lines) and not original_lines[i].strip()):
#                 meaningful_downloaded_lines.append(line)
#
#         # Compare the meaningful content
#         assert meaningful_original_lines == meaningful_downloaded_lines, \
#             f"Downloaded content mismatch after normalization.\nOriginal: {normalized_original_content!r}\nDownloaded: {normalized_downloaded_content!r}"
#         print("Downloaded content matches original (after normalization).")
#
#         print("Assertions passed.")
#
#     finally:
#         # Cleanup
#         if client: # Check if client was successfully created
#             try:
#                 print(f"Cleaning up remote file: {remote_path}")
#                 # Use short timeout for cleanup command
#                 client.run(f"rm -f {shlex.quote(remote_path)}", io_timeout=5, runtime_timeout=10)
#             except Exception as cleanup_err:
#                 print(f"Warning: Failed to cleanup remote file {remote_path}: {cleanup_err}")
#         # Use local_temp_path for existence check and unlink
#         if local_temp_path and os.path.exists(local_temp_path):
#             print(f"Cleaning up local temp file: {local_temp_path}")
#             os.unlink(local_temp_path)
#         if local_download_path and os.path.exists(local_download_path):
#             print(f"Cleaning up downloaded file: {local_download_path}")
#             os.unlink(local_download_path)
#
#
#
# # if __name__ == "__main__":
# #     print("Running file editing tests...")
# #     test_file_upload_download(get_client(force_new=True))
# #     test_replace_line_simple(get_client(force_new=True))
# #     test_replace_line_multiple(get_client(force_new=True))
# #     test_replace_line_no_match(get_client(force_new=True))
# #     test_replace_line_sudo(get_client(force_new=True, sudo_password=TEST_SUDO_PASSWORD))
# #     test_replace_block_simple(get_client(force_new=True))
# #     test_replace_block_no_match(get_client(force_new=True))
# #     test_replace_block_sudo(get_client(force_new=True, sudo_password=TEST_SUDO_PASSWORD))
# #
# #     print("All file editing tests completed.")
