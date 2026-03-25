# Windows Support

## Overview

cygnus-ssh-mcp supports Windows Server as a target system via SSH. This document covers supported versions, requirements, and behavioral differences from Linux/macOS.

## Supported Windows Versions

| Windows Version | PowerShell | Status |
|-----------------|------------|--------|
| Windows Server 2022 | 5.1 | Fully supported |
| Windows Server 2019 | 5.1 | Fully supported |
| Windows Server 2016 | 5.0 | Fully supported |
| Windows 11 | 5.1 | Fully supported |
| Windows 10 | 5.0+ | Fully supported |
| Windows Server 2012 R2 | 4.0 | Not supported (requires WMF 5.1) |
| Windows 8.1 and earlier | < 5.0 | Not supported |

### Why PowerShell 5.0+?

The following PowerShell features require version 5.0 or later:

| Feature | Cmdlet | Used For |
|---------|--------|----------|
| Archive operations | `Compress-Archive`, `Expand-Archive` | Creating/extracting archives |
| Depth-limited listing | `Get-ChildItem -Depth` | Directory listings with max depth |

Connecting to a Windows system with PowerShell < 5.0 will result in an error:

```
PowerShell 4.x detected. This tool requires PowerShell 5.0 or later.
Windows Server 2016+, Windows 10+ have PowerShell 5.0+ built-in.
For older Windows versions, install Windows Management Framework (WMF) 5.1.
```

## Prerequisites

### 1. OpenSSH Server

Windows Server 2019+ includes OpenSSH as an optional feature. To enable:

```powershell
# Check if installed
Get-WindowsCapability -Online | Where-Object Name -like 'OpenSSH.Server*'

# Install OpenSSH Server
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0

# Start and enable the service
Start-Service sshd
Set-Service -Name sshd -StartupType 'Automatic'

# Confirm firewall rule exists
Get-NetFirewallRule -Name *ssh*
```

For Windows Server 2016, download OpenSSH from [Microsoft's GitHub releases](https://github.com/PowerShell/Win32-OpenSSH/releases).

### 2. Administrator Access

Unlike Linux where `sudo` elevates privileges per-command, Windows elevation works differently:

- **Elevated session**: Connect as a user in the Administrators group
- **Non-elevated session**: Limited to user-level operations

The `use_sudo` parameter on tools is ignored on Windows. If an operation requires admin rights, the SSH session must be connected as an Administrator.

## Behavioral Differences

### Path Separators

| Platform | Separator | Example |
|----------|-----------|---------|
| Linux/macOS | `/` | `/home/user/file.txt` |
| Windows | `\` | `C:\Users\user\file.txt` |

Tools accept Windows-style paths:

```
ssh_file_write with file_path: "C:\Users\admin\config.txt"
ssh_dir_list_advanced with path: "C:\Program Files"
```

### Archive Format

| Platform | Default Format |
|----------|----------------|
| Linux/macOS | `.tar.gz` |
| Windows | `.zip` |

The `ssh_archive_create` tool creates `.zip` files on Windows using `Compress-Archive`.

### Permissions Model

| Aspect | Linux/macOS | Windows |
|--------|-------------|---------|
| Permission format | Octal (755, 644) | ACLs |
| Owner/Group | UID/GID | SID-based |
| Elevation | `sudo` per command | Session-level |

The `ssh_file_stat` tool returns owner information but not Unix-style permission bits on Windows.

### Line Endings

Windows uses `\r\n` (CRLF) line endings. The tools handle this automatically:
- File writes preserve the content as-is
- File reads may include `\r\n` in output

### Temp Directory

| Platform | Default Temp Path |
|----------|------------------|
| Linux | `/tmp` |
| macOS | `/tmp` or `/var/folders/...` |
| Windows | `C:\Users\<user>\AppData\Local\Temp` |

## Tool-Specific Notes

### Command Execution (`ssh_cmd_run`)

Commands are executed via PowerShell:

```powershell
powershell -Command "your-command-here"
```

For CMD commands, prefix with `cmd /c`:

```
ssh_cmd_run with command: "cmd /c dir C:\Windows"
```

### Directory Operations

| Operation | Linux | Windows |
|-----------|-------|---------|
| List files | `find` | `Get-ChildItem` |
| Directory size | `du -sb` | `Measure-Object -Sum Length` |
| Search content | `grep -r` | `Select-String -Recurse` |

### File Operations

| Operation | Linux | Windows |
|-----------|-------|---------|
| Find pattern | `grep` | `Select-String` |
| File stats | `stat` | `Get-Item`, `Get-Acl` |
| Copy file | `cp` | `Copy-Item` |
| Move file | `mv` | `Move-Item` |

### Background Tasks

| Aspect | Linux | Windows |
|--------|-------|---------|
| Launch method | `nohup cmd &` | `Start-Process -NoNewWindow` |
| Check running | `kill -0 $pid` | `Get-Process -Id $pid` |
| Terminate | `kill -15` / `kill -9` | `Stop-Process [-Force]` |

### System Information (`ssh_conn_host_info`)

Windows system info is gathered via CIM/WMI:

| Info | Linux Source | Windows Source |
|------|--------------|----------------|
| CPU | `/proc/cpuinfo` | `Win32_Processor` |
| Memory | `free -m` | `Win32_OperatingSystem` |
| OS version | `/etc/os-release` | `Win32_OperatingSystem` |
| Disk | `df -h` | `Win32_LogicalDisk` |
| Network | `ip addr` | `Get-NetIPAddress` |

## Known Limitations

### 1. Unicode in Command Output

**Issue**: PowerShell over SSH doesn't properly encode UTF-8 output. Emojis and special characters may appear as `?` or garbled text. This is a fundamental Windows console encoding limitation—PowerShell uses the system's OEM code page (typically CP437 or CP1252) for stdout, not UTF-8.

**Affected**: Reading files via `ssh_cmd_run` with PowerShell commands like `Get-Content`.

**Not affected**: SFTP-based operations bypass the console entirely and work correctly with Unicode.

**Solution**: Use `ssh_file_read` to read file contents. This tool uses SFTP to transfer raw bytes and decodes them on the client side, completely avoiding the Windows console encoding problem. Works correctly with emojis, international characters, and any valid UTF-8 content.

### 2. No Per-Command Elevation

**Issue**: Windows doesn't have `sudo`. The `use_sudo` parameter is ignored.

**Workaround**: Connect as an Administrator user for operations requiring elevation.

### 3. Non-Empty Directory Removal

**Behavior**: When `ssh_dir_remove` is called with `recursive=False` on a non-empty directory, Windows will return an error immediately (same as Linux).

## Host Configuration Example

```toml
# Windows Server with password auth
["administrator@winserver.example.com"]
password = "SecurePassword123"
port = 22
alias = "win-prod"
description = "Windows production server"

# Windows with SSH key
["deploy@winserver.example.com"]
keyfile = "~/.ssh/id_ed25519"
port = 22
alias = "win-staging"
description = "Windows staging server"
```

Note: `sudo_password` is not used for Windows connections.

## Troubleshooting

### Connection Refused

Ensure OpenSSH Server is running:

```powershell
Get-Service sshd
Start-Service sshd
```

### Permission Denied

Verify the user is in the Administrators group for admin operations:

```powershell
net localgroup Administrators
```

### PowerShell Version Error

Check PowerShell version:

```powershell
$PSVersionTable.PSVersion
```

If below 5.0, install [WMF 5.1](https://www.microsoft.com/en-us/download/details.aspx?id=54616).

### Commands Not Found

Ensure PowerShell is the default shell for SSH. Check `C:\ProgramData\ssh\sshd_config`:

```
# Should NOT have this line, or it should point to PowerShell:
# Subsystem powershell C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe
```
