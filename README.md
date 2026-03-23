# cygnus-ssh-mcp

An MCP (Model Context Protocol) server for SSH remote server management. Enables AI assistants like Claude to execute commands, manage files, and perform system administration tasks on remote Linux servers.

## Features

- **Command Execution**: Run commands with timeout control and background process support
- **File Operations**: Read, write, edit, search files and directories
- **Process Management**: Background tasks, process monitoring, interruption
- **Sudo Support**: Elevated privileges with password or passwordless sudo
- **Multi-Auth**: Password or SSH key authentication (with passphrase support)
- **Host Management**: TOML-based configuration with aliases

## Installation

```bash
pip install cygnus-ssh-mcp
```

Or with [uvx](https://docs.astral.sh/uv/):

```bash
uvx cygnus-ssh-mcp
```

## Quick Start

### 1. Create a hosts configuration file

Create `~/.mcp_ssh_hosts.toml`:

```toml
[admin@myserver.com]
password = "your_password"
port = 22
alias = "myserver"
description = "My production server"

# Or with SSH key
[deploy@server2.com]
keyfile = "~/.ssh/id_ed25519"
port = 22
alias = "deploy"
```

### 2. Configure Claude Desktop

Add to your Claude Desktop config (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "ssh": {
      "command": "cygnus-ssh-mcp",
      "args": ["--config", "/path/to/.mcp_ssh_hosts.toml"]
    }
  }
}
```

### 3. Connect and use

In Claude, connect to a host:
```
Connect to myserver
```

Then run commands:
```
Show disk usage on the server
```

## Documentation

See the [docs/](docs/) folder for detailed documentation:

- [Overview](docs/10-overview.md)
- [Installation](docs/15-installation.md)
- [Platform Compatibility](docs/20-platform-compatibility.md)
- [Host Configuration](docs/30-host-configuration.md)
- [Tools Reference](docs/40-tools-reference.md)
- [Command Execution](docs/50-command-execution.md)
- [Process Management](docs/60-process-management.md)
- [Logging](docs/70-logging.md)
- [Claude Desktop Setup](docs/80-claude-desktop.md)

## Available Tools

| Tool | Description |
|------|-------------|
| `ssh_conn_connect` | Connect to a configured host |
| `ssh_cmd_run` | Execute a command |
| `ssh_cmd_run_sudo` | Execute with sudo |
| `ssh_file_read` | Read file contents |
| `ssh_file_write` | Write to a file |
| `ssh_file_edit` | Edit file with search/replace |
| `ssh_dir_list` | List directory contents |
| `ssh_task_start` | Start background task |
| `ssh_task_status` | Check task status |
| ... and more |

See [Tools Reference](docs/40-tools-reference.md) for the complete list.

## License

GPL-3.0 - See [LICENSE](LICENSE) for details.

## Author

Peter Ritter
