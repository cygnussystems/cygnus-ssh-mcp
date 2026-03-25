# Platform Compatibility

## Overview

This SSH MCP server enables remote server management via SSH. This document describes platform compatibility for both the **client** (machine running the MCP server) and the **target** (remote machine being connected to).

## Compatibility Matrix

| Client (Local) | Target (Remote) | Status |
|----------------|-----------------|--------|
| Windows | Linux | Fully supported |
| macOS | Linux | Fully supported |
| Linux | Linux | Fully supported |
| Windows | macOS | Fully supported |
| macOS | macOS | Fully supported |
| Linux | macOS | Fully supported |
| Windows | Windows Server 2016+ | Fully supported |
| macOS | Windows Server 2016+ | Fully supported |
| Linux | Windows Server 2016+ | Fully supported |
| Any | Windows Server 2012 R2 | Not supported |
| Any | BSD variants | Not supported |

## Client-Side Requirements

The MCP server can run on **Windows, macOS, or Linux**. Requirements:

- Python 3.10+
- Paramiko >= 3.5.0
- Standard SSH connectivity to target

The client platform does not affect functionality since all operations use the Paramiko SSH library, which is cross-platform.

## Target Requirements by Platform

### Linux

All Linux distributions with SSH access are supported. No special requirements.

Tested distributions:
- Debian 11, 12
- Ubuntu 20.04, 22.04, 24.04
- CentOS 7, 8, 9
- Rocky Linux 8, 9
- Amazon Linux 2, 2023

### macOS

macOS 10.15 (Catalina) and later are supported. Remote Login must be enabled in System Preferences.

See [MACOS_COMPATIBILITY.md](MACOS_COMPATIBILITY.md) for macOS-specific details.

### Windows

Windows Server 2016 and later are supported. Requires:
- PowerShell 5.0 or later (built-in on supported versions)
- OpenSSH Server enabled

See [25-windows-support.md](25-windows-support.md) for Windows-specific details including:
- Supported versions and requirements
- Behavioral differences from Linux/macOS
- Known limitations
- Troubleshooting guide

## Architecture

The codebase uses platform-specific operation classes:

```
SshFileOperations
├── SshFileOperations_Linux
├── SshFileOperations_Mac
└── SshFileOperations_Win

SshDirectoryOperations
├── SshDirectoryOperations_Linux
├── SshDirectoryOperations_Mac
└── SshDirectoryOperations_Win

SshRunOperations
├── SshRunOperations_Linux (also used for macOS)
└── SshRunOperations_Win

SshTaskOperations
├── SshTaskOperations_Linux (also used for macOS)
└── SshTaskOperations_Win

SshOsOperations
├── SshOsOperations_Linux
├── SshOsOperations_Mac
└── SshOsOperations_Win
```

The correct operation class is automatically selected based on the detected OS during SSH connection.

## Platform Detection

When connecting, the server automatically detects the remote OS:

1. **Linux**: Detected via `uname -s` returning "Linux"
2. **macOS**: Detected via `uname -s` returning "Darwin"
3. **Windows**: Detected via `echo %OS%` returning "Windows_NT" or PowerShell availability

The detected OS type and subtype are available in connection status:

```json
{
  "os_type": "windows",
  "os_subtype": "windows_server_2019"
}
```

## Key Differences by Platform

| Feature | Linux | macOS | Windows |
|---------|-------|-------|---------|
| Shell | bash | bash/zsh | PowerShell |
| Elevation | `sudo` | `sudo` | Run as Administrator |
| Path separator | `/` | `/` | `\` |
| Archive format | `.tar.gz` | `.tar.gz` | `.zip` |
| Permissions | Unix octal | Unix octal | ACLs |
| Background tasks | `nohup &` | `nohup &` | `Start-Process` |

## Unsupported Platforms

The following are explicitly not supported:

- **FreeBSD, OpenBSD, NetBSD**: Different command syntax and utilities
- **Windows Server 2012 R2 and earlier**: Requires PowerShell 5.0+
- **Windows 8.1 and earlier**: Requires PowerShell 5.0+
- **Embedded Linux**: May lack required utilities
- **Solaris/illumos**: Different command syntax
