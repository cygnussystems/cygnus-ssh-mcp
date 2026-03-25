# Test Infrastructure

This document describes the test environment required for full platform testing of cygnus-ssh-mcp.

## Quick Start

```bash
# 1. Copy credentials template
cp testing_mcp/.env.example testing_mcp/.env

# 2. Edit with your test server details
# (IP addresses, usernames, passwords for each platform)

# 3. Install dev dependencies
pip install -e ".[dev]"

# 4. Run tests
python -m pytest testing_mcp/ -v                        # Linux (default)
TEST_PLATFORM=windows python -m pytest testing_mcp/ -v  # Windows
TEST_PLATFORM=macos python -m pytest testing_mcp/ -v    # macOS
```

## Test Credentials Configuration

Test server credentials are stored in `testing_mcp/.env` (gitignored).

### Setup

1. Copy the example file:
   ```bash
   cp testing_mcp/.env.example testing_mcp/.env
   ```

2. Edit `testing_mcp/.env` with your test server details:
   ```env
   # Linux Test Server
   LINUX_SSH_HOST=192.168.1.x
   LINUX_SSH_PORT=22
   LINUX_SSH_USER=testuser
   LINUX_SSH_PASSWORD=testpassword

   # Windows Test Server
   WINDOWS_SSH_HOST=192.168.1.x
   WINDOWS_SSH_PORT=22
   WINDOWS_SSH_USER=testuser
   WINDOWS_SSH_PASSWORD=testpassword

   # macOS Test Server
   MACOS_SSH_HOST=192.168.1.x
   MACOS_SSH_PORT=22
   MACOS_SSH_USER=testuser
   MACOS_SSH_PASSWORD=testpassword
   ```

## Required Virtual Machines

All VMs must be running and accessible before running tests.

### Linux Test Server

| Requirement | Details |
|-------------|---------|
| **OS** | Any Linux distribution (tested on Debian 12) |
| **SSH** | OpenSSH server running on port 22 |
| **User** | Test user with sudo access |
| **Purpose** | Primary test target |

### Windows Test Server

| Requirement | Details |
|-------------|---------|
| **OS** | Windows Server 2016+ or Windows 10+ |
| **SSH** | OpenSSH Server installed and running |
| **PowerShell** | 5.0+ (built-in on supported versions) |
| **User** | User in Administrators group |
| **Purpose** | Windows target testing |

### macOS Test Server

| Requirement | Details |
|-------------|---------|
| **OS** | macOS 10.15+ (Catalina or later) |
| **SSH** | Remote Login enabled (System Preferences) |
| **User** | Test user with admin access |
| **Purpose** | macOS target testing |

## Platform Test Matrix

| Client (runs tests) | Target (SSH to) | Status |
|---------------------|-----------------|--------|
| Windows | Linux | ✅ Working |
| Windows | Windows | ✅ Working |
| Linux | Linux | ✅ Working |
| Linux | Windows | ✅ Working |
| macOS | Linux | ⏳ Not yet tested |
| macOS | Windows | ⏳ Not yet tested |
| macOS | macOS | ⏳ Not yet tested |

## Running Tests

### By Platform Target

```bash
# Linux target (default)
python -m pytest testing_mcp/ -v

# Windows target
TEST_PLATFORM=windows python -m pytest testing_mcp/ -v

# macOS target
TEST_PLATFORM=macos python -m pytest testing_mcp/ -v
```

### Specific Test Files

```bash
# File operations
python -m pytest testing_mcp/test_tool__file_ops.py -v

# Directory operations
python -m pytest testing_mcp/test_tool__directory_ops.py -v

# Command execution
python -m pytest testing_mcp/test_tool__run.py -v

# Unicode tests (Linux only)
python -m pytest testing_mcp/test_tool__file_unicode.py -v
```

### Full Platform Matrix

Run from each client environment to test all combinations:

```bash
# Test all supported targets
python -m pytest testing_mcp/ -v                        # → Linux
TEST_PLATFORM=windows python -m pytest testing_mcp/ -v  # → Windows
TEST_PLATFORM=macos python -m pytest testing_mcp/ -v    # → macOS
```

## SSH Key Authentication Testing

For testing key-based authentication:

1. Generate test keys:
   ```bash
   # Unencrypted key
   ssh-keygen -t ed25519 -f ~/.ssh/test_vm_key -N "" -C "test_vm_key"

   # Encrypted key
   ssh-keygen -t ed25519 -f ~/.ssh/test_vm_key_encrypted -N "your_passphrase" -C "test_vm_key_encrypted"
   ```

2. Copy public keys to test servers:
   ```bash
   ssh-copy-id -i ~/.ssh/test_vm_key.pub user@host
   ssh-copy-id -i ~/.ssh/test_vm_key_encrypted.pub user@host
   ```

## Platform-Specific Notes

### Windows

- Unicode file tests are skipped (`@linux_only` decorator) due to PowerShell console encoding
- Use `ssh_file_read` for Unicode content (SFTP-based, bypasses encoding issues)
- `use_sudo` parameter is ignored (no sudo on Windows)
- User must be in Administrators group for elevated operations

### macOS

- `stat` command syntax differs from Linux (`-f` flags vs `-c` flags)
- Remote Login must be enabled in System Preferences

### Linux

- Full functionality supported
- Sudo operations require correct sudo password in .env

## Troubleshooting

### Missing Credentials Error
```
ValueError: Missing test credentials for platform 'linux'
```
**Fix:** Copy `.env.example` to `.env` and fill in your test server details.

### Connection Refused
- Verify VM is running
- Check SSH service is started (`systemctl status sshd`)
- Verify firewall allows port 22

### Permission Denied
- Check username/password in `.env`
- For Windows: ensure user is in Administrators group
- For Linux: verify sudo password is correct

### PowerShell Version Error (Windows)
```
PowerShell 4.x detected. This tool requires PowerShell 5.0 or later.
```
**Fix:** Install WMF 5.1 or upgrade to Windows Server 2016+.

### Unicode Corruption (Windows)
- Use `ssh_file_read` instead of `ssh_cmd_run` with `Get-Content`
- This is a known Windows SSH limitation, not a bug in this tool
