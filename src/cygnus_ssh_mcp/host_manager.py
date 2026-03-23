

import logging
import tomlkit
from pathlib import Path
from typing import Dict, Any, Optional
from cygnus_ssh_mcp.models import SshError

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
            home_config = Path.home() / ".mcp_ssh_hosts.toml"
            project_config = Path.cwd() / "mcp_ssh_hosts.toml"

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
                data = tomlkit.load(f)

            for key, config_details in data.items():
                # Require either password or keyfile (or both), plus port
                has_password = 'password' in config_details
                has_keyfile = 'keyfile' in config_details

                if not isinstance(config_details, dict) or \
                        not (has_password or has_keyfile) or \
                        'port' not in config_details:
                    logger.warning(f"Skipping malformed configuration for '{key}' in {self.config_path}. "
                                   "Each host must be a table with ('password' or 'keyfile') and 'port'.")
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
                    'password': str(config_details['password']) if has_password else None,
                    'port': int(config_details['port']),  # Ensure port is an int
                    'parsed_user': user,
                    'parsed_host': host_address,
                    'sudo_password': str(config_details.get('sudo_password', '')) if 'sudo_password' in config_details else None,
                    'alias': str(config_details.get('alias', '')) if 'alias' in config_details else None,
                    'description': str(config_details.get('description', '')) if 'description' in config_details else None,
                    'keyfile': str(config_details.get('keyfile', '')) if 'keyfile' in config_details else None,
                    'key_passphrase': str(config_details.get('key_passphrase', '')) if 'key_passphrase' in config_details else None
                }
            return loaded_hosts
        except FileNotFoundError:
            logger.error(f"Configuration file not found: {self.config_path}")
            return {}  # Should be created by _ensure_config_file, but good to handle
        except tomlkit.exceptions.TOMLKitError as e:
            logger.error(f"Failed to parse TOML configuration file {self.config_path}: {e}")
            return {}
        except Exception as e:
            logger.error(f"Failed to load SSH hosts from {self.config_path}: {e}")
            return {}

    def get_host(self, user_at_host_key: str) -> Optional[Dict[str, Any]]:
        """Get host config by 'user@hostname' key."""
        return self.hosts.get(user_at_host_key)

    def get_host_by_alias(self, alias: str) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
        """
        Get host config by alias.

        Args:
            alias: The alias to look up

        Returns:
            Tuple of (host_key, host_config) if found, (None, None) if not found

        Raises:
            SshError: If multiple hosts have the same alias
        """
        matches = []
        for key, details in self.hosts.items():
            if details.get('alias') == alias:
                matches.append((key, details))

        if len(matches) > 1:
            host_keys = [m[0] for m in matches]
            raise SshError(f"Duplicate alias '{alias}' found on multiple hosts: {', '.join(host_keys)}")

        if matches:
            return matches[0]
        return None, None

    def resolve_host(self, identifier: str) -> tuple[str, Dict[str, Any]]:
        """
        Resolve a host by key (user@host) or alias.

        Args:
            identifier: Either a 'user@host' key or an alias

        Returns:
            Tuple of (host_key, host_config)

        Raises:
            SshError: If host not found or duplicate aliases exist
        """
        # First try direct key lookup
        host = self.get_host(identifier)
        if host:
            return identifier, host

        # Then try alias lookup
        key, host = self.get_host_by_alias(identifier)
        if host:
            return key, host

        raise SshError(f"Host '{identifier}' not found (tried as key and alias)")

    def remove_host(self, user_at_host_key: str) -> bool:
        """
        Remove a host configuration by key.

        Args:
            user_at_host_key: The 'user@host' key to remove

        Returns:
            True if host was removed, False if not found
        """
        if user_at_host_key in self.hosts:
            del self.hosts[user_at_host_key]
            self._save_hosts()
            return True
        return False

    def add_host(self, user: str, host: str, port: int, password: Optional[str] = None,
                 sudo_password: Optional[str] = None, alias: Optional[str] = None,
                 description: Optional[str] = None, keyfile: Optional[str] = None,
                 key_passphrase: Optional[str] = None):
        """Add or update a host configuration. The key will be 'user@host'.

        Either password or keyfile (or both) must be provided.
        """
        # Validate port range
        clamped_port = max(1, min(port, 65535))
        if clamped_port != port:
            logger.warning(f"Invalid port number {port} - clamping to {clamped_port}")

        key = f"{user}@{host}"
        self.hosts[key] = {
            'password': password,
            'port': clamped_port,
            'parsed_user': user,
            'parsed_host': host,
            'sudo_password': sudo_password,
            'alias': alias,
            'description': description,
            'keyfile': keyfile,
            'key_passphrase': key_passphrase
        }
        self._save_hosts()

    def _save_hosts(self):
        """Save hosts to TOML config file, preserving comments and formatting."""
        try:
            # Load existing document to preserve comments
            if self.config_path.exists():
                with open(self.config_path, 'r') as f:
                    doc = tomlkit.load(f)
            else:
                doc = tomlkit.document()
                doc.add(tomlkit.comment("SSH Host Configurations"))
                doc.add(tomlkit.nl())

            # Get current keys in document
            existing_keys = set(doc.keys())
            current_keys = set(self.hosts.keys())

            # Remove hosts that are no longer in self.hosts
            for key in existing_keys - current_keys:
                del doc[key]

            # Add/update hosts
            for key, details in self.hosts.items():
                host_table = tomlkit.table()
                if details.get('password'):
                    host_table.add('password', details['password'])
                if details.get('keyfile'):
                    host_table.add('keyfile', details['keyfile'])
                if details.get('key_passphrase'):
                    host_table.add('key_passphrase', details['key_passphrase'])
                host_table.add('port', details['port'])
                if details.get('sudo_password'):
                    host_table.add('sudo_password', details['sudo_password'])
                if details.get('alias'):
                    host_table.add('alias', details['alias'])
                if details.get('description'):
                    host_table.add('description', details['description'])
                doc[key] = host_table

            with open(self.config_path, 'w') as f:
                tomlkit.dump(doc, f)
            self.config_path.chmod(0o600)  # Maintain secure permissions
        except Exception as e:
            logger.error(f"Failed to save SSH hosts to {self.config_path}: {e}")
            raise SshError(f"Failed to save host configuration to {self.config_path}")
