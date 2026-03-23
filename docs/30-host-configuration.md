# SSH Host Configuration

## Overview

The MCP server uses a TOML configuration file to store SSH host credentials and settings. This allows you to pre-configure hosts and connect to them by name or alias rather than providing credentials each time.

## File Locations

The server looks for host configurations in this order:

1. **Explicit path** via `--config` command-line parameter
2. **User home directory**: `~/.ssh_hosts.toml`
3. **Current working directory**: `./ssh_hosts.toml`

## File Format

The TOML file uses section names in `user@hostname` format:

```toml
[user@hostname]
password = "plaintext_password"    # Password authentication
keyfile = "~/.ssh/id_rsa"          # Key-based authentication (alternative to password)
key_passphrase = "passphrase"      # Passphrase for encrypted keys (optional)
port = 22                          # SSH port (default: 22)
sudo_password = "sudo_pass"        # Password for sudo operations
alias = "shortname"                # Friendly name for quick access
description = "Server description" # Human-readable description
```

**Note:** At least one of `password` or `keyfile` must be provided.

## Authentication Methods

### Password Authentication
Traditional password-based login:

```toml
[admin@server.example.com]
password = "mypassword"
port = 22
```

### Key-Based Authentication
Using an SSH private key (recommended):

```toml
[deploy@server.example.com]
keyfile = "~/.ssh/id_ed25519"
port = 22
```

### Key with Passphrase
For encrypted private keys:

```toml
[admin@secure.example.com]
keyfile = "~/.ssh/id_rsa"
key_passphrase = "my_key_passphrase"
port = 22
```

### Both Methods (Key Preferred)
Key authentication is attempted first, with password as fallback:

```toml
[ops@server.example.com]
keyfile = "~/.ssh/id_rsa"
password = "fallback_password"
port = 22
```

## Configuration Fields

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `password` | No* | - | SSH password for authentication |
| `keyfile` | No* | - | Path to SSH private key file |
| `key_passphrase` | No | - | Passphrase for encrypted SSH keys |
| `port` | No | 22 | SSH port number |
| `sudo_password` | No | Same as `password` | Password for sudo operations |
| `alias` | No | - | Short name for quick connection |
| `description` | No | - | Human-readable server description |

*At least one of `password` or `keyfile` must be provided.

## Example Configuration

```toml
# Development server (password auth)
[developer@dev.example.com]
password = "devpass123"
port = 22
alias = "dev"
description = "Development environment"

# Production server (key auth with sudo password)
[admin@prod.example.com]
keyfile = "~/.ssh/prod_key"
sudo_password = "sudoPassword"
port = 2222
alias = "prod"
description = "Production web server"

# Staging server (key with passphrase)
[deploy@staging.example.com]
keyfile = "~/.ssh/staging_ed25519"
key_passphrase = "staging_passphrase"
alias = "staging"
description = "Staging environment for testing"

# CI/CD deployment (key auth, passwordless sudo on server)
[deploy@ci.example.com]
keyfile = "~/.ssh/ci_deploy_key"
port = 22
alias = "ci"
description = "CI/CD deployment target"
```

## Connecting to Hosts

### By Full Key
```
ssh_conn_connect(host_name="admin@prod.example.com")
```

### By Alias
```
ssh_conn_connect(host_name="prod")
```

Both methods work interchangeably. The server first checks if the identifier matches a `user@host` key, then searches by alias.

## Listing Configured Hosts

Use `ssh_host_list()` to view all configured hosts:

```json
{
  "hosts": {
    "developer@dev.example.com": {
      "port": 22,
      "has_password": true,
      "has_keyfile": false,
      "has_sudo_password": false,
      "alias": "dev",
      "description": "Development environment"
    },
    "admin@prod.example.com": {
      "port": 2222,
      "has_password": false,
      "has_keyfile": true,
      "has_sudo_password": true,
      "alias": "prod",
      "description": "Production web server"
    }
  },
  "count": 2,
  "config_path": "/home/user/.ssh_hosts.toml"
}
```

## Adding Hosts at Runtime

Hosts can be added programmatically without editing the config file:

### With Password
```
ssh_conn_add_host(
    host="server.example.com",
    user="admin",
    password="password123",
    port=22,
    sudo_password="sudopass",
    alias="myserver",
    description="My new server"
)
```

### With SSH Key
```
ssh_conn_add_host(
    host="server.example.com",
    user="deploy",
    keyfile="~/.ssh/deploy_key",
    port=22,
    alias="deploy-server",
    description="Deployment server"
)
```

### With Encrypted Key
```
ssh_conn_add_host(
    host="secure.example.com",
    user="admin",
    keyfile="~/.ssh/id_rsa",
    key_passphrase="my_passphrase",
    sudo_password="sudopass",
    port=22
)
```

This saves the host to the configuration file for future use.

## Removing Hosts

```
ssh_host_remove(host_name="admin@server.example.com")
# or by alias
ssh_host_remove(host_name="myserver")
```

## Sudo Operations with Key Authentication

When using key-based authentication without a password, sudo operations require special handling:

1. **Explicit sudo_password**: Provide `sudo_password` in the config
2. **Passwordless sudo**: Configure the server for passwordless sudo (NOPASSWD in sudoers)

If neither is available, sudo operations will fail at runtime with a clear error message.

```toml
# Key auth with explicit sudo password
[admin@server.example.com]
keyfile = "~/.ssh/id_rsa"
sudo_password = "sudo_password_here"
port = 22

# Key auth with passwordless sudo configured on server
[deploy@server.example.com]
keyfile = "~/.ssh/deploy_key"
port = 22
# No sudo_password needed if server has NOPASSWD configured
```

## Security Best Practices

### File Permissions

Set strict permissions on your config file:

```bash
# Linux/macOS
chmod 600 ~/.ssh_hosts.toml

# Windows (PowerShell)
icacls $env:USERPROFILE\.ssh_hosts.toml /inheritance:r /grant:r "$($env:USERNAME):(R,W)"
```

### General Security

1. **Prefer SSH keys** over passwords for authentication
2. **Use passphrases** on SSH keys for additional security
3. **Never commit** the config file to version control
4. **Add to .gitignore**: `ssh_hosts.toml` and `*.toml`
5. **Use unique credentials** per host when possible
6. **Restrict key permissions**: `chmod 600 ~/.ssh/id_rsa`

### Example .gitignore Entry

```gitignore
# SSH host configurations (contain credentials)
ssh_hosts.toml
.ssh_hosts.toml
*.ssh_hosts.toml
```

## Command Line Usage

```bash
# Use custom config location
python mcp_ssh_server.py --config /path/to/custom_hosts.toml

# Default locations are checked automatically
python mcp_ssh_server.py
```

## Reloading Configuration

If you edit the config file while the server is running:

```
ssh_host_reload_config()
```

This reloads the configuration from disk without restarting the server.
