

import logging
import toml
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger("SSH_Host_Manager")

class SshHostManager:
    def __init__(self, config_path: Optional[Path] = None):
        # Try paths in this order:
        # 1. Explicit config_path if provided
        # 2. ~/.ssh_hosts.toml
        # 3. ./ssh_hosts.toml
        if config_path:
            self.config_path = config_path
        else:
            home_config = Path.home() / ".ssh_hosts.toml"
            project_config = Path.cwd() / "ssh_hosts.toml"  # Use absolute path

            # Prefer home config if exists, otherwise project config
            self.config_path = home_config if home_config.exists() else project_config

        self._ensure_config_file()
        self.hosts: Dict[str, Dict[str, Any]] = self._load_hosts()

    def _ensure_config_file(self):
        """Create config file if it doesn't exist with secure permissions."""
        if not self.config_path.exists():
            with open(self.config_path, 'w') as f:
                # Create an empty TOML file with a helpful comment
                f.write("# SSH Host Configurations (TOML format)\n")
                f.write("# Add your hosts using the [user@hostname] syntax, for example:\n")
                f.write("#\n")
                f.write("# [testuser@localhost]\n")
                f.write("# password = \"yourpassword\"\n")
                f.write("# port = 2222\n")
                f.write("#\n")
                f.write("# [anotheruser@example.com]\n")
                f.write("# password = \"anothersecret\"\n")
                f.write("# port = 22\n")
            self.config_path.chmod(0o600)  # rw-------

    def _load_hosts(self) -> Dict[str, Dict[str, Any]]:
        """Load hosts from TOML config file."""
        loaded_hosts: Dict[str, Dict[str, Any]] = {}
        try:
            with open(self.config_path, 'r') as f:
                data = toml.load(f)

            for key, config_details in data.items():
                if not isinstance(config_details, dict) or \
                        'password' not in config_details or \
                        'port' not in config_details:
                    logger.warning(f"Skipping malformed configuration for '{key}' in {self.config_path}. "
                                   "Each host must be a table with 'password' and 'port'.")
                    continue

                try:
                    user, host_address = key.rsplit('@', 1)
                    if not user or not host_address:  # Ensure neither part is empty
                        raise ValueError("User or host part is empty.")
                except ValueError:
                    logger.warning(f"Skipping malformed host key '{key}' in {self.config_path}. "
                                   "Key must be in 'user@hostname' format.")
                    continue

                loaded_hosts[key] = {
                    'password': str(config_details['password']),  # Ensure password is a string
                    'port': int(config_details['port']),  # Ensure port is an int
                    'parsed_user': user,
                    'parsed_host': host_address
                }
            return loaded_hosts
        except FileNotFoundError:
            logger.error(f"Configuration file not found: {self.config_path}")
            return {}  # Should be created by _ensure_config_file, but good to handle
        except toml.TomlDecodeError as e:
            logger.error(f"Failed to parse TOML configuration file {self.config_path}: {e}")
            return {}
        except Exception as e:
            logger.error(f"Failed to load SSH hosts from {self.config_path}: {e}")
            return {}

    def get_host(self, user_at_host_key: str) -> Optional[Dict[str, Any]]:
        """Get host config by 'user@hostname' key."""
        return self.hosts.get(user_at_host_key)

    def add_host(self, user: str, host: str, port: int, password: str):
        """Add or update a host configuration. The key will be 'user@host'."""
        # Validate port range
        clamped_port = max(1, min(port, 65535))
        if clamped_port != port:
            logger.warning(f"Clamping invalid port {port} to {clamped_port}")

        key = f"{user}@{host}"
        self.hosts[key] = {
            'password': password,
            'port': clamped_port,
            'parsed_user': user,
            'parsed_host': host
        }
        self._save_hosts()

    def _save_hosts(self):
        """Save hosts to TOML config file."""
        data_to_save: Dict[str, Dict[str, Any]] = {}
        for key, details in self.hosts.items():
            # Only save password and port to the TOML file, as user and host are in the key
            data_to_save[key] = {
                'password': details['password'],
                'port': details['port']
            }

        try:
            with open(self.config_path, 'w') as f:
                toml.dump(data_to_save, f)
            self.config_path.chmod(0o600)  # Maintain secure permissions
        except Exception as e:
            logger.error(f"Failed to save SSH hosts to {self.config_path}: {e}")
            raise SshError(f"Failed to save host configuration to {self.config_path}")
