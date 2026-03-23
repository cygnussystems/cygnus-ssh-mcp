# Installation

## Requirements

- Python 3.10 or higher
- pip or uv package manager

## Install from PyPI

```bash
pip install cygnus-ssh-mcp
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv pip install cygnus-ssh-mcp
```

## Run without Installing

Use `uvx` to run directly without permanent installation:

```bash
uvx cygnus-ssh-mcp --config ~/.mcp_ssh_hosts.toml
```

This downloads and runs the latest version each time.

## Verify Installation

```bash
cygnus-ssh-mcp --help
```

Expected output:
```
usage: cygnus-ssh-mcp [-h] [--config CONFIG]

SSH MCP Server - Remote server management via SSH

options:
  -h, --help       show this help message and exit
  --config CONFIG  Path to TOML host configuration file
```

## Claude Desktop Configuration

After installation, add to your `claude_desktop_config.json`:

**Using pip install:**
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

**Using uvx (no permanent install):**
```json
{
  "mcpServers": {
    "ssh": {
      "command": "uvx",
      "args": ["cygnus-ssh-mcp", "--config", "/path/to/.mcp_ssh_hosts.toml"]
    }
  }
}
```

See [Claude Desktop Setup](80-claude-desktop.md) for detailed configuration options.

## Next Steps

1. [Create a host configuration file](30-host-configuration.md)
2. [Configure Claude Desktop](80-claude-desktop.md)
3. [Review available tools](40-tools-reference.md)
