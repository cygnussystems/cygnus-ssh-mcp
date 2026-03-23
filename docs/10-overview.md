# SSH MCP Server Overview

## What is This?

The SSH MCP Server is a Model Context Protocol (MCP) server that provides SSH-based remote server management capabilities. It enables AI assistants like Claude to securely connect to and manage remote Linux servers through a comprehensive set of tools.

## Key Capabilities

### Connection Management
- Connect to remote hosts using password or key-based authentication
- Store and manage host configurations with aliases for easy reference
- Support for sudo operations with password handling
- Connection status monitoring and reconnection

### Command Execution
- Execute commands with configurable I/O and runtime timeouts
- Background task management for long-running operations
- Command history tracking with output retrieval
- Process monitoring and termination

### File Operations
- Read, write, and transfer files (upload/download via SFTP)
- Pattern-based line searching and replacement
- Block replacement for multi-line edits
- File copy, move, and delete operations
- All file operations support sudo elevation

### Directory Operations
- Create, list, and remove directories
- Glob-based file searching
- Content-based file searching (grep-like)
- Directory size calculation
- Batch file deletion with pattern matching
- Directory copy operations

### Archive Operations
- Create compressed archives (tar.gz, tar.bz2, zip)
- Extract archives to specified locations

### System Information
- Host information (CPU, memory, disk, network)
- Connection status and session details
- Sudo access verification

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     MCP Client (Claude)                      │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    MCP SSH Server                            │
│  ┌─────────────────────────────────────────────────────────┐│
│  │                   mcp_ssh_server.py                     ││
│  │              (FastMCP Tool Definitions)                 ││
│  └─────────────────────────────────────────────────────────┘│
│                              │                               │
│  ┌─────────────────────────────────────────────────────────┐│
│  │                    ssh_client.py                        ││
│  │               (SSH Connection Manager)                  ││
│  └─────────────────────────────────────────────────────────┘│
│                              │                               │
│  ┌──────────┬──────────┬──────────┬──────────┬────────────┐ │
│  │ssh_ops_  │ssh_ops_  │ssh_ops_  │ssh_ops_  │ssh_ops_    │ │
│  │file.py   │directory │run.py    │task.py   │os.py       │ │
│  │          │.py       │          │          │            │ │
│  └──────────┴──────────┴──────────┴──────────┴────────────┘ │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    Remote Linux Server                       │
│                      (via Paramiko SSH)                      │
└─────────────────────────────────────────────────────────────┘
```

## Tool Categories

| Category | Prefix | Count | Description |
|----------|--------|-------|-------------|
| Connection | `ssh_conn_*` | 6 | Connection and session management |
| Host Config | `ssh_host_*` | 3 | Host configuration management |
| Commands | `ssh_cmd_*` | 6 | Command execution and history |
| Tasks | `ssh_task_*` | 3 | Background task management |
| Files | `ssh_file_*` | 12 | File operations |
| Directories | `ssh_dir_*` | 9 | Directory operations |
| Archives | `ssh_archive_*` | 2 | Archive operations |

## Quick Start

1. **Configure a host** in `~/.ssh_hosts.toml`:
   ```toml
   [admin@myserver.com]
   password = "secretpassword"
   port = 22
   alias = "prod"
   description = "Production server"
   ```

2. **Connect** using the tool:
   ```
   ssh_conn_connect(host_name="prod")
   ```

3. **Execute commands**:
   ```
   ssh_cmd_run(command="ls -la /var/log", use_sudo=True)
   ```

See the individual documentation files for detailed information on each topic.

## Documentation Index

- [Installation](15-installation.md) - Install from PyPI
- [Platform Compatibility](20-platform-compatibility.md) - Supported platforms and requirements
- [Host Configuration](30-host-configuration.md) - Host file format and options
- [Tools Reference](40-tools-reference.md) - Complete tool documentation
- [Command Execution](50-command-execution.md) - Command execution details
- [Process Management](60-process-management.md) - Handle IDs and process concepts
- [Logging](70-logging.md) - Logging configuration
- [Claude Desktop Setup](80-claude-desktop.md) - Claude Desktop integration
