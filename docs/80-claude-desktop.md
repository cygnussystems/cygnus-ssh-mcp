# Claude Desktop Integration

## Overview

This guide explains how to configure the SSH MCP Server for use with Claude Desktop, enabling Claude to manage remote Linux servers via SSH.

## Configuration File Location

Claude Desktop stores MCP server configurations in:

**Windows:**
```
%APPDATA%\Claude\claude_desktop_config.json
```

**macOS:**
```
~/Library/Application Support/Claude/claude_desktop_config.json
```

**Linux:**
```
~/.config/Claude/claude_desktop_config.json
```

---

## Basic Configuration

First, install the package:

```bash
pip install cygnus-ssh-mcp
```

Then add the SSH MCP server to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "ssh": {
      "command": "cygnus-ssh-mcp",
      "args": ["--config", "/path/to/ssh_hosts.toml"]
    }
  }
}
```

### Using uvx (No Permanent Install)

Run directly without installing:

```json
{
  "mcpServers": {
    "ssh": {
      "command": "uvx",
      "args": ["cygnus-ssh-mcp", "--config", "/path/to/ssh_hosts.toml"]
    }
  }
}
```

---

## Full Configuration Example

```json
{
  "mcpServers": {
    "ssh": {
      "command": "cygnus-ssh-mcp",
      "args": ["--config", "~/.mcp_ssh_hosts.toml"],
      "env": {
        "LOG_LEVEL": "INFO"
      }
    }
  }
}
```

---

## Configuration Options

### Command Line Arguments

| Argument | Description |
|----------|-------------|
| `--config PATH` | Path to host configuration file |

### Environment Variables

| Variable | Description |
|----------|-------------|
| `PYTHONUNBUFFERED` | Set to "1" for immediate output |
| `LOG_LEVEL` | Logging verbosity (DEBUG, INFO, WARNING, ERROR) |

---

## Host Configuration

Create your host configuration file (e.g., `~/.ssh_hosts.toml`):

```toml
# Production server
[admin@prod.example.com]
password = "secretPassword"
port = 22
sudo_password = "sudoPass"
alias = "prod"
description = "Production web server"

# Development server
[dev@dev.example.com]
password = "devPassword"
alias = "dev"
description = "Development environment"
```

**Security:** Ensure this file has restricted permissions:
```bash
chmod 600 ~/.ssh_hosts.toml
```

---

## Verifying the Setup

After configuring, restart Claude Desktop. You should see:

1. **SSH tools available** in Claude's tool list
2. **Connection capability** - Ask Claude: "Connect to my prod server"
3. **Tool execution** - Ask Claude: "Run `ls -la` on the server"

### Testing Connection
```
User: Connect to prod and show me the system info

Claude: [Uses ssh_conn_connect and ssh_conn_host_info]
Connected to prod.example.com. Here's the system information:
- OS: Debian 12
- CPU: 4 cores
- Memory: 8GB (5GB available)
- Disk: 100GB (60GB free)
```

---

## Troubleshooting

### Server Not Starting

1. **Check paths** - Ensure all paths in config are absolute
2. **Test manually** - Run the command in a terminal:
   ```bash
   /path/to/.venv/bin/python /path/to/mcp_ssh_server.py --config /path/to/hosts.toml
   ```
3. **Check logs** - Look for errors in Claude Desktop logs

### Tools Not Appearing

1. **Restart Claude Desktop** after config changes
2. **Validate JSON** - Use a JSON validator on your config
3. **Check server output** - The server should list available tools on startup

### Connection Failures

1. **Verify host config** - Check `ssh_hosts.toml` syntax
2. **Test SSH manually** - `ssh user@host` should work
3. **Check firewall** - Ensure SSH port is accessible
4. **Verify credentials** - Password may have changed

### Permission Errors

1. **sudo operations failing** - Verify `sudo_password` in config
2. **File access denied** - May need `use_sudo=True`
3. **Host config not found** - Check file path and permissions

---

## Security Recommendations

1. **Secure host config file** - chmod 600
2. **Use separate sudo password** - Don't reuse SSH password
3. **Limit server access** - Only configure necessary hosts
4. **Review commands** - Be cautious with destructive operations
5. **Network security** - Use VPN for remote servers

---

## Example Interactions

### Basic Server Management
```
User: Connect to prod and check disk space

Claude: [Connects and runs df -h]
Disk usage on prod.example.com:
- / (root): 45% used (27GB of 60GB)
- /home: 12% used (2GB of 16GB)
```

### File Operations
```
User: Read the nginx config on prod

Claude: [Uses ssh_file_read or ssh_cmd_run with cat]
Here's /etc/nginx/nginx.conf:
[file contents...]
```

### Process Management
```
User: Restart the web service on prod

Claude: [Uses ssh_cmd_run with sudo]
Restarted nginx service. Current status: active (running)
```

---

## Multiple Servers

You can configure multiple SSH MCP server instances for different environments:

```json
{
  "mcpServers": {
    "ssh-prod": {
      "command": "python",
      "args": ["mcp_ssh_server.py", "--config", "prod_hosts.toml"]
    },
    "ssh-dev": {
      "command": "python",
      "args": ["mcp_ssh_server.py", "--config", "dev_hosts.toml"]
    }
  }
}
```

Or use a single server with all hosts in one config file and use aliases to distinguish them.
