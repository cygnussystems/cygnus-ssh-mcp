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
Add a host configuration. Fails if the host already exists. Requires either
`password` or `keyfile` (or both).

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `host` | str | Yes | - | Hostname or IP address |
| `user` | str | Yes | - | SSH username |
| `password` | str | No | None | SSH password (required unless `keyfile` is given) |
| `port` | int | No | 22 | SSH port |
| `sudo_password` | str | No | None | Sudo password (defaults to SSH password) |
| `alias` | str | No | None | Short name for quick access |
| `description` | str | No | None | Human-readable description |
| `keyfile` | str | No | None | Path to SSH private key (required unless `password` is given) |
| `key_passphrase` | str | No | None | Passphrase for an encrypted key |

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

### ssh_host_use_config
Switch which host configuration TOML file all `ssh_host_*`/`ssh_conn_add_host`
tools operate against, for the rest of the session (not just one call) - the same
"one active thing at a time" model `ssh_conn_connect` uses for SSH connections,
applied to host config files instead. The alternate file must already exist (this
does not auto-create a missing file, unlike the server's own default config file).
Omit `config_path` to switch back to the default.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `config_path` | str | No | None | Path to an alternate host config TOML file. Omit/`""` to revert to the default |

**Returns:** `{'status': 'success'/'error', 'message'?, 'config_path'?, 'is_default'?, 'host_count'?, 'error'?}`

---

### ssh_host_list
List all configured hosts from whichever config file is currently active. Never
contains passwords, sudo passwords, or key passphrases - this is the only correct
way to see what's configured; never read the host config TOML file directly.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| (none) | - | - | - | - |

**Returns:** `{'hosts': [{'key', 'alias'?, 'description'?}, ...], 'config_path'}`

---

### ssh_host_update
Change one or more fields on an existing host (rotate a password, change the port,
etc.) without needing to read or hand-edit the config file. Only the fields you pass
are changed; pass an empty string `""` to explicitly clear a field. Prefer this over
`ssh_host_remove` + `ssh_conn_add_host`, which loses every field you don't
explicitly resupply.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `host_name` | str | Yes | - | Host key (`user@host`) or alias |
| `password` | str | No | None | New password. Omit to keep unchanged, `""` to clear |
| `port` | int | No | None | New SSH port. Omit to keep unchanged |
| `sudo_password` | str | No | None | New sudo password. Omit to keep unchanged, `""` to clear |
| `alias` | str | No | None | New alias. Omit to keep unchanged, `""` to clear |
| `description` | str | No | None | New description. Omit to keep unchanged, `""` to clear |
| `keyfile` | str | No | None | New SSH key path. Omit to keep unchanged, `""` to clear |
| `key_passphrase` | str | No | None | New key passphrase. Omit to keep unchanged, `""` to clear |

**Returns:** Operation status dictionary, including `updated_fields` (list of fields
actually changed)

---

### ssh_host_remove
Remove a host from configuration.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `host_name` | str | Yes | - | Host key (`user@host`) or alias |

**Returns:** Operation status dictionary

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
Execute a command on the remote host and block until it completes, an `io_timeout`
(silence) occurs, or a `runtime_timeout` (hard cap) occurs. See
[50-command-execution.md](50-command-execution.md) for full timeout semantics.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `command` | str | Yes | - | Command to execute |
| `io_timeout` | float | No | 60.0 | Inactivity timeout in seconds - does NOT kill the remote command |
| `runtime_timeout` | float | No | None | Total wall-clock cap in seconds - DOES attempt to kill the remote command |
| `use_sudo` | bool | No | False | Run with sudo privileges |
| `cwd` | str | No | None | Run this call in this directory (Linux/macOS only). Not remembered between calls; fails closed if the directory doesn't exist |

**Returns:** Dictionary with `status`, `output`, `exit_code`, `id` (the handle ID - NOT `handle_id`, despite `handle_id` being the parameter name other `ssh_cmd_*` tools use to accept it), `pid`, `cwd`, timestamps

**Status values:** `success`, `command_failed`, `cwd_not_found`, `io_timeout`, `runtime_timeout`, `sudo_required`, `busy`, `error`

---

### ssh_cmd_check_status
Wait, then check the status of a command started with `ssh_cmd_run`.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `handle_id` | int | Yes | - | Command handle ID (the `id` field from `ssh_cmd_run`'s response) |
| `wait_seconds` | float | No | 5.0 | Seconds to wait before checking |

**Returns:** Dictionary with `status`, `exit_code`, `pid`, output metadata

**Status values:** `completed` (finished, `exit_code` populated), `running` (still being
monitored), `killed` (the remote process was confirmed terminated - e.g. `runtime_timeout`
killed it, or a prior `ssh_cmd_kill` call found it already gone; `exit_code` is not known,
but treat this as terminal, same as `completed`), `completed_exit_code_unknown` (monitoring
previously stopped, e.g. a prior `io_timeout`, without a confirmed exit code, but a live
check now confirms the remote process is no longer running - terminal, but the real exit
code was never observed and cannot be recovered), `unknown_still_running` (monitoring
previously stopped and a live check confirms the remote command is still actually running -
not a failure, call this tool again to keep checking), `not_found` (handle doesn't exist -
handles don't survive reconnects)

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
Create a new file or overwrite/append to an existing file with specified content.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `file_path` | str | Yes | - | Path to write |
| `content` | str | Yes | - | Content to write |
| `append` | bool | No | False | Append instead of overwrite |
| `use_sudo` | bool | No | False | Use sudo |
| `mode` | int | No | None | File permissions to set after writing (octal, e.g. `0o644`) |
| `create_dirs` | bool | No | False | Create parent directories if they don't exist |

**Returns:** Dictionary with `success`, `file_path`, `bytes_written`, `mode`, `append`

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
Move or rename a file or directory.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `source` | str | Yes | - | Source path |
| `destination` | str | Yes | - | Destination path |
| `overwrite` | bool | No | False | Overwrite destination if it exists |
| `use_sudo` | bool | No | False | Use sudo |

**Returns:** Dictionary with `success`, `source`, `destination`

---

### ssh_file_find_lines_with_pattern
Search for lines matching a pattern.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `file_path` | str | Yes | - | File to search |
| `pattern` | str | Yes | - | Search pattern |
| `regex` | bool | No | False | Treat `pattern` as a regular expression (otherwise literal text) |
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
Replace a unique line in a file with a new line. The match must be exact (whitespace-trimmed) and unique in the file.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `file_path` | str | Yes | - | File path |
| `match_line` | str | Yes | - | Exact line content to match and replace |
| `new_line` | str | Yes | - | Replacement line |
| `use_sudo` | bool | No | False | Use sudo |
| `force` | bool | No | False | Force operation even if file can't be read (sudo only) |

**Returns:** Dictionary with `success`, `lines_written` (or `error` on failure)

---

### ssh_file_replace_line_multi
Replace a unique line with one or more new lines (or delete it, with an empty list).

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `file_path` | str | Yes | - | File path |
| `match_line` | str | Yes | - | Exact line content to match and replace |
| `new_lines` | list[str] | Yes | - | Lines to insert in place of the match (`[]` deletes the line) |
| `use_sudo` | bool | No | False | Use sudo |
| `force` | bool | No | False | Force operation even if file can't be read (sudo only) |

**Returns:** Dictionary with `success`, `lines_written` (or `error` on failure)

---

### ssh_file_insert_lines_after_match
Insert lines after a matching line.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `file_path` | str | Yes | - | File path |
| `match_line` | str | Yes | - | Line to match |
| `lines_to_insert` | list | Yes | - | Lines to insert |
| `use_sudo` | bool | No | False | Use sudo |
| `force` | bool | No | False | Force operation even if file can't be read (sudo only) |

**Returns:** Dictionary with `success`, `lines_inserted`

---

### ssh_file_delete_line_by_content
Delete a line matching a unique content string.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `file_path` | str | Yes | - | File path |
| `match_line` | str | Yes | - | Exact line content to match and delete |
| `use_sudo` | bool | No | False | Use sudo |
| `force` | bool | No | False | Force operation even if file can't be read (sudo only) |

**Returns:** Dictionary with `success` (or `error` on failure - no `lines_deleted` count is returned)

---

## Directory Operations (`ssh_dir_*`)

### ssh_dir_transfer
Transfer a directory between local and remote using archive-based transfer
(archives locally/remotely, transfers, extracts on the other side). Archive format
is chosen automatically by remote OS: `tar.gz` on Linux/macOS, `zip` on Windows.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `direction` | str | Yes | - | `"upload"` (local to remote) or `"download"` (remote to local) |
| `local_path` | str | Yes | - | Local directory path |
| `remote_path` | str | Yes | - | Remote directory path |
| `use_sudo` | bool | No | False | Use sudo for remote archive/extract operations (Linux/macOS only) |

**Returns:** Dictionary with `success`, `operation`, `local_path`, `remote_path`, `archive_format`, `files_transferred`, `bytes_transferred`

---

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
List directory contents recursively with detailed information.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | str | Yes | - | Directory path |
| `max_depth` | int | No | None | Maximum recursion depth (None for unlimited) |
| `use_sudo` | bool | No | False | Use sudo |

**Returns:** List of file/directory metadata dictionaries

---

### ssh_dir_search_glob
Recursively search for files matching a filename glob pattern.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | str | Yes | - | Base directory to search from |
| `pattern` | str | Yes | - | Glob pattern (e.g., `*.txt`) |
| `max_depth` | int | No | None | Maximum recursion depth (None for unlimited) |
| `include_dirs` | bool | No | False | Include matching directories in results |
| `use_sudo` | bool | No | False | Use sudo |

**Returns:** List of matching file/directory info dictionaries

---

### ssh_dir_search_files_content
Search file contents (grep-like).

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `dir_path` | str | Yes | - | Directory to search in |
| `pattern` | str | Yes | - | Text or pattern to search for |
| `regex` | bool | No | False | Treat `pattern` as a regular expression |
| `case_sensitive` | bool | No | True | Perform case-sensitive search |
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
Delete all files matching a pattern under a directory.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `path` | str | Yes | - | Base directory to search in |
| `pattern` | str | Yes | - | File pattern to match for deletion (e.g. `*.tmp`) |
| `dry_run` | bool | No | True | Preview deletion without actually deleting |
| `use_sudo` | bool | No | False | Use sudo |

**Returns:** Status with list of affected files

---

### ssh_dir_copy
Copy a directory recursively.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `source_path` | str | Yes | - | Source directory path |
| `destination_path` | str | Yes | - | Destination directory path |
| `overwrite` | bool | No | False | Overwrite existing files |
| `preserve_symlinks` | bool | No | True | Preserve symbolic links |
| `preserve_permissions` | bool | No | True | Preserve file permissions |
| `use_sudo` | bool | No | False | Use sudo |

**Returns:** Dictionary with copy operation details

---

## Archive Operations (`ssh_archive_*`)

### ssh_archive_create
Create a compressed archive from a directory.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `source_path` | str | Yes | - | Directory to archive |
| `archive_path` | str | Yes | - | Path for the created archive |
| `format` | str | No | "tar.gz" | Archive format |
| `use_sudo` | bool | No | False | Use sudo |

**Supported formats:** `tar.gz`, `tar` (Linux/macOS only). Windows always produces `.zip`
via `ssh_dir_transfer`/internal archive helpers, regardless of `format` - there is no
`format` choice on Windows and `zip` is not a valid value for this tool's `format` parameter.

**Returns:** Archive info dictionary

---

### ssh_archive_extract
Extract a tar or tar.gz archive to a directory (or a `.zip` on Windows).

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `archive_path` | str | Yes | - | Path to the archive file |
| `destination_path` | str | Yes | - | Directory to extract to |
| `overwrite` | bool | No | False | Overwrite existing files |
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
