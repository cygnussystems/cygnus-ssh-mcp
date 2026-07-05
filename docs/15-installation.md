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

## Uninstalling / Upgrading

```bash
pip uninstall cygnus-ssh-mcp
pip install --upgrade cygnus-ssh-mcp
```

If you installed with `uv tool install cygnus-ssh-mcp` instead of pip, use
`uv tool uninstall cygnus-ssh-mcp` / `uv tool upgrade cygnus-ssh-mcp`. `uvx` doesn't
persist an install at all - it re-fetches the latest version on every run, so
there's nothing to uninstall and no separate upgrade step.

Uninstalling only removes the Python package - your host config file
(`~/.mcp_ssh_hosts.toml`) and any Claude Desktop config changes are left in place,
since they may contain saved credentials or configuration you want to keep. Remove
them yourself if you no longer need them.

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

Note: none of the `--config` paths above need to already exist - the server creates
an empty host configuration file at that path (with secure `0o600` permissions) the
first time it starts if nothing is there yet. See
[Host Configuration](30-host-configuration.md) for the exact lookup order and
auto-creation behavior.

## Next Steps

1. [Add your first host](30-host-configuration.md)
2. [Configure Claude Desktop](80-claude-desktop.md)
3. [Review available tools](40-tools-reference.md)
