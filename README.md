<div align="center">

<img src="assets/banner.png" alt="cygnus-ssh-mcp" width="400">

# cygnus-ssh-mcp

**The most powerful SSH MCP server for AI assistants**

[![PyPI version](https://img.shields.io/pypi/v/cygnus-ssh-mcp.svg)](https://pypi.org/project/cygnus-ssh-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/cygnus-ssh-mcp.svg)](https://pypi.org/project/cygnus-ssh-mcp/)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Tests](https://img.shields.io/badge/tests-119%2B%20passing-brightgreen.svg)]()

*Give Claude full control of your Linux servers with 43+ specialized tools*

[Installation](#installation) · [Quick Start](#quick-start) · [Features](#features) · [Documentation](docs/)

</div>

---

## Why cygnus-ssh-mcp?

Most SSH MCP servers let you run commands. **cygnus-ssh-mcp** lets you *manage servers*.

| What you get | Basic SSH MCP | cygnus-ssh-mcp |
|--------------|:-------------:|:--------------:|
| Run commands | ✅ | ✅ |
| Pre-configured hosts with aliases | ❌ | ✅ |
| Sudo support (all operations) | Limited | ✅ |
| Background task management | ❌ | ✅ |
| Line-level file editing | ❌ | ✅ |
| Command history with output | ❌ | ✅ |
| Recursive directory operations | ❌ | ✅ |
| Archive create/extract | ❌ | ✅ |
| Full Unicode support | ? | ✅ |

---

## Installation

```bash
pip install cygnus-ssh-mcp
```

Or run without installing using [uvx](https://docs.astral.sh/uv/):

```bash
uvx cygnus-ssh-mcp
```

---

## Quick Start

### 1. Create your hosts file

Create `~/.mcp_ssh_hosts.toml`:

```toml
["admin@production.example.com"]
password = "your_password"
port = 22
sudo_password = "sudo_pass"
alias = "prod"
description = "Production web server"

# Or use SSH keys
["deploy@staging.example.com"]
keyfile = "~/.ssh/id_ed25519"
alias = "staging"
```

### 2. Add to Claude Desktop

Edit your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "ssh": {
      "command": "cygnus-ssh-mcp"
    }
  }
}
```

### 3. Start managing servers

In Claude, just say:

> "Connect to prod and show me the disk usage"

> "Edit /etc/nginx/nginx.conf and change worker_connections to 2048"

> "Find all .log files larger than 100MB in /var/log"

---

## Features

### Host Configuration

Stop typing credentials. Connect by alias.

```toml
["admin@server.com"]
password = "secret"
alias = "web"
description = "Web server"
```

Then just: *"Connect to web"*

Supports **password**, **SSH key**, and **encrypted keys with passphrase**.

---

### Line-Level File Editing

Edit config files with surgical precision—no download/upload needed.

```python
# Replace a single line
ssh_file_replace_line(
    file_path="/etc/nginx/nginx.conf",
    match_line="worker_connections 1024;",
    new_line="worker_connections 4096;"
)

# Insert lines after a match
ssh_file_insert_lines_after_match(
    file_path="/etc/hosts",
    match_line="# Custom entries",
    lines_to_insert=["192.168.1.10 app.local", "192.168.1.11 db.local"]
)
```

**Safety built-in**: Operations fail if the match isn't unique—no accidental mass edits.

---

### Background Task Management

Launch long-running processes and check back later.

```python
# Start a backup (returns immediately)
ssh_task_launch(command="./backup.sh", stdout_log="/var/log/backup.log")

# Check status anytime
ssh_task_status(pid=12345)  # → 'running' or 'exited'

# Kill if needed
ssh_task_kill(pid=12345, force=True)
```

---

### Comprehensive Sudo Support

Every tool supports `use_sudo`. Password is handled automatically.

```python
ssh_file_write(path="/etc/app/config.yaml", content="...", use_sudo=True)
ssh_dir_mkdir(path="/opt/myapp", use_sudo=True)
ssh_archive_extract(archive="/backup.tar.gz", dest="/", use_sudo=True)
```

---

### Dual Timeout System

Never get stuck on a hanging command.

```python
ssh_cmd_run(
    command="./long_script.sh",
    io_timeout=60.0,       # Kill if no output for 60s
    runtime_timeout=3600.0  # Kill if total time exceeds 1 hour
)
```

---

### Full Unicode Support

Write documentation, reports, and configs with emojis and international text.

Tested with: ✅ ❌ 🎉 • → ≥ ∞ │ ┌ ─ 你好 مرحبا Привет

---

### And Much More...

- **Command history** with output retention and pattern filtering
- **Recursive directory operations**: search, copy, delete with dry-run
- **Archive operations**: create and extract tar.gz
- **System info**: OS version, memory, disk, CPU, uptime
- **Pattern search**: regex and plain text in files

---

## All 43+ Tools

<details>
<summary><strong>Connection & Host Management (11 tools)</strong></summary>

| Tool | Description |
|------|-------------|
| `ssh_conn_connect` | Connect using pre-configured host (by key or alias) |
| `ssh_conn_is_connected` | Check if SSH connection is active |
| `ssh_conn_status` | Get connection status (user, host, OS, cwd) |
| `ssh_conn_host_info` | Get detailed system information |
| `ssh_conn_verify_sudo` | Verify sudo access |
| `ssh_conn_add_host` | Add new host to configuration |
| `ssh_host_list` | List all configured hosts |
| `ssh_host_remove` | Remove host from configuration |
| `ssh_host_reload_config` | Reload TOML config |
| `ssh_host_disconnect` | Disconnect current session |
| `list_tools` | List all available tools |

</details>

<details>
<summary><strong>Command Execution (6 tools)</strong></summary>

| Tool | Description |
|------|-------------|
| `ssh_cmd_run` | Execute command with I/O and runtime timeouts |
| `ssh_cmd_kill` | Terminate running command |
| `ssh_cmd_check_status` | Check command status |
| `ssh_cmd_output` | Retrieve output from command |
| `ssh_cmd_history` | Get command history with filtering |
| `ssh_cmd_clear_history` | Clear command history |

</details>

<details>
<summary><strong>Background Tasks (3 tools)</strong></summary>

| Tool | Description |
|------|-------------|
| `ssh_task_launch` | Launch command in background |
| `ssh_task_status` | Check if task is running |
| `ssh_task_kill` | Send signal to task |

</details>

<details>
<summary><strong>File Operations (11 tools)</strong></summary>

| Tool | Description |
|------|-------------|
| `ssh_file_stat` | Get file metadata |
| `ssh_file_write` | Create/overwrite/append file |
| `ssh_file_copy` | Copy file |
| `ssh_file_move` | Move or rename file |
| `ssh_file_transfer` | Upload or download files |
| `ssh_file_find_lines_with_pattern` | Search for pattern in file |
| `ssh_file_get_context_around_line` | Get context around match |
| `ssh_file_replace_line` | Replace single line |
| `ssh_file_replace_line_multi` | Replace with multiple lines |
| `ssh_file_insert_lines_after_match` | Insert lines after match |
| `ssh_file_delete_line_by_content` | Delete line by content |

</details>

<details>
<summary><strong>Directory Operations (10 tools)</strong></summary>

| Tool | Description |
|------|-------------|
| `ssh_dir_mkdir` | Create directory |
| `ssh_dir_remove` | Remove directory |
| `ssh_dir_list_files_basic` | Basic directory listing |
| `ssh_dir_list_advanced` | Recursive listing with metadata |
| `ssh_dir_search_glob` | Search files by pattern |
| `ssh_dir_search_files_content` | Search text in files |
| `ssh_dir_calc_size` | Calculate directory size |
| `ssh_dir_delete` | Delete with dry-run support |
| `ssh_dir_batch_delete_files` | Batch delete by pattern |
| `ssh_dir_copy` | Copy directory recursively |

</details>

<details>
<summary><strong>Archive Operations (2 tools)</strong></summary>

| Tool | Description |
|------|-------------|
| `ssh_archive_create` | Create tar.gz archive |
| `ssh_archive_extract` | Extract archive |

</details>

---

## Documentation

Detailed guides available in [docs/](docs/):

- [Overview](docs/10-overview.md)
- [Installation](docs/15-installation.md)
- [Platform Compatibility](docs/20-platform-compatibility.md)
- [Host Configuration](docs/30-host-configuration.md)
- [Tools Reference](docs/40-tools-reference.md)
- [Command Execution](docs/50-command-execution.md)
- [Process Management](docs/60-process-management.md)
- [Logging](docs/70-logging.md)
- [Claude Desktop Setup](docs/80-claude-desktop.md)

---

## Use Cases

- **DevOps Automation** — Deploy, configure, and manage servers via AI
- **Log Analysis** — Search and analyze logs across multiple servers
- **Configuration Management** — Edit configs with precision line operations
- **Backup & Recovery** — Create archives, transfer files, restore backups
- **System Monitoring** — Check status, verify services, monitor processes
- **Security Auditing** — Search for sensitive patterns, verify configurations

---

## License

[GPL-3.0](LICENSE) — Free and open source.

---

<div align="center">

**Built by [Peter Ritter](https://github.com/cygnussystems)**

*Star this repo if you find it useful!*

</div>
