# macOS Support

## Overview

cygnus-ssh-mcp supports macOS as a target system via SSH, alongside Linux and
Windows. This document covers macOS-specific behavior and the BSD/GNU command
differences the implementation handles for you.

macOS is detected automatically at connect time (`os_type: 'macos'`) and routed to
dedicated `_Mac` operation classes (`SshOsOperations_Mac`, `SshFileOperations_Mac`,
`SshDirectoryOperations_Mac`) that use BSD-native commands instead of the GNU
coreutils variants Linux targets use - `sysctl`/`vm_stat`/`sw_vers` instead of
`/proc/*` and `/etc/os-release`, BSD `stat`/`find`/`du` flag syntax instead of GNU's.
None of this requires any configuration - it's purely based on the detected `os_type`.

## Prerequisites

- SSH access (built-in `sshd` on macOS, enabled via System Settings → General →
  Sharing → Remote Login, or `sudo systemsetup -setremotelogin on`)
- A user account with password or key-based auth configured the same way as any
  other host in `mcp_ssh_hosts.toml`
- `sudo` access follows the same password-based model as Linux (no macOS-specific
  elevation mechanism) - `ssh_conn_verify_sudo` and `use_sudo=True` work identically

## Behavioral Differences from Linux

### System Information (`ssh_conn_host_info`, `ssh_conn_status`)

| Info | Linux Source | macOS Source |
|------|--------------|----------------|
| CPU count/model | `/proc/cpuinfo` | `sysctl -n hw.ncpu`, `sysctl -n machdep.cpu.brand_string` |
| Memory | `free -m` | `hw.memsize` (total) + `vm_stat` (free/available pages) |
| Load average | `/proc/loadavg` | `sysctl -n vm.loadavg` |
| OS name/version | `/etc/os-release` | `sw_vers -productName` / `-productVersion` / `-buildVersion` |
| Network interfaces | `ip addr` | `ifconfig` |
| Disk | `df -h` | `df -h` + `mount` (filesystem type) |
| Date/time format | GNU `date --iso-8601=seconds` | BSD `date -u +%Y-%m-%dT%H:%M:%S%z` |

The `os_type` field is always the normalized value `"macos"`, not the raw `uname -s`
output (`"Darwin"`/`"darwin"`) - this matches the convention used for `"windows"`
elsewhere in the codebase.

### File Operations

macOS's BSD `stat` uses different format flags than Linux's GNU `stat` - this only
matters for `use_sudo=True` file operations that need to read permissions/ownership
(`ssh_file_replace_line`, `ssh_file_insert_lines_after_match`, and similar), which use
`stat -f '%Lp %u %g'` on macOS instead of GNU's `stat -c '%a %u %g'`. This is handled
automatically; no behavior difference from the caller's perspective.

### Directory Operations

BSD `find` has no `-printf` flag (unlike GNU `find`), so directory listing/search
tools (`ssh_dir_list_advanced`, `ssh_dir_search_glob`, `ssh_dir_copy`) combine `find`
with per-file `stat -f` calls instead. BSD `du` has no `-b` (bytes) flag, so
`ssh_dir_calc_size`/`ssh_dir_copy` sum file sizes via `find -type f -exec stat -f %z`
instead. Results are identical to the Linux tools; only the underlying remote command
differs.

### Command Execution (`ssh_cmd_run`)

Commands run through `bash -c`, same as Linux - no wrapper differences, no PID/exit
code caveats. Real remote PID capture and reliable exit codes work the same way they
do on Linux.

## Known Limitations

None specific to macOS beyond what's documented for Linux/all platforms generally
(see [50-command-execution.md](50-command-execution.md) and
[60-process-management.md](60-process-management.md)).
