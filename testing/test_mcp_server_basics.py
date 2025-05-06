import pytest
import os
import yaml
from pathlib import Path
from mcp_ssh_server import SshHostManager, parse_args
from ssh_models import SshError

@pytest.fixture
def temp_config(tmp_path):
    """Fixture that creates a temporary SSH config file."""
    config_path = tmp_path / "ssh_hosts.yaml"
    with open(config_path, 'w') as f:
        yaml.safe_dump({'hosts': []}, f)
    return config_path

def test_parse_args_default():
    """Test that parse_args() returns default config path."""
    args = parse_args([])
    assert args.config is None

def test_parse_args_with_config():
    """Test that parse_args() handles explicit config path."""
    args = parse_args(['--config', '/path/to/config.yaml'])
    assert args.config == '/path/to/config.yaml'

def test_ssh_host_manager_default_config():
    """Test that SshHostManager finds default config files."""
    # Test with no config files present
    with pytest.raises(SshError):
        manager = SshHostManager()
        
    # Create a home config file
    home_config = Path.home() / ".ssh_hosts.yaml"
    try:
        with open(home_config, 'w') as f:
            yaml.safe_dump({'hosts': []}, f)
            
        manager = SshHostManager()
        assert manager.config_path == home_config
    finally:
        if home_config.exists():
            home_config.unlink()

def test_ssh_host_manager_explicit_config(temp_config):
    """Test SshHostManager with explicit config path."""
    manager = SshHostManager(config_path=temp_config)
    assert manager.config_path == temp_config
    assert manager.hosts == {}

def test_ssh_host_manager_add_host(temp_config):
    """Test adding a host to the config."""
    manager = SshHostManager(config_path=temp_config)
    
    # Add a host
    manager.add_host('test1', 'host1', 22, 'user1', 'pass1')
    assert 'test1' in manager.hosts
    assert manager.hosts['test1']['host'] == 'host1'
    
    # Verify the file was updated
    with open(temp_config, 'r') as f:
        data = yaml.safe_load(f)
        assert len(data['hosts']) == 1
        assert data['hosts'][0]['name'] == 'test1'

def test_ssh_host_manager_get_host(temp_config):
    """Test retrieving host configurations."""
    manager = SshHostManager(config_path=temp_config)
    
    # Add two hosts
    manager.add_host('test1', 'host1', 22, 'user1', 'pass1')
    manager.add_host('test2', 'host2', 22, 'user2', 'pass2')
    
    # Test getting existing host
    host = manager.get_host('test1')
    assert host['host'] == 'host1'
    
    # Test getting non-existent host
    assert manager.get_host('test3') is None

def test_ssh_host_manager_config_permissions(temp_config):
    """Test that config file permissions are set securely."""
    manager = SshHostManager(config_path=temp_config)
    
    # Verify permissions are 600
    mode = temp_config.stat().st_mode
    assert oct(mode)[-3:] == '600'

def test_ssh_host_manager_invalid_config(temp_config):
    """Test handling of invalid config files."""
    # Write invalid YAML
    with open(temp_config, 'w') as f:
        f.write("invalid: yaml: {")
    
    with pytest.raises(SshError):
        manager = SshHostManager(config_path=temp_config)

def test_ssh_host_manager_save_failure(temp_config):
    """Test handling of config save failures."""
    manager = SshHostManager(config_path=temp_config)
    
    # Make config file read-only
    temp_config.chmod(0o400)
    
    with pytest.raises(SshError):
        manager.add_host('test1', 'host1', 22, 'user1', 'pass1')
