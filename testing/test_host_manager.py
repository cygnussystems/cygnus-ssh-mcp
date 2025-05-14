import pytest
from pathlib import Path
import toml
import logging
from ssh_host_manager import SshHostManager

# Configure logging for tests
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("SshHostManagerTests")

@pytest.fixture
def temp_config_path(tmp_path):
    """Fixture that provides a temporary config file path"""
    config_path = tmp_path / "test_hosts.toml"
    yield config_path
    # Cleanup temporary file after test
    if config_path.exists():
        config_path.unlink()


def test_initialization_with_temp_path(temp_config_path):
    """Test initialization with a temporary config path"""
    # Should create empty config file if none exists
    manager = SshHostManager(config_path=temp_config_path)
    
    assert manager.config_path == temp_config_path
    assert temp_config_path.exists()
    
    # Verify file has header comments
    with open(temp_config_path, 'r') as f:
        content = f.read()
        assert "# SSH Host Configurations (TOML format)" in content



def test_add_and_load_host(temp_config_path):
    """Test adding a host and loading it back"""
    manager = SshHostManager(config_path=temp_config_path)
    
    # Add test host
    test_user = "testuser"
    test_host = "example.com"
    test_port = 2222
    test_pass = "testpass123"
    manager.add_host(test_user, test_host, test_port, test_pass)
    
    # Load hosts
    loaded_hosts = manager._load_hosts()
    key = f"{test_user}@{test_host}"
    
    assert key in loaded_hosts
    assert loaded_hosts[key]['password'] == test_pass
    assert loaded_hosts[key]['port'] == test_port
    
    # Verify the saved TOML file
    with open(temp_config_path, 'r') as f:
        toml_data = toml.load(f)
        assert key in toml_data
        assert toml_data[key]['password'] == test_pass
        assert toml_data[key]['port'] == test_port


def test_config_path_priority(tmp_path, monkeypatch):
    """Test config path resolution priority"""
    # Setup mock home directory
    mock_home = tmp_path / "home"
    mock_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: mock_home)
    
    # 1. Test explicit path
    explicit_path = tmp_path / "explicit.toml"
    manager = SshHostManager(config_path=explicit_path)
    assert manager.config_path == explicit_path
    
    # 2. Test home directory config
    home_config = mock_home / ".ssh_hosts.toml"
    home_config.touch()
    manager = SshHostManager()
    assert manager.config_path == home_config
    
    # 3. Test project directory config
    home_config.unlink()
    project_config = Path("ssh_hosts.toml")
    project_config.touch()
    manager = SshHostManager()
    assert manager.config_path == project_config.resolve()
    project_config.unlink()



def test_malformed_config_handling(temp_config_path, caplog):
    """Test handling of malformed TOML entries"""
    # Create invalid TOML content
    bad_content = """
    [invalid_section]
    missing_port = "justpassword"
    
    [another@invalid@section]
    password = "badformat"
    """
    temp_config_path.write_text(bad_content)
    
    manager = SshHostManager(config_path=temp_config_path)
    hosts = manager._load_hosts()
    
    # Verify malformed entries are skipped
    assert len(hosts) == 0
    # Verify warnings were logged
    assert "Skipping malformed configuration" in caplog.text
    assert "Skipping malformed host key" in caplog.text



def test_duplicate_host_handling(temp_config_path):
    """Test updating existing host configuration"""
    manager = SshHostManager(config_path=temp_config_path)
    
    # Add initial host
    manager.add_host("user1", "host1", 22, "pass1")
    # Update with new credentials
    manager.add_host("user1", "host1", 2222, "newpass")
    
    loaded_hosts = manager._load_hosts()
    key = "user1@host1"
    
    assert key in loaded_hosts
    assert loaded_hosts[key]['password'] == "newpass"
    assert loaded_hosts[key]['port'] == 2222



def test_invalid_port_handling(temp_config_path, caplog):
    """Test handling of invalid port numbers"""
    manager = SshHostManager(config_path=temp_config_path)
    
    # Add host with invalid port
    manager.add_host("baduser", "badhost", 70000, "badpass")
    
    # Verify port is clamped to valid range
    loaded_hosts = manager._load_hosts()
    entry = loaded_hosts["baduser@badhost"]
    assert 1 <= entry['port'] <= 65535
    assert "Invalid port number" in caplog.text


def test_special_characters_handling(temp_config_path):
    """Test handling of special characters in passwords"""
    manager = SshHostManager(config_path=temp_config_path)
    
    special_pass = 'p@ssw0rd! #with$pecia1_chars'
    manager.add_host("special", "host", 22, special_pass)
    
    loaded_hosts = manager._load_hosts()
    assert loaded_hosts["special@host"]['password'] == special_pass
    
    # Verify TOML file handles special characters
    with open(temp_config_path, 'r') as f:
        toml_data = toml.load(f)
        assert toml_data["special@host"]["password"] == special_pass
