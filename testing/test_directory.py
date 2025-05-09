import os
import time
import shlex
from test_utils import print_test_header, print_test_footer, TEST_SUDO_PASSWORD
from ssh_client import SshClient


# --- Test Functions ---

def test_mkdir_rmdir(ssh_client):
    """Test creating and removing directories."""
    print_test_header("test_mkdir_rmdir")
    client = ssh_client
    test_dir = f"/tmp/test_dir_{int(time.time())}"
    
    try:
        # Create directory
        client.mkdir(test_dir)
        assert test_dir in client.listdir("/tmp")
        
        # Remove directory
        client.rmdir(test_dir)
        assert test_dir not in client.listdir("/tmp")
        
        print("Basic directory create/remove successful.")
    finally:
        # Cleanup in case test failed
        try:
            client.run(f"rm -rf {shlex.quote(test_dir)}", sudo=False)
        except Exception:
            pass
        print_test_footer()

def test_mkdir_sudo(ssh_client):
    """Test creating directories with sudo."""
    print_test_header("test_mkdir_sudo")
    client = ssh_client
    test_dir = f"/root/test_dir_{int(time.time())}"
    
    try:
        # Create directory as root
        client.mkdir(test_dir, sudo=True)
        
        # Verify directory exists
        assert "test_dir_" in client.run(f"ls /root", sudo=True).tail()[0]
        
        # Cleanup
        client.rmdir(test_dir, sudo=True)
        print("Sudo directory operations successful.")
    finally:
        # Cleanup in case test failed
        try:
            client.run(f"rm -rf {shlex.quote(test_dir)}", sudo=True)
        except Exception:
            pass
        print_test_footer()

def test_listdir_stat(ssh_client):
    """Test listing directories and getting stats."""
    print_test_header("test_listdir_stat")
    client = ssh_client
    test_dir = f"/tmp/test_dir_{int(time.time())}"
    test_file = f"{test_dir}/test.txt"
    
    try:
        client.mkdir(test_dir)
        client.run(f"echo 'test' > {shlex.quote(test_file)}")
        
        # Test listdir
        contents = client.listdir(test_dir)
        assert "test.txt" in contents
        
        # Test stat
        stat = client.stat(test_file)
        assert stat.st_size > 0
        
        print("Directory listing and stats successful.")
    finally:
        # Cleanup
        client.run(f"rm -rf {shlex.quote(test_dir)}", sudo=False)
        print_test_footer()

if __name__ == "__main__":
    print("Running directory tests...")
    test_mkdir_rmdir(get_client(force_new=True))
    test_mkdir_sudo(get_client(force_new=True, sudo_password=TEST_SUDO_PASSWORD))
    test_listdir_stat(get_client(force_new=True))
    print("All directory tests completed.")
