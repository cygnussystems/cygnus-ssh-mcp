import pytest
from pathlib import Path
import toml
import logging
from ssh_host_manager import SshHostManager
from ssh_models import SshError

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
    home_config = mock_home / ".mcp_ssh_hosts.toml"
    home_config.touch()
    manager = SshHostManager()
    assert manager.config_path == home_config

    # 3. Test project directory config
    home_config.unlink()
    project_config = Path("mcp_ssh_hosts.toml")
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
    
    ["another@invalid@section"]  # Valid TOML key but missing required fields
    password = "badformat"
    """
    temp_config_path.write_text(bad_content)
    
    manager = SshHostManager(config_path=temp_config_path)
    hosts = manager.hosts  # Use already loaded hosts from initialization
    
    # Verify malformed entries are skipped
    assert len(hosts) == 0
    # Verify warnings were logged
    # Should have one error for invalid_section missing port
    # and one error for the quoted but incomplete section
    assert caplog.text.count("Skipping malformed configuration") == 2
    assert "Invalid group name" not in caplog.text



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


def test_comment_preservation(temp_config_path):
    """Test that comments in TOML file are preserved when adding hosts"""
    # Create a TOML file with comments
    initial_content = """# My SSH hosts configuration
# This comment should be preserved

["existing@host.com"]
password = "existingpass"
port = 22
# This is a comment about the existing host
"""
    temp_config_path.write_text(initial_content)

    # Load manager and add a new host
    manager = SshHostManager(config_path=temp_config_path)
    manager.add_host("newuser", "newhost.com", 2222, "newpass")

    # Read the file content after save
    with open(temp_config_path, 'r') as f:
        saved_content = f.read()

    # Verify comments are preserved
    assert "# My SSH hosts configuration" in saved_content
    assert "# This comment should be preserved" in saved_content

    # Verify both hosts exist
    assert "existing@host.com" in saved_content
    assert "newuser@newhost.com" in saved_content

    # Verify the new host was added correctly
    loaded_hosts = manager._load_hosts()
    assert "newuser@newhost.com" in loaded_hosts
    assert loaded_hosts["newuser@newhost.com"]["password"] == "newpass"
    assert loaded_hosts["newuser@newhost.com"]["port"] == 2222


def test_add_host_with_alias_and_description(temp_config_path):
    """Test adding a host with alias and description"""
    manager = SshHostManager(config_path=temp_config_path)

    # Add host with alias and description
    manager.add_host(
        user="deploy",
        host="production.example.com",
        port=22,
        password="secret",
        alias="prod",
        description="Production web server"
    )

    # Verify in memory
    key = "deploy@production.example.com"
    assert key in manager.hosts
    assert manager.hosts[key]['alias'] == "prod"
    assert manager.hosts[key]['description'] == "Production web server"

    # Verify in TOML file
    with open(temp_config_path, 'r') as f:
        toml_data = toml.load(f)
        assert toml_data[key]['alias'] == "prod"
        assert toml_data[key]['description'] == "Production web server"

    # Verify reload
    loaded_hosts = manager._load_hosts()
    assert loaded_hosts[key]['alias'] == "prod"
    assert loaded_hosts[key]['description'] == "Production web server"


def test_get_host_by_alias(temp_config_path):
    """Test looking up a host by its alias"""
    manager = SshHostManager(config_path=temp_config_path)

    # Add host with alias
    manager.add_host("admin", "server1.local", 22, "pass1", alias="s1")
    manager.add_host("admin", "server2.local", 22, "pass2", alias="s2")

    # Look up by alias
    key, host = manager.get_host_by_alias("s1")
    assert key == "admin@server1.local"
    assert host['parsed_host'] == "server1.local"

    key2, host2 = manager.get_host_by_alias("s2")
    assert key2 == "admin@server2.local"

    # Non-existent alias should return None
    key_none, host_none = manager.get_host_by_alias("nonexistent")
    assert key_none is None
    assert host_none is None


def test_duplicate_alias_detection(temp_config_path):
    """Test that duplicate aliases are detected and raise error"""
    manager = SshHostManager(config_path=temp_config_path)

    # Add two hosts with the same alias directly to config file
    content = """
["user1@host1.com"]
password = "pass1"
port = 22
alias = "myalias"

["user2@host2.com"]
password = "pass2"
port = 22
alias = "myalias"
"""
    temp_config_path.write_text(content)
    manager.hosts = manager._load_hosts()

    # Looking up the duplicate alias should raise an error
    with pytest.raises(SshError) as exc_info:
        manager.get_host_by_alias("myalias")

    assert "Duplicate alias" in str(exc_info.value)
    assert "myalias" in str(exc_info.value)


def test_resolve_host_by_key_or_alias(temp_config_path):
    """Test resolving a host by either key or alias"""
    manager = SshHostManager(config_path=temp_config_path)

    # Add host with alias
    manager.add_host("deploy", "prod.example.com", 22, "secret", alias="production")

    # Resolve by key
    key1, host1 = manager.resolve_host("deploy@prod.example.com")
    assert key1 == "deploy@prod.example.com"
    assert host1['parsed_host'] == "prod.example.com"

    # Resolve by alias
    key2, host2 = manager.resolve_host("production")
    assert key2 == "deploy@prod.example.com"
    assert host2['alias'] == "production"

    # Non-existent should raise error
    with pytest.raises(SshError) as exc_info:
        manager.resolve_host("nonexistent")

    assert "not found" in str(exc_info.value)


def test_remove_host(temp_config_path):
    """Test removing a host"""
    manager = SshHostManager(config_path=temp_config_path)

    # Add two hosts
    manager.add_host("user1", "host1", 22, "pass1", alias="h1")
    manager.add_host("user2", "host2", 22, "pass2", alias="h2")

    assert "user1@host1" in manager.hosts
    assert "user2@host2" in manager.hosts

    # Remove one host
    result = manager.remove_host("user1@host1")
    assert result is True
    assert "user1@host1" not in manager.hosts
    assert "user2@host2" in manager.hosts

    # Verify removal persisted
    loaded_hosts = manager._load_hosts()
    assert "user1@host1" not in loaded_hosts
    assert "user2@host2" in loaded_hosts

    # Removing non-existent host should return False
    result = manager.remove_host("nonexistent@host")
    assert result is False
