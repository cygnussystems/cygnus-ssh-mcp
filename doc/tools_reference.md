# SSH Management Tools Reference

## Connection Management Tools

| Tool | Arguments | Returns | Description |
|------|-----------|---------|-------------|
| `ssh_is_connected` | None | `bool` | Checks active SSH connection status |
| `ssh_connect` | `host_name: str` | Connection status dict | Connects using pre-configured host |
| `ssh_add_host` | `name, host, user, password, port=22` | Operation status | Adds/updates host configuration |

## Command Execution Tools

| Tool | Arguments | Returns | Description |
|------|-----------|---------|-------------|
| `ssh_cmd_run` | `command, io_timeout=60, runtime_timeout=None, sudo=False` | Command result dict | Executes command with timeout handling |
| `ssh_cmd_output` | `handle_id, lines=None` | List of output lines | Retrieves command output by handle ID |
| `ssh_cmd_history` | `limit=None, include_output=False, output_lines=3, reverse=False` | Command history list | Gets execution history with output snippets |
| `ssh_cmd_check` | `handle_id, wait_seconds=5` | Status dict | Checks command status after waiting |
| `ssh_cmd_kill` | `handle_id, signal=15, force=True` | Kill result dict | Terminates command by handle ID |

## File Operations Tools

| Tool | Arguments | Returns | Description |
|------|-----------|---------|-------------|
| `ssh_file_transfer` | `direction, local_path, remote_path, sudo=False` | Transfer status | Uploads/downloads files |
| `ssh_replace_line` | `path, old_line, new_line, count=1, sudo=False` | Operation status | Replaces text lines in file |
| `ssh_replace_block` | `path, old_block, new_block, sudo=False` | Operation status | Replaces text block in file |
| `ssh_stat` | `path` | File metadata dict | Gets file/directory status info |

## Directory Operations Tools

| Tool | Arguments | Returns | Description |
|------|-----------|---------|-------------|
| `ssh_mkdir` | `path, sudo=False, mode=0o755` | Operation status | Creates directory |
| `ssh_rmdir` | `path, sudo=False, recursive=False` | Operation status | Removes directory |
| `ssh_listdir` | `path` | File list | Lists directory contents |
| `ssh_search_files` | `path, pattern, max_depth=None, include_dirs=False` | Match list | Recursive file search |
| `ssh_directory_size` | `path` | Size info dict | Calculates directory size |

## Process Management Tools

| Tool | Arguments | Returns | Description |
|------|-----------|---------|-------------|
| `ssh_launch_task` | `command, sudo=False, stdout_log=None, stderr_log=None` | Task info dict | Starts background task |
| `ssh_task_status` | `pid` | Status dict | Checks task status |
| `ssh_task_kill` | `pid, signal=15, force=True` | Kill result dict | Terminates background task |

## Advanced Operations

| Tool | Arguments | Returns | Description |
|------|-----------|---------|-------------|
| `ssh_conn_status` | None | System status dict | Gets connection & system info |
| `ssh_verify_sudo` | None | `bool` | Checks sudo access |
| `ssh_create_archive` | `source_path, archive_path, format="tar.gz"` | Archive info | Creates compressed archive |
| `ssh_search_content` | `path, pattern, regex=False, case_sensitive=True` | Match list | Searches file contents |

## Key Argument Types
- **sudo**: Use privileged operations (default: False)
- **timeout** values: In seconds (float)
- **handle_id**: Unique command identifier
- **signal**: UNIX signal number (15=TERM, 9=KILL)

## Status Dictionary Structure
```python
{
    "status": "success|error|timeout...",
    "timestamp": "ISO8601 timestamp",
    # Additional fields vary by tool
}
```

To view this documentation:
```bash
start doc/tools_reference.md
```
