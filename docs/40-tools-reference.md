# SSH MCP Server Tools Reference

This document provides a complete reference for all tools available in the SSH MCP Server.

## Tool Naming Convention

All tools follow a consistent naming pattern: `ssh_{category}_{action}`

| Prefix | Category |
|--------|----------|
| `ssh_conn_*` | Connection management |
| `ssh_host_*` | Host configuration |
| `ssh_cmd_*` | Command execution |
| `ssh_task_*` | Background tasks |
| `ssh_file_*` | File operations |
| `ssh_dir_*` | Directory operations |
| `ssh_archive_*` | Archive operations |

---

## Connection Management (`ssh_conn_*`)

### ssh_conn_is_connected
Check if there's an active SSH connection.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| (none) | - | - | - | - |

**Returns:** `bool` - True if connected

---

### ssh_conn_connect
Connect to a configured SSH host.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `host_name` | str | Yes | - | Host key (`user@host`) or alias |

**Returns:** Connection status dictionary

---

### ssh_conn_add_host
Add or update a host configuration.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `host` | str | Yes | - | Hostname or IP address |
| `user` | str | Yes | - | SSH username |
| `password` | str | Yes | - | SSH password |
| `port` | int | No | 22 | SSH port |
| `sudo_password` | str | No | None | Sudo password (defaults to SSH password) |
| `alias` | str | No | None | Short name for quick access |
| `description` | str | No | None | Human-readable description |

**Returns:** Operation status dictionary

---

### ssh_conn_status
Get current connection and system status.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| (none) | - | - | - | - |

**Returns:** Status dictionary with connection info, user, working directory

---

### ssh_conn_host_info
Get detailed system information from connected host.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| (none) | - | - | - | - |

**Returns:** Dictionary with CPU, memory, disk, network, and OS information

---

### ssh_conn_verify_sudo
Check if sudo access is available.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| (none) | - | - | - | - |

**Returns:** Dictionary with `available`, `passwordless`, `requires_password` keys

---

## Host Configuration (`ssh_host_*`)

### ssh_host_list
List all configured hosts.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| (none) | - | - | - | - |

**Returns:** Dictionary with hosts, count, and config file path

---

### ssh_host_remove
Remove a host from configuration.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `host_name` | str | Yes | - | Host key (`user@host`) or alias |

**Returns:** Operation status dictionary

---

### ssh_host_reload_config
Reload host configurations from disk.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| (none) | - | - | - | - |

**Returns:** Status dictionary with host count

---

### ssh_host_disconnect
Disconnect from the current host.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| (none) | - | - | - | - |

**Returns:** Disconnection status

---

## Command Execution (`ssh_cmd_*`)

### ssh_cmd_run
Execute a command on the remote host.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `command` | str | Yes | - | Command to execute |
| `io_timeout` | float | No | 60.0 | I/O timeout in seconds |
| `runtime_timeout` | float | No | None | Total runtime timeout |
| `use_sudo` | bool | No | False | Run with sudo privileges |

**Returns:** Dictionary with `status`, `output`, `exit_code`, `handle_id`, timestamps

**Status values:** `success`, `command_failed`, `io_timeout`, `runtime_timeout`, `busy`

---

### ssh_cmd_check_status
Check status of a running command.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `handle_id` | int | Yes | - | Command handle ID |
| `wait_seconds` | float | No | 0 | Seconds to wait before checking |

**Returns:** Status dictionary with running state and output

---

### ssh_cmd_kill
Terminate a running command.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `handle_id` | int | Yes | - | Command handle ID |
| `signal` | int | No | 15 | Signal to send (15=TERM, 9=KILL) |
| `force` | bool | No | True | Force kill if process doesn't exit |
| `wait_seconds` | float | No | 1.0 | Seconds to wait before force kill |

**Returns:** Kill result dictionary

---

### ssh_cmd_output
Retrieve output from a command.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `handle_id` | int | Yes | - | Command handle ID |
| `lines` | int | No | None | Number of lines to retrieve |

**Returns:** List of output lines

---

### ssh_cmd_history
Get command execution history.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `limit` | int | No | None | Max entries to return |
| `include_output` | bool | No | False | Include output snippets |
| `output_lines` | int | No | 3 | Lines per output snippet |
| `reverse` | bool | No | False | Reverse chronological order |
| `pattern` | str | No | None | Filter by command pattern |

**Returns:** List of command history entries

---

### ssh_cmd_clear_history
Clear command history.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| (none) | - | - | - | - |

**Returns:** Clear status dictionary

---

## Background Tasks (`ssh_task_*`)

### ssh_task_launch
Launch a command in the background.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `command` | str | Yes | - | Command to execute |
| `use_sudo` | bool | No | False | Run with sudo |
| `stdout_log` | str | No | Auto | Path for stdout log |
| `stderr_log` | str | No | Auto | Path for stderr log |
| `log_output` | bool | No | True | Whether to log output |

**Returns:** Dictionary with `command`, `pid`, `start_time`, log paths

---

### ssh_task_status
Check status of a background task.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `pid` | int | Yes | - | Process ID to check |

**Returns:** Dictionary with `pid`, `status`, `running`, `timestamp`

---

### ssh_task_kill
Terminate a background task.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `pid` | int | Yes | - | Process ID to kill |
| `signal` | int | No | 15 | Signal to send |
| `use_sudo` | bool | No | False | Use sudo for kill |
| `force` | bool | No | True | Force kill if needed |
| `wait_seconds` | float | No | 1.0 | Wait before force kill |

**Returns:** Dictionary with `pid`, `result`, `signal`, `force_kill_used`

**Result values:** `killed`, `already_exited`, `failed_to_kill`, `error`

---

## File Operations (`ssh_file_*`)

### ssh_file_stat
Get file or directory metadata.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | str | Yes | - | Path to file/directory |
| `use_sudo` | bool | No | False | Use sudo |

**Returns:** Dictionary with size, permissions, ownership, timestamps

---

### ssh_file_read
Read file contents directly via SFTP.

This tool reads raw bytes using SFTP and decodes them client-side, bypassing any shell or console encoding issues. **Recommended for reading files on Windows** where PowerShell's console encoding can corrupt Unicode characters.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `file_path` | str | Yes | - | Path to file to read |
| `encoding` | str | No | "utf-8" | Character encoding to use |
| `max_size` | int | No | 10MB | Maximum file size (0 for no limit) |

**Returns:** Dictionary with `success`, `content`, `size`, `encoding`

**Why use this instead of `ssh_cmd_run` with `cat`/`Get-Content`?**
- Works correctly with Unicode on ALL platforms including Windows
- Bypasses Windows PowerShell's OEM code page encoding problem
- More efficient for binary-safe file transfer
- No shell escaping issues with special characters in content

---

### ssh_file_write
Write content to a file.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `file_path` | str | Yes | - | Path to write |
| `content` | str | Yes | - | Content to write |
| `use_sudo` | bool | No | False | Use sudo |
| `append` | bool | No | False | Append instead of overwrite |
| `create_dirs` | bool | No | False | Create parent directories |
| `encoding` | str | No | "utf-8" | File encoding |

**Returns:** Dictionary with `success`, `path`, `bytes_written`

---

### ssh_file_transfer
Transfer files between local and remote.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `direction` | str | Yes | - | "upload" or "download" |
| `local_path` | str | Yes | - | Local file path |
| `remote_path` | str | Yes | - | Remote file path |
| `use_sudo` | bool | No | False | Use sudo for remote operations |

**Returns:** Transfer status dictionary

---

### ssh_file_copy
Copy a file on the remote system.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `source_path` | str | Yes | - | Source file path |
| `destination_path` | str | Yes | - | Destination path |
| `use_sudo` | bool | No | False | Use sudo |

**Returns:** Dictionary with `success`, `source`, `destination`

---

### ssh_file_move
Move or rename a file.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `source` | str | Yes | - | Source path |
| `destination` | str | Yes | - | Destination path |
| `use_sudo` | bool | No | False | Use sudo |

**Returns:** Dictionary with `success`, `source`, `destination`

---

### ssh_file_find_lines_with_pattern
Search for lines matching a pattern.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `file_path` | str | Yes | - | File to search |
| `pattern` | str | Yes | - | Search pattern (regex) |
| `use_sudo` | bool | No | False | Use sudo |

**Returns:** Dictionary with `total_matches`, `matches` (list of line info)

---

### ssh_file_get_context_around_line
Get surrounding context for a line.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `file_path` | str | Yes | - | File path |
| `match_line` | str | Yes | - | Line to find |
| `context` | int | No | 3 | Lines of context |
| `use_sudo` | bool | No | False | Use sudo |

**Returns:** Dictionary with `match_found`, `match_line_number`, `context_block`

---

### ssh_file_replace_line
Replace a single line in a file.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `file_path` | str | Yes | - | File path |
| `match_line` | str | Yes | - | Line to replace |
| `new_line` | str | Yes | - | Replacement line |
| `use_sudo` | bool | No | False | Use sudo |

**Returns:** Dictionary with `success`, `lines_replaced`

---

### ssh_file_replace_line_multi
Replace multiple lines (block replacement).

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `file_path` | str | Yes | - | File path |
| `old_block` | str | Yes | - | Text block to find |
| `new_block` | str | Yes | - | Replacement block |
| `use_sudo` | bool | No | False | Use sudo |

**Returns:** Dictionary with `success`, `replacements`

---

### ssh_file_insert_lines_after_match
Insert lines after a matching line.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `file_path` | str | Yes | - | File path |
| `match_line` | str | Yes | - | Line to match |
| `lines_to_insert` | list | Yes | - | Lines to insert |
| `use_sudo` | bool | No | False | Use sudo |

**Returns:** Dictionary with `success`, `lines_inserted`

---

### ssh_file_delete_line_by_content
Delete lines matching content.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `file_path` | str | Yes | - | File path |
| `line_content` | str | Yes | - | Content to match |
| `use_sudo` | bool | No | False | Use sudo |

**Returns:** Dictionary with `success`, `lines_deleted`

---

## Directory Operations (`ssh_dir_*`)

### ssh_dir_mkdir
Create a directory.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | str | Yes | - | Directory path |
| `use_sudo` | bool | No | False | Use sudo |
| `mode` | int | No | 0o755 | Directory permissions |

**Returns:** Status dictionary

---

### ssh_dir_remove
Remove a directory.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | str | Yes | - | Directory path |
| `use_sudo` | bool | No | False | Use sudo |
| `recursive` | bool | No | False | Remove recursively |

**Returns:** Status dictionary

---

### ssh_dir_list_files_basic
List directory contents.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | str | Yes | - | Directory path |

**Returns:** List of filenames

---

### ssh_dir_list_advanced
List directory with detailed metadata.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | str | Yes | - | Directory path |
| `pattern` | str | No | None | Filter pattern |
| `recursive` | bool | No | False | Include subdirectories |
| `use_sudo` | bool | No | False | Use sudo |

**Returns:** List of file metadata dictionaries

---

### ssh_dir_search_glob
Search for files using glob patterns.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `base_path` | str | Yes | - | Starting directory |
| `pattern` | str | Yes | - | Glob pattern (e.g., `*.txt`) |
| `max_depth` | int | No | None | Maximum search depth |
| `use_sudo` | bool | No | False | Use sudo |

**Returns:** List of matching file paths

---

### ssh_dir_search_files_content
Search file contents (grep-like).

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `directory` | str | Yes | - | Directory to search |
| `pattern` | str | Yes | - | Search pattern |
| `file_pattern` | str | No | None | Filter by filename |
| `recursive` | bool | No | True | Search recursively |
| `use_sudo` | bool | No | False | Use sudo |

**Returns:** List of matches with file, line number, content

---

### ssh_dir_calc_size
Calculate directory size.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | str | Yes | - | Directory path |
| `use_sudo` | bool | No | False | Use sudo |

**Returns:** Dictionary with size in bytes and human-readable format

---

### ssh_dir_delete
Delete a directory and contents.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | str | Yes | - | Directory path |
| `use_sudo` | bool | No | False | Use sudo |
| `dry_run` | bool | No | True | Preview without deleting |

**Returns:** Status dictionary (set `dry_run=False` to actually delete)

---

### ssh_dir_batch_delete_files
Batch delete files matching pattern.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `directory` | str | Yes | - | Directory path |
| `pattern` | str | Yes | - | File pattern to match |
| `use_sudo` | bool | No | False | Use sudo |
| `dry_run` | bool | No | True | Preview without deleting |

**Returns:** Status with list of affected files

---

### ssh_dir_copy
Copy a directory recursively.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `source` | str | Yes | - | Source directory |
| `destination` | str | Yes | - | Destination path |
| `use_sudo` | bool | No | False | Use sudo |

**Returns:** Status dictionary

---

## Archive Operations (`ssh_archive_*`)

### ssh_archive_create
Create a compressed archive.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `source_path` | str | Yes | - | Path to archive |
| `archive_path` | str | Yes | - | Output archive path |
| `format` | str | No | "tar.gz" | Archive format |
| `use_sudo` | bool | No | False | Use sudo |

**Supported formats:** `tar.gz`, `tar.bz2`, `zip`

**Returns:** Archive info dictionary

---

### ssh_archive_extract
Extract an archive.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `archive_path` | str | Yes | - | Archive file path |
| `destination` | str | Yes | - | Extraction destination |
| `use_sudo` | bool | No | False | Use sudo |

**Returns:** Extraction status dictionary

---

## Common Parameters

### use_sudo
Many tools support the `use_sudo` parameter for privileged operations:
- File operations in protected directories (`/etc`, `/root`, `/opt`)
- Process management for other users' processes
- Directory operations in system locations

The sudo password is taken from the host configuration.

### Timeouts
- `io_timeout`: Maximum time to wait for I/O activity
- `runtime_timeout`: Maximum total execution time
- `wait_seconds`: Time to wait before checking/killing

### dry_run
Destructive operations (`ssh_dir_delete`, `ssh_dir_batch_delete_files`) default to `dry_run=True`. Set to `False` to actually perform the operation.

---

## Status Codes

Common status values returned by tools:

| Status | Meaning |
|--------|---------|
| `success` | Operation completed successfully |
| `error` | General error occurred |
| `command_failed` | Command exited with non-zero code |
| `io_timeout` | No output activity within timeout |
| `runtime_timeout` | Total runtime exceeded limit |
| `busy` | Another command is running |
| `killed` | Process was terminated |
| `already_exited` | Process already finished |
