# Platform Compatibility

## Overview

This SSH MCP server enables remote server management via SSH. This document describes platform compatibility for both the **client** (machine running the MCP server) and the **target** (remote machine being connected to).

## Compatibility Matrix

| Client (Local) | Target (Remote) | Status |
|----------------|-----------------|--------|
| Windows | Linux | ✅ Tested |
| macOS | Linux | ✅ Supported |
| Linux | Linux | ✅ Supported |
| Windows | macOS | ❌ Not supported |
| macOS | macOS | ❌ Not supported |
| Linux | macOS | ❌ Not supported |
| Any | BSD variants | ❌ Not supported |
| Any | Other Unix | ❌ Not supported |

## Client-Side Requirements

The MCP server can run on **Windows, macOS, or Linux**. Requirements:

- Python 3.x
- Paramiko (SSH library)
- Standard SSH connectivity to target

The client platform does not affect functionality since all operations use the Paramiko SSH library, which is cross-platform.

## Target Requirements

**Linux is the only supported target operating system.**

The server explicitly validates the remote OS and rejects non-Linux systems:

```python
if self.os_type != 'linux':
    raise SshError(f"Unsupported OS detected: {self.os_type}. Only Linux is supported.")
```

### Why Linux Only?

The implementation relies heavily on GNU/Linux-specific utilities and system paths that are not available or behave differently on other Unix-like systems.

#### GNU Utilities Used

| Utility | Linux (GNU) | BSD/macOS | Notes |
|---------|-------------|-----------|-------|
| `find -printf` | ✅ | ❌ | GNU extension, not in BSD find |
| `find -maxdepth` | ✅ | ❌ | BSD uses different syntax |
| `free -m` | ✅ | ❌ | Linux-only, macOS uses `vm_stat` |
| `ip addr` | ✅ | ❌ | Linux-only, macOS uses `ifconfig` |
| `df -T` | ✅ | ❌ | GNU coreutils only |

#### Linux-Specific Paths

| Path | Purpose | Alternative on macOS/BSD |
|------|---------|--------------------------|
| `/proc/cpuinfo` | CPU information | `sysctl`, `system_profiler` |
| `/proc/loadavg` | System load | `sysctl vm.loadavg` |
| `/etc/os-release` | OS identification | `sw_vers` |

### Affected Tools

The following tools use Linux-specific commands:

**Directory Operations:**
- `ssh_dir_search_glob` - Uses `find -printf`
- `ssh_dir_list_advanced` - Uses `find -printf`
- `ssh_dir_batch_delete_files` - Uses `find` with GNU options
- `ssh_dir_copy` - Uses `find` with `xargs`

**System Information:**
- `ssh_conn_host_info` - Reads `/proc/cpuinfo`, uses `free`
- Network interface enumeration - Uses `ip` command
- Disk information - Uses `df -T`

**File Operations:**
- Basic file operations (read, write, transfer) use SFTP and should work on any Unix system
- Pattern searching uses `grep`, which is mostly portable

## Market Context

Linux dominates the server market:

- ~80%+ of web servers run Linux
- Cloud providers (AWS, GCP, Azure) predominantly offer Linux
- Container workloads (Docker, Kubernetes) run on Linux
- 100% of top 500 supercomputers run Linux

For most use cases involving SSH access to remote servers, Linux target support covers the vast majority of real-world scenarios.

## Architecture

The codebase uses platform-specific operation classes:

```
ssh_ops_file.py      → SshFileOperations_Linux
ssh_ops_directory.py → SshDirectoryOperations_Linux
ssh_ops_run.py       → SshRunOperations_Linux
ssh_ops_task.py      → SshTaskOperations_Linux
ssh_ops_os.py        → SshOsOperations_Linux
```

This design allows adding support for additional platforms by creating new classes (e.g., `SshDirectoryOperations_Mac`) without modifying the existing Linux implementations.

## Future Considerations

To add macOS or BSD target support:

1. **Modify OS validation** in `ssh_client.py` to accept additional OS types
2. **Create platform-specific operation classes** (e.g., `SshDirectoryOperations_Mac`)
3. **Instantiate the correct class** based on detected OS type during connection
4. **Implement platform-appropriate commands** (e.g., `sysctl` instead of `/proc/cpuinfo`)

The existing architecture makes this straightforward - each new platform just needs its own operation classes.

Contributions for additional platform support are welcome.
