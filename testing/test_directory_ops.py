import os
import time
import tempfile
import pytest
import shlex
from test_utils import get_client, cleanup_client, print_test_header, print_test_footer, SSH_USER, TEST_SUDO_PASSWORD
from ssh_client import (
    SshClient, CommandFailed, BusyError, TaskNotFound, SshError, SudoRequired
)

# --- Test Functions ---

def test_search_files_recursive(ssh_client):
    """Tests recursive file search functionality."""
    print_test_header("test_search_files_recursive")
    client = ssh_client
    
    # Create test directory structure
    test_dir = f"/tmp/search_test_{int(time.time())}"
    try:
        # Create directory structure
        client.run(f"mkdir -p {test_dir}/dir1/subdir {test_dir}/dir2")
        client.run(f"touch {test_dir}/file1.txt {test_dir}/dir1/file2.txt {test_dir}/dir1/subdir/file3.txt {test_dir}/dir2/file4.log")
        
        # Test basic search
        results = client.search_files_recursive(test_dir, "*.txt")
        print(f"Found {len(results)} .txt files")
        assert len(results) == 3, f"Expected 3 .txt files, found {len(results)}"
        
        # Test with depth limit
        results = client.search_files_recursive(test_dir, "*.txt", max_depth=1)
        print(f"Found {len(results)} .txt files with max_depth=1")
        assert len(results) == 1, f"Expected 1 .txt file with max_depth=1, found {len(results)}"
        
        # Test including directories
        results = client.search_files_recursive(test_dir, "*dir*", include_dirs=True)
        print(f"Found {len(results)} items matching *dir* with include_dirs=True")
        dirs = [item for item in results if item['type'] == 'directory']
        assert len(dirs) > 0, "Expected to find directories but found none"
        
        print("search_files_recursive tests passed")
    finally:
        # Cleanup
        client.run(f"rm -rf {test_dir}", io_timeout=10)
        print_test_footer()

def test_calculate_directory_size(ssh_client):
    """Tests directory size calculation."""
    print_test_header("test_calculate_directory_size")
    client = ssh_client
    
    # Create test directory with files of known size
    test_dir = f"/tmp/size_test_{int(time.time())}"
    try:
        # Create directory with files
        client.run(f"mkdir -p {test_dir}")
        client.run(f"dd if=/dev/zero of={test_dir}/file1 bs=1M count=1", io_timeout=30)
        client.run(f"dd if=/dev/zero of={test_dir}/file2 bs=512K count=1", io_timeout=30)
        
        # Calculate expected size (1MB + 512KB = 1536KB)
        expected_size = 1024 * 1024 + 512 * 1024
        
        # Test size calculation
        size = client.calculate_directory_size(test_dir)
        print(f"Directory size: {size} bytes")
        
        # Allow for some overhead in directory entries
        assert abs(size - expected_size) < 1024, f"Expected ~{expected_size} bytes, got {size}"
        
        print("calculate_directory_size test passed")
    finally:
        # Cleanup
        client.run(f"rm -rf {test_dir}", io_timeout=10)
        print_test_footer()

def test_delete_directory_recursive(ssh_client):
    """Tests recursive directory deletion with dry run."""
    print_test_header("test_delete_directory_recursive")
    client = ssh_client
    
    # Create test directory structure
    test_dir = f"/tmp/delete_test_{int(time.time())}"
    try:
        # Create directory structure
        client.run(f"mkdir -p {test_dir}/dir1/subdir {test_dir}/dir2")
        client.run(f"touch {test_dir}/file1.txt {test_dir}/dir1/file2.txt {test_dir}/dir2/file3.txt")
        
        # Test dry run
        result = client.delete_directory_recursive(test_dir, dry_run=True)
        print(f"Dry run result: {result['status']}, items: {len(result['deleted_items'])}")
        assert result['status'] == 'success', "Expected success status for dry run"
        assert 'dry_run' in result and result['dry_run'], "Expected dry_run flag in result"
        assert len(result['deleted_items']) > 0, "Expected items to be listed for deletion"
        
        # Verify files still exist
        exists_check = client.run(f"[ -d {test_dir} ] && echo 'exists' || echo 'gone'")
        assert 'exists' in exists_check.tail(1)[0], "Directory should still exist after dry run"
        
        # Test actual deletion
        result = client.delete_directory_recursive(test_dir, dry_run=False)
        print(f"Actual deletion result: {result['status']}, items: {len(result['deleted_items'])}")
        assert result['status'] == 'success', "Expected success status for actual deletion"
        
        # Verify directory is gone
        exists_check = client.run(f"[ -d {test_dir} ] && echo 'exists' || echo 'gone'")
        assert 'gone' in exists_check.tail(1)[0], "Directory should be gone after deletion"
        
        print("delete_directory_recursive test passed")
    finally:
        # Cleanup (just in case)
        client.run(f"rm -rf {test_dir}", io_timeout=10)
        print_test_footer()

def test_batch_delete_by_pattern(ssh_client):
    """Tests batch deletion by pattern."""
    print_test_header("test_batch_delete_by_pattern")
    client = ssh_client
    
    # Create test directory with various file types
    test_dir = f"/tmp/batch_delete_test_{int(time.time())}"
    try:
        # Create directory with files
        client.run(f"mkdir -p {test_dir}")
        client.run(f"touch {test_dir}/file1.tmp {test_dir}/file2.tmp {test_dir}/file3.txt {test_dir}/file4.log")
        
        # Test dry run for .tmp files
        result = client.batch_delete_by_pattern(test_dir, "*.tmp", dry_run=True)
        print(f"Dry run result: {result['status']}, files: {len(result['deleted_files'])}")
        assert result['status'] == 'success', "Expected success status for dry run"
        assert len(result['deleted_files']) == 2, f"Expected 2 .tmp files, found {len(result['deleted_files'])}"
        
        # Verify files still exist
        count_check = client.run(f"find {test_dir} -name '*.tmp' | wc -l")
        assert '2' in count_check.tail(1)[0], "Should still have 2 .tmp files after dry run"
        
        # Test actual deletion
        result = client.batch_delete_by_pattern(test_dir, "*.tmp", dry_run=False)
        print(f"Actual deletion result: {result['status']}, files: {len(result['deleted_files'])}")
        assert result['status'] == 'success', "Expected success status for actual deletion"
        
        # Verify .tmp files are gone but others remain
        tmp_check = client.run(f"find {test_dir} -name '*.tmp' | wc -l")
        assert '0' in tmp_check.tail(1)[0], "Should have 0 .tmp files after deletion"
        
        other_check = client.run(f"find {test_dir} -type f | wc -l")
        assert '2' in other_check.tail(1)[0], "Should still have 2 other files"
        
        print("batch_delete_by_pattern test passed")
    finally:
        # Cleanup
        client.run(f"rm -rf {test_dir}", io_timeout=10)
        print_test_footer()

def test_list_directory_recursive(ssh_client):
    """Tests recursive directory listing with metadata."""
    print_test_header("test_list_directory_recursive")
    client = ssh_client
    
    # Create test directory structure
    test_dir = f"/tmp/list_test_{int(time.time())}"
    try:
        # Create directory structure with different permissions
        client.run(f"mkdir -p {test_dir}/dir1 {test_dir}/dir2")
        client.run(f"touch {test_dir}/file1.txt {test_dir}/dir1/file2.txt")
        client.run(f"chmod 700 {test_dir}/dir1")
        client.run(f"chmod 644 {test_dir}/file1.txt")
        
        # Test listing
        results = client.list_directory_recursive(test_dir)
        print(f"Found {len(results)} items in directory listing")
        
        # Verify we have both files and directories
        files = [item for item in results if item['type'] == 'file']
        dirs = [item for item in results if item['type'] == 'directory']
        
        assert len(files) >= 2, f"Expected at least 2 files, found {len(files)}"
        assert len(dirs) >= 2, f"Expected at least 2 directories, found {len(dirs)}"
        
        # Check metadata fields
        for item in results:
            assert 'path' in item, "Missing 'path' in result"
            assert 'type' in item, "Missing 'type' in result"
            assert 'size_bytes' in item, "Missing 'size_bytes' in result"
            assert 'modified_time' in item, "Missing 'modified_time' in result"
            assert 'permissions' in item, "Missing 'permissions' in result"
            assert 'user' in item, "Missing 'user' in result"
            assert 'group' in item, "Missing 'group' in result"
        
        # Test with depth limit
        limited_results = client.list_directory_recursive(test_dir, max_depth=1)
        print(f"Found {len(limited_results)} items with max_depth=1")
        assert len(limited_results) < len(results), "Depth-limited results should be fewer than full results"
        
        print("list_directory_recursive test passed")
    finally:
        # Cleanup
        client.run(f"rm -rf {test_dir}", io_timeout=10)
        print_test_footer()

def test_copy_directory_recursive(ssh_client):
    """Tests recursive directory copying."""
    print_test_header("test_copy_directory_recursive")
    client = ssh_client
    
    # Create test directories
    source_dir = f"/tmp/copy_source_{int(time.time())}"
    dest_dir = f"/tmp/copy_dest_{int(time.time())}"
    
    try:
        # Create source directory structure
        client.run(f"mkdir -p {source_dir}/dir1/subdir {source_dir}/dir2")
        client.run(f"echo 'test1' > {source_dir}/file1.txt")
        client.run(f"echo 'test2' > {source_dir}/dir1/file2.txt")
        client.run(f"ln -s {source_dir}/file1.txt {source_dir}/link1")
        
        # Test copying
        result = client.copy_directory_recursive(source_dir, dest_dir)
        print(f"Copy result: {result['status']}, files copied: {result['files_copied']}")
        
        assert result['status'] == 'success', f"Expected success status, got {result['status']}"
        assert result['files_copied'] > 0, "Expected files to be copied"
        
        # Verify destination structure
        dir_check = client.run(f"find {dest_dir} -type d | wc -l")
        file_check = client.run(f"find {dest_dir} -type f | wc -l")
        link_check = client.run(f"find {dest_dir} -type l | wc -l")
        
        assert int(dir_check.tail(1)[0].strip()) >= 3, "Expected at least 3 directories in destination"
        assert int(file_check.tail(1)[0].strip()) >= 2, "Expected at least 2 files in destination"
        assert int(link_check.tail(1)[0].strip()) >= 1, "Expected at least 1 symlink in destination"
        
        # Test content preservation
        content_check = client.run(f"cat {dest_dir}/file1.txt")
        assert 'test1' in content_check.tail(1)[0], "File content should be preserved"
        
        print("copy_directory_recursive test passed")
    finally:
        # Cleanup
        client.run(f"rm -rf {source_dir} {dest_dir}", io_timeout=10)
        print_test_footer()

def test_create_and_extract_archive(ssh_client):
    """Tests creating and extracting archives."""
    print_test_header("test_create_and_extract_archive")
    client = ssh_client
    
    # Create test directories
    source_dir = f"/tmp/archive_source_{int(time.time())}"
    extract_dir = f"/tmp/archive_extract_{int(time.time())}"
    archive_path = f"/tmp/archive_test_{int(time.time())}.tar.gz"
    
    try:
        # Create source directory with files
        client.run(f"mkdir -p {source_dir}/dir1")
        client.run(f"echo 'test1' > {source_dir}/file1.txt")
        client.run(f"echo 'test2' > {source_dir}/dir1/file2.txt")
        
        # Test creating archive
        result = client.create_archive_from_directory(source_dir, archive_path)
        print(f"Archive creation result: {result['status']}, size: {result.get('size_bytes', 'N/A')} bytes")
        
        assert result['status'] == 'success', f"Expected success status, got {result['status']}"
        assert result['archive_created'] == archive_path, "Archive path mismatch"
        assert result['size_bytes'] > 0, "Archive should have positive size"
        
        # Verify archive exists
        exists_check = client.run(f"[ -f {archive_path} ] && echo 'exists' || echo 'missing'")
        assert 'exists' in exists_check.tail(1)[0], "Archive file should exist"
        
        # Test extracting archive
        client.run(f"mkdir -p {extract_dir}")
        extract_result = client.extract_archive_to_directory(archive_path, extract_dir)
        print(f"Extract result: {extract_result['status']}, files: {len(extract_result.get('extracted_files', []))}")
        
        assert extract_result['status'] == 'success', f"Expected success status, got {extract_result['status']}"
        assert len(extract_result['extracted_files']) > 0, "Should have extracted files"
        
        # Verify extracted content
        content_check = client.run(f"cat {extract_dir}/file1.txt")
        assert 'test1' in content_check.tail(1)[0], "File content should be preserved in extraction"
        
        dir_check = client.run(f"[ -d {extract_dir}/dir1 ] && echo 'exists' || echo 'missing'")
        assert 'exists' in dir_check.tail(1)[0], "Subdirectory should be preserved in extraction"
        
        print("create_and_extract_archive test passed")
    finally:
        # Cleanup
        client.run(f"rm -rf {source_dir} {extract_dir} {archive_path}", io_timeout=10)
        print_test_footer()

def test_search_file_contents(ssh_client):
    """Tests searching for content in files."""
    print_test_header("test_search_file_contents")
    client = ssh_client
    
    # Create test directory with files containing specific content
    test_dir = f"/tmp/search_content_test_{int(time.time())}"
    try:
        # Create directory with files
        client.run(f"mkdir -p {test_dir}/dir1")
        client.run(f"echo 'This is a test file with PATTERN1' > {test_dir}/file1.txt")
        client.run(f"echo 'This file has pattern2 in it' > {test_dir}/file2.txt")
        client.run(f"echo 'This file has both PATTERN1 and pattern2' > {test_dir}/dir1/file3.txt")
        
        # Test basic search
        results = client.search_file_contents(test_dir, "PATTERN1")
        print(f"Found {len(results)} matches for 'PATTERN1' (case-sensitive)")
        assert len(results) == 2, f"Expected 2 matches for PATTERN1, found {len(results)}"
        
        # Test case-insensitive search
        results = client.search_file_contents(test_dir, "pattern1", case_sensitive=False)
        print(f"Found {len(results)} matches for 'pattern1' (case-insensitive)")
        assert len(results) == 2, f"Expected 2 matches for pattern1 case-insensitive, found {len(results)}"
        
        # Test regex search
        results = client.search_file_contents(test_dir, "pattern[12]", regex=True, case_sensitive=False)
        print(f"Found {len(results)} matches for regex 'pattern[12]'")
        assert len(results) == 3, f"Expected 3 matches for regex pattern[12], found {len(results)}"
        
        # Check result structure
        for item in results:
            assert 'file' in item, "Missing 'file' in result"
            assert 'line' in item, "Missing 'line' in result"
            assert 'content' in item, "Missing 'content' in result"
        
        print("search_file_contents test passed")
    finally:
        # Cleanup
        client.run(f"rm -rf {test_dir}", io_timeout=10)
        print_test_footer()

def test_safe_move_or_rename(ssh_client):
    """Tests safe move/rename operation."""
    print_test_header("test_safe_move_or_rename")
    client = ssh_client
    
    # Create test files
    source_file = f"/tmp/move_source_{int(time.time())}.txt"
    dest_file = f"/tmp/move_dest_{int(time.time())}.txt"
    
    try:
        # Create source file
        client.run(f"echo 'test content' > {source_file}")
        
        # Test basic move
        result = client.safe_move_or_rename(source_file, dest_file)
        print(f"Move result: {result['status']}, message: {result.get('message', 'N/A')}")
        
        assert result['status'] == 'success', f"Expected success status, got {result['status']}"
        
        # Verify source is gone and destination exists
        source_check = client.run(f"[ -f {source_file} ] && echo 'exists' || echo 'gone'")
        dest_check = client.run(f"[ -f {dest_file} ] && echo 'exists' || echo 'gone'")
        
        assert 'gone' in source_check.tail(1)[0], "Source file should be gone after move"
        assert 'exists' in dest_check.tail(1)[0], "Destination file should exist after move"
        
        # Test overwrite protection
        client.run(f"echo 'original content' > {source_file}")
        
        # Try to move without overwrite
        result = client.safe_move_or_rename(source_file, dest_file, overwrite=False)
        print(f"Move without overwrite result: {result['status']}, message: {result.get('message', 'N/A')}")
        
        assert result['status'] == 'error', "Expected error status when overwrite=False and destination exists"
        
        # Try to move with overwrite
        result = client.safe_move_or_rename(source_file, dest_file, overwrite=True)
        print(f"Move with overwrite result: {result['status']}, message: {result.get('message', 'N/A')}")
        
        assert result['status'] == 'success', "Expected success status when overwrite=True"
        
        # Verify content was overwritten
        content_check = client.run(f"cat {dest_file}")
        assert 'original content' in content_check.tail(1)[0], "File content should be overwritten"
        
        print("safe_move_or_rename test passed")
    finally:
        # Cleanup
        client.run(f"rm -f {source_file} {dest_file}", io_timeout=10)
        print_test_footer()

if __name__ == "__main__":
    print("Running directory operations tests...")
    client = get_client(force_new=True)
    
    try:
        test_search_files_recursive(client)
        test_calculate_directory_size(client)
        test_delete_directory_recursive(client)
        test_batch_delete_by_pattern(client)
        test_list_directory_recursive(client)
        test_copy_directory_recursive(client)
        test_create_and_extract_archive(client)
        test_search_file_contents(client)
        test_safe_move_or_rename(client)
        
        print("All directory operations tests completed successfully.")
    finally:
        cleanup_client(client)
