"""
Tests for ssh_dir_transfer tool - directory upload and download operations.
Supports all platforms (Linux, macOS, Windows).
"""
import pytest
import json
import os
import tempfile
import shutil
from conftest import (
    print_test_header, print_test_footer, make_connection, disconnect_ssh,
    extract_result_text, TEST_WORKSPACE, PATH_SEP, IS_WINDOWS, cleanup_command
)
from cygnus_ssh_mcp.server import mcp
from fastmcp import Client


def cross_platform_basename(path: str) -> str:
    """Get basename from a path that may use Windows or Unix separators.

    os.path.basename doesn't work for Windows paths when running on Linux/macOS.
    This handles both separators correctly.
    """
    # Normalize to forward slashes, then get the last component
    return path.replace('\\', '/').rstrip('/').rsplit('/', 1)[-1]


@pytest.mark.asyncio
async def test_ssh_dir_transfer_upload(mcp_test_environment):
    """Test directory upload (local to remote)."""
    print_test_header("Testing 'ssh_dir_transfer' upload")

    async with Client(mcp) as client:
        local_dir = None
        remote_path = f"{TEST_WORKSPACE}{PATH_SEP}uploaded_dir"

        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            # Create a local directory with some test files
            local_dir = tempfile.mkdtemp(prefix='ssh_dir_transfer_test_')

            # Create some test files
            with open(os.path.join(local_dir, 'file1.txt'), 'w') as f:
                f.write('Content of file 1')
            with open(os.path.join(local_dir, 'file2.txt'), 'w') as f:
                f.write('Content of file 2')

            # Create a subdirectory with a file
            subdir = os.path.join(local_dir, 'subdir')
            os.makedirs(subdir)
            with open(os.path.join(subdir, 'nested_file.txt'), 'w') as f:
                f.write('Nested file content')

            # Test upload
            upload_result = await client.call_tool("ssh_dir_transfer", {
                "direction": "upload",
                "local_path": local_dir,
                "remote_path": remote_path
            })
            upload_json = json.loads(extract_result_text(upload_result))

            print(f"Upload result: {json.dumps(upload_json, indent=2)}")

            assert upload_json['success'], f"Upload failed: {upload_json.get('error')}"
            assert upload_json['operation'] == 'upload'
            assert upload_json['files_transferred'] > 0, "Expected files to be transferred"

            # Verify the directory exists on remote
            stat_result = await client.call_tool("ssh_file_stat", {"path": remote_path})
            stat_json = json.loads(extract_result_text(stat_result))
            assert stat_json.get('exists'), "Uploaded directory should exist on remote"
            assert stat_json.get('type') == 'directory', "Should be a directory"

        finally:
            # Cleanup local directory
            if local_dir and os.path.exists(local_dir):
                shutil.rmtree(local_dir)

            # Cleanup remote directory
            await client.call_tool("ssh_cmd_run", {
                "command": cleanup_command(remote_path),
                "io_timeout": 10.0
            })
            await disconnect_ssh(client)

    print_test_footer()


@pytest.mark.asyncio
async def test_ssh_dir_transfer_download(mcp_test_environment):
    """Test directory download (remote to local)."""
    print_test_header("Testing 'ssh_dir_transfer' download")

    async with Client(mcp) as client:
        local_download_dir = None
        remote_dir = f"{TEST_WORKSPACE}{PATH_SEP}dir_to_download"

        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            # Create a remote directory with files using ssh_dir_mkdir and ssh_file_write
            await client.call_tool("ssh_dir_mkdir", {"path": remote_dir})

            await client.call_tool("ssh_file_write", {
                "file_path": f"{remote_dir}{PATH_SEP}remote_file1.txt",
                "content": "Remote file 1 content"
            })
            await client.call_tool("ssh_file_write", {
                "file_path": f"{remote_dir}{PATH_SEP}remote_file2.txt",
                "content": "Remote file 2 content"
            })

            # Create local temp directory for download
            local_download_dir = tempfile.mkdtemp(prefix='ssh_dir_download_test_')

            # Test download
            download_result = await client.call_tool("ssh_dir_transfer", {
                "direction": "download",
                "local_path": local_download_dir,
                "remote_path": remote_dir
            })
            download_json = json.loads(extract_result_text(download_result))

            print(f"Download result: {json.dumps(download_json, indent=2)}")

            assert download_json['success'], f"Download failed: {download_json.get('error')}"
            assert download_json['operation'] == 'download'
            assert download_json['files_transferred'] > 0, "Expected files to be transferred"

            # Verify files were downloaded
            # The directory structure includes the base directory name
            # Use cross_platform_basename for paths that may be Windows-style
            base_name = cross_platform_basename(remote_dir)
            downloaded_dir = os.path.join(local_download_dir, base_name)

            assert os.path.isdir(downloaded_dir), f"Downloaded directory should exist: {downloaded_dir}"

            # Check for files
            files = os.listdir(downloaded_dir)
            assert len(files) >= 2, f"Expected at least 2 files, got {len(files)}: {files}"

        finally:
            # Cleanup local directory
            if local_download_dir and os.path.exists(local_download_dir):
                shutil.rmtree(local_download_dir)

            # Cleanup remote directory
            await client.call_tool("ssh_cmd_run", {
                "command": cleanup_command(remote_dir),
                "io_timeout": 10.0
            })
            await disconnect_ssh(client)

    print_test_footer()


@pytest.mark.asyncio
async def test_ssh_dir_transfer_roundtrip(mcp_test_environment):
    """Test upload followed by download - verify data integrity."""
    print_test_header("Testing 'ssh_dir_transfer' roundtrip")

    async with Client(mcp) as client:
        local_source = None
        local_dest = None
        remote_path = f"{TEST_WORKSPACE}{PATH_SEP}roundtrip_dir"

        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            # Create source directory with test content
            local_source = tempfile.mkdtemp(prefix='ssh_roundtrip_source_')
            test_content = "Test content for roundtrip verification - with special chars: aouAOU"
            with open(os.path.join(local_source, 'test_file.txt'), 'w', encoding='utf-8') as f:
                f.write(test_content)

            # Upload
            upload_result = await client.call_tool("ssh_dir_transfer", {
                "direction": "upload",
                "local_path": local_source,
                "remote_path": remote_path
            })
            upload_json = json.loads(extract_result_text(upload_result))
            assert upload_json['success'], f"Upload failed: {upload_json.get('error')}"

            print(f"Upload result: {json.dumps(upload_json, indent=2)}")

            # Verify the uploaded file exists on remote
            # Note: extraction uses --strip-components=1, so files go directly to remote_path/
            remote_file = f"{remote_path}{PATH_SEP}test_file.txt"
            stat_result = await client.call_tool("ssh_file_stat", {"path": remote_file})
            stat_json = json.loads(extract_result_text(stat_result))
            print(f"Remote file stat: {json.dumps(stat_json, indent=2)}")
            assert stat_json.get('exists'), f"Uploaded file should exist: {remote_file}"

            # Download the remote directory
            local_dest = tempfile.mkdtemp(prefix='ssh_roundtrip_dest_')

            download_result = await client.call_tool("ssh_dir_transfer", {
                "direction": "download",
                "local_path": local_dest,
                "remote_path": remote_path
            })
            download_json = json.loads(extract_result_text(download_result))
            print(f"Download result: {json.dumps(download_json, indent=2)}")
            assert download_json['success'], f"Download failed: {download_json.get('error')}"

            # Verify content matches
            # Downloaded structure: local_dest/roundtrip_dir/test_file.txt
            # Use cross_platform_basename for paths that may be Windows-style
            remote_base = cross_platform_basename(remote_path)
            downloaded_file = os.path.join(local_dest, remote_base, 'test_file.txt')

            # Debug: list what was downloaded
            print(f"Looking for: {downloaded_file}")
            print(f"Contents of {local_dest}: {os.listdir(local_dest)}")
            if os.path.exists(os.path.join(local_dest, remote_base)):
                print(f"Contents of {os.path.join(local_dest, remote_base)}: {os.listdir(os.path.join(local_dest, remote_base))}")

            assert os.path.exists(downloaded_file), f"Downloaded file should exist: {downloaded_file}"

            with open(downloaded_file, 'r', encoding='utf-8') as f:
                downloaded_content = f.read()

            assert downloaded_content == test_content, "Content should match after roundtrip"

        finally:
            # Cleanup
            if local_source and os.path.exists(local_source):
                shutil.rmtree(local_source)
            if local_dest and os.path.exists(local_dest):
                shutil.rmtree(local_dest)

            await client.call_tool("ssh_cmd_run", {
                "command": cleanup_command(remote_path),
                "io_timeout": 10.0
            })
            await disconnect_ssh(client)

    print_test_footer()


@pytest.mark.asyncio
async def test_ssh_dir_transfer_nonexistent_source(mcp_test_environment):
    """Test error handling when source directory doesn't exist."""
    print_test_header("Testing 'ssh_dir_transfer' with nonexistent source")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"

            # Try to upload a nonexistent directory
            result = await client.call_tool("ssh_dir_transfer", {
                "direction": "upload",
                "local_path": "/nonexistent/directory/path",
                "remote_path": f"{TEST_WORKSPACE}{PATH_SEP}should_not_exist"
            })
            result_json = json.loads(extract_result_text(result))

            print(f"Result for nonexistent source: {json.dumps(result_json, indent=2)}")

            assert not result_json['success'], "Should fail for nonexistent source"
            assert 'error' in result_json, "Should have error message"

        finally:
            await disconnect_ssh(client)

    print_test_footer()
