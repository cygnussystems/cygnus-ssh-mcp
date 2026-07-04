# SSH Host Configuration

## Overview

The MCP server uses a TOML configuration file to store SSH host credentials and settings. This allows you to pre-configure hosts and connect to them by name or alias rather than providing credentials each time.

## File Locations

The server looks for host configurations in this order:

1. **Explicit path** via `--config` command-line parameter
2. **User home directory**: `~/.mcp_ssh_hosts.toml`
3. **Current working directory**: `./mcp_ssh_hosts.toml`

**The file is created automatically the first time the server starts**, at whichever
of the paths above it resolves to (home directory takes priority if `--config` isn't
given). If none of these files exist yet, the server creates an empty one at the
home-directory path with a few commented-out example entries, and sets its
permissions to `0o600` (owner read/write only) immediately. You don't need to create
this file yourself before adding your first host - `ssh_conn_add_host` (or hand-editing
the file, if you prefer) both just work against whatever file already exists.

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

**Tip:** If a host is reachable both directly (its own configured alias) and indirectly
by SSH'ing through another host, prefer connecting via its direct alias when one exists.
Nesting SSH through an intermediate host adds an extra hop where key/password mismatches
can surface confusingly (auth errors that look like they belong to the wrong host), even
when the direct alias works fine.

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
  "config_path": "/home/user/.mcp_ssh_hosts.toml"
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

## Updating Hosts

To change a field on an existing host (rotate a password, change the port, add a
description) without losing every other field, use `ssh_host_update` rather than
removing and re-adding. Only the fields you pass are changed; pass an empty string
`""` to explicitly clear a field (e.g. dropping a password when switching a host to
key-only auth):

```
ssh_host_update(host_name="admin@server.example.com", password="new_password123")
ssh_host_update(host_name="myserver", port=2222, description="Moved to new port")
ssh_host_update(host_name="myserver", password="", keyfile="~/.ssh/id_ed25519")
```

## Using an Alternate Host List

By default, every host tool (`ssh_host_list`, `ssh_conn_connect`,
`ssh_conn_add_host`, `ssh_host_update`, `ssh_host_remove`) operates against the
single config file the server resolved at startup (see File Locations above). If you
maintain more than one host list - e.g. separate files per project or environment -
use `ssh_host_use_config` to point all of these tools at a different file for the
rest of the session, without restarting the server or merging everything into one
file:

```
ssh_host_use_config(config_path="~/projects/client-a/hosts.toml")
# ssh_host_list, ssh_conn_connect, etc. now all use client-a/hosts.toml

ssh_host_use_config()  # or config_path=""
# back to the server's default config file
```

The alternate file must already exist and be valid host configuration TOML - unlike
the server's own default file, this does not auto-create a missing path (an
LLM-supplied typo in a path should fail loudly, not silently create a stray file).
`ssh_host_list`'s response always includes `config_path`, so it's easy to confirm
which file is currently active before running `ssh_conn_add_host`/`ssh_host_update`/
`ssh_host_remove`.

This is a session-wide switch, not a per-call argument - it stays in effect for
every subsequent host tool call until you switch again or the server restarts. It's
independent of any active SSH connection (`ssh_conn_connect`/`ssh_host_disconnect`),
which is unaffected by switching config files.

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
chmod 600 ~/.mcp_ssh_hosts.toml

# Windows (PowerShell)
icacls $env:USERPROFILE\.mcp_ssh_hosts.toml /inheritance:r /grant:r "$($env:USERNAME):(R,W)"
```

The server also sets these permissions itself (`0o600`) whenever it creates or
updates the file, so this is a defense-in-depth step, not something you need to
maintain manually.

### Never Read the Config File Directly

Every host's password, sudo password, and key passphrase is stored here in
**plaintext** - that's the whole reason `ssh_host_list`, `ssh_conn_add_host`,
`ssh_host_update`, and `ssh_host_remove` exist as dedicated tools instead of asking
callers to edit the file themselves. None of these four tools ever return a stored
credential back out. If you're driving this server through an LLM agent, make sure
it's instructed to use these tools rather than reading/parsing
`~/.mcp_ssh_hosts.toml` directly (e.g. via a generic file-read or shell tool) - doing
so would expose every configured host's credentials to the LLM at once, not just the
one host it actually needed.

### General Security

1. **Prefer SSH keys** over passwords for authentication
2. **Use passphrases** on SSH keys for additional security
3. **Never commit** the config file to version control
4. **Add to .gitignore**: `mcp_ssh_hosts.toml` and `*.toml`
5. **Use unique credentials** per host when possible
6. **Restrict key permissions**: `chmod 600 ~/.ssh/id_rsa`

### Example .gitignore Entry

```gitignore
# SSH host configurations (contain credentials)
mcp_ssh_hosts.toml
.mcp_ssh_hosts.toml
*.mcp_ssh_hosts.toml
```

## Command Line Usage

```bash
# Use custom config location
python mcp_ssh_server.py --config /path/to/custom_hosts.toml

# Default locations are checked automatically
python mcp_ssh_server.py
```

## Reloading Configuration

No reload step is needed. Host configuration is read fresh from the TOML file on
every access (`ssh_conn_connect`, `ssh_host_list`, etc.) - there is no in-memory
cache to invalidate. Edit the config file while the server is running and the
next call will see the change.
