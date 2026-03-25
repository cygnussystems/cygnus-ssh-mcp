# macOS Compatibility Survey

This document surveys all Linux-specific code in cygnus-ssh-mcp that would need to be modified to support macOS targets.

## Executive Summary

The codebase uses GNU/Linux-specific commands throughout. macOS uses BSD variants of common tools (`stat`, `find`, `du`, `date`) which have different command-line syntax. Additionally, Linux-specific filesystem paths (`/proc/*`, `/sys/*`) don't exist on macOS.

**Estimated Effort**: Medium (2-3 days of focused work)

**Strategy**: Create `_Mac` suffix operation classes (e.g., `SshFileOperations_Mac`) alongside existing `_Linux` classes, with OS-aware instantiation in `client.py`.

---

## Current Blockers

### client.py:87-88
```python
if self.os_type != 'linux':
    raise SshError(f"Unsupported OS detected: {self.os_type}. Only Linux is supported.")
```

**Status**: Hard block - macOS connections are rejected at connect time.
**Solution**: Remove this check and create macOS-compatible operation classes.

---

## Incompatible Commands by File

### 1. os_ops.py - System Information

| Line | Command | Issue | macOS Alternative |
|------|---------|-------|-------------------|
| 98 | `/proc/cpuinfo` | Linux-only virtual filesystem | `sysctl -n hw.ncpu`, `sysctl -n machdep.cpu.brand_string` |
| 101-103 | `free -m` | Linux-only command | `vm_stat`, parse `pagesize` and page counts |
| 104 | `/proc/loadavg` | Linux-only virtual filesystem | `sysctl -n vm.loadavg` |
| 124-130 | `/etc/os-release` | Linux distro file | `sw_vers` command |
| 136 | `uname -r` | Works on macOS | **OK** |
| 137 | `uname -m` | Works on macOS | **OK** |
| 156-159 | `/sys/class/net` | Linux-only sysfs | `networksetup -listallhardwareports` or `ifconfig` |
| 157 | `ip -4 addr show` | Linux `iproute2` | `ifconfig` |
| 191-194 | `df -h /`, `df -T /` | Works on macOS | `df -T` doesn't exist, use `mount` for fs type |
| 213 | `date --iso-8601=seconds` | GNU date | `date -u +%Y-%m-%dT%H:%M:%S%z` |

**Affected Methods**:
- `hardware_info()` - Complete rewrite needed
- `os_info()` - Complete rewrite needed
- `network_info()` - Complete rewrite needed
- `disk_info()` - Minor changes
- `user_status()` - `date` format change only

### 2. file.py - File Operations

| Line | Command | Issue | macOS Alternative |
|------|---------|-------|-------------------|
| 727 | `stat -c '%a %u %g'` | GNU stat format | `stat -f '%Lp %u %g'` |

**Affected Methods**:
- `_replace_content_sudo()` - `stat` format change

**Note**: Most file operations use SFTP or portable commands (`grep`, `sed`, `cp`, `mv`, `rm`). These work on macOS.

### 3. directory.py - Directory Operations

| Line | Command | Issue | macOS Alternative |
|------|---------|-------|-------------------|
| 65 | `find -printf '%p\t%y\n'` | GNU find | `find` + `stat` per file, or use `ls -lR` parsing |
| 120 | `du -sb` | `-b` flag GNU-only | `du -sk` and multiply by 1024, or `find + stat` |
| 398 | `find -printf '%p\t%y\t%s\t%T@\t%m\t%u\t%g\n'` | GNU find | Complex workaround needed |
| 525 | `stat -c %s` | GNU stat | `stat -f %z` |
| 791 | `find -printf '%p\t%l\n'` | GNU find (symlinks) | `find -type l -exec readlink {} \;` |
| 821 | `du -sb` | Same as line 120 | Same solution |

**Affected Methods**:
- `search_files_recursive()` - `-printf` removal
- `calculate_directory_size()` - `du` flag change
- `list_directory_recursive()` - Major rewrite for `-printf`
- `create_archive_from_directory()` - `stat -c %s` change
- `copy_directory_recursive()` - `-printf` removal, `du -sb` change

### 4. run.py - Command Execution

| Line | Command | Issue | macOS Alternative |
|------|---------|-------|-------------------|
| 127, 135 | `bash -c`, heredoc `<<<` | Works on macOS | **OK** |

**Status**: Fully compatible with macOS.

### 5. task.py - Background Tasks

| Line | Command | Issue | macOS Alternative |
|------|---------|-------|-------------------|
| All | `/tmp/`, `kill -0`, `bash` | Works on macOS | **OK** |

**Status**: Fully compatible with macOS.

---

## Tool Compatibility Matrix

| Tool | Compatible | Notes |
|------|------------|-------|
| `ssh_conn_connect` | Yes | |
| `ssh_conn_status` | **No** | Uses `user_status()` with GNU `date` |
| `ssh_conn_host_info` | **No** | Uses `full_status()` with Linux-only commands |
| `ssh_conn_is_connected` | Yes | |
| `ssh_conn_verify_sudo` | Yes | |
| `ssh_host_list` | Yes | |
| `ssh_host_disconnect` | Yes | |
| `ssh_cmd_run` | Yes | |
| `ssh_cmd_history` | Yes | |
| `ssh_cmd_clear_history` | Yes | |
| `ssh_cmd_check_status` | Yes | |
| `ssh_cmd_output` | Yes | |
| `ssh_cmd_kill` | Yes | |
| `ssh_file_write` | Yes | Uses SFTP |
| `ssh_file_stat` | Yes | Uses SFTP |
| `ssh_file_find_lines_with_pattern` | Yes | Uses `grep` |
| `ssh_file_get_context_around_line` | Yes | Uses `grep` + `sed` |
| `ssh_file_replace_line` | **Partial** | `stat -c` in sudo mode |
| `ssh_file_replace_line_multi` | **Partial** | `stat -c` in sudo mode |
| `ssh_file_insert_lines_after_match` | **Partial** | `stat -c` in sudo mode |
| `ssh_file_delete_line_by_content` | **Partial** | `stat -c` in sudo mode |
| `ssh_file_copy` | Yes | Uses `cp` |
| `ssh_file_move` | Yes | Uses `mv` |
| `ssh_file_transfer` | Yes | Uses SFTP |
| `ssh_dir_mkdir` | Yes | Uses `mkdir -p` |
| `ssh_dir_remove` | Yes | Uses `rmdir` / `rm -rf` |
| `ssh_dir_list_files_basic` | Yes | Uses SFTP |
| `ssh_dir_list_advanced` | **No** | Uses `find -printf` |
| `ssh_dir_search_glob` | **No** | Uses `find -printf` |
| `ssh_dir_search_files_content` | Yes | Uses `find` + `grep` |
| `ssh_dir_calc_size` | **No** | Uses `du -sb` |
| `ssh_dir_copy` | **No** | Uses `find -printf`, `du -sb` |
| `ssh_dir_delete` | Yes | Uses `find -print`, `rm -rf` |
| `ssh_dir_batch_delete_files` | Yes | Uses `find -name` |
| `ssh_archive_create` | **Partial** | Uses `stat -c %s` |
| `ssh_archive_extract` | Yes | Uses `tar` |
| `ssh_task_launch` | Yes | |
| `ssh_task_status` | Yes | |
| `ssh_task_kill` | Yes | |

---

## Proposed Implementation Approach

### Phase 1: Create macOS Operation Classes

1. **Create new files** (or add classes to existing):
   - `SshOsOperations_Mac` in `os_ops.py`
   - `SshFileOperations_Mac` in `file.py`
   - `SshDirectoryOperations_Mac` in `directory.py`

2. **Update `client.py`** to instantiate correct classes based on `os_type`:
   ```python
   if self.os_type == 'linux':
       self.os_ops = SshOsOperations_Linux(self)
       self.file_ops = SshFileOperations_Linux(self)
       self.dir_ops = SshDirectoryOperations_Linux(self)
   elif self.os_type == 'macos':
       self.os_ops = SshOsOperations_Mac(self)
       self.file_ops = SshFileOperations_Mac(self)
       self.dir_ops = SshDirectoryOperations_Mac(self)
   ```

### Phase 2: Implement macOS Commands

#### os_ops.py - macOS Alternatives

```bash
# CPU count
sysctl -n hw.ncpu

# CPU model
sysctl -n machdep.cpu.brand_string

# Memory info (requires parsing)
vm_stat | grep 'Pages active'
sysctl -n hw.memsize  # Total bytes

# Load average
sysctl -n vm.loadavg

# OS info
sw_vers -productName
sw_vers -productVersion
uname -r  # Kernel

# Network interfaces
ifconfig | grep -E '^[a-z]'
ifconfig en0 | grep 'inet '

# Disk info
df -h /
diskutil info / | grep 'File System Personality'

# Date ISO format
date -u +%Y-%m-%dT%H:%M:%S%z
```

#### directory.py - macOS Alternatives

**Replace `find -printf`** with a shell loop:
```bash
# Instead of: find /path -printf '%p\t%y\n'
find /path -exec stat -f '%N\t%HT' {} \;

# Or use a loop:
find /path | while read f; do
  type=$(stat -f %HT "$f")
  echo "$f\t$type"
done
```

**Replace `du -sb`**:
```bash
# macOS: du -sk gives kilobytes
du -sk /path | awk '{print $1 * 1024}'

# Or more accurate:
find /path -type f -exec stat -f %z {} + | awk '{s+=$1} END {print s}'
```

**Replace `stat -c %s`**:
```bash
stat -f %z /path/to/file
```

### Phase 3: Testing

1. Set up a macOS VM or use a Mac for testing
2. Create test fixtures similar to the Linux test VM
3. Run full test suite against macOS target

---

## Alternative Approaches Considered

### 1. Use Python's `os` module remotely
**Rejected**: Would require a Python installation on the target and more complex execution.

### 2. Install GNU coreutils on macOS
**Rejected**: Changes target system requirements; not suitable for production servers.

### 3. Abstract to SFTP-only operations
**Rejected**: Would lose functionality; SFTP doesn't support all operations.

---

## Effort Estimate

| Component | Effort | Notes |
|-----------|--------|-------|
| `SshOsOperations_Mac` | 4 hours | Most complex due to `/proc` alternatives |
| `SshFileOperations_Mac` | 1 hour | Just `stat` format change |
| `SshDirectoryOperations_Mac` | 3 hours | Multiple `find -printf` replacements |
| `client.py` updates | 30 min | OS detection already exists |
| Testing | 4 hours | Needs macOS test environment |
| **Total** | ~12-15 hours | |

---

## Questions to Resolve

1. **Do we need Windows support too?** The codebase has placeholder `_Win` classes but no implementation. Windows would be a much larger effort.

2. **Minimum macOS version?** Some commands differ between macOS versions. Recommend targeting macOS 10.15+ (Catalina).

3. **Should we support both Intel and Apple Silicon?** CPU detection commands may differ.
