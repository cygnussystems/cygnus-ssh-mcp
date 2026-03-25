# PR_MCP_SSH Project

## Internal Documentation

Detailed internal docs are in the `docs_internal/` folder:
- **[docs_internal/TEST-INFRASTRUCTURE.md](docs_internal/TEST-INFRASTRUCTURE.md)** - Test VMs, platform matrix, full test procedures
- **[docs_internal/RELEASING.md](docs_internal/RELEASING.md)** - Release process and PyPI publishing

## Test Environment

Test credentials are configured in `testing_mcp/.env`.

**Setup:**
```bash
cp testing_mcp/.env.example testing_mcp/.env
# Edit .env with your test server credentials
```

**Supported platforms:** Linux, Windows, macOS (set `TEST_PLATFORM` env var)

## SSH Key Authentication Testing

For key-based auth testing, SSH keys should be configured:

- `~/.ssh/test_vm_key` - Unencrypted key (no passphrase)
- `~/.ssh/test_vm_key_encrypted` - Encrypted key

See [docs_internal/TEST-INFRASTRUCTURE.md](docs_internal/TEST-INFRASTRUCTURE.md) for full setup instructions.

## Running Tests

See [docs_internal/TEST-INFRASTRUCTURE.md](docs_internal/TEST-INFRASTRUCTURE.md) for full platform matrix and test procedures.

**Quick commands:**
```bash
# Linux target (default)
python -m pytest testing_mcp/ -v

# Windows target
TEST_PLATFORM=windows python -m pytest testing_mcp/ -v

# macOS target
TEST_PLATFORM=macos python -m pytest testing_mcp/ -v

# Single test
python -m pytest testing_mcp/test_tool__run.py::test_ssh_run_basic -v
```

## Test Files

- `test_tool__sudo_production.py` - Production sudo tests (run separately)
- `test_tool__file_unicode.py` - Unicode file handling tests (new)

## Project Structure

- `mcp_ssh_server.py` - Main MCP SSH server
- `ssh_client.py` - SSH client wrapper using paramiko
- `ssh_models.py` - Data models and exceptions
- `testing_mcp/` - Test files
- `testing_mcp/conftest.py` - Test fixtures and configuration

## Platform Support

### Supported Platforms

| Target OS | Shell | Status |
|-----------|-------|--------|
| Linux (all distros) | bash | Fully supported |
| macOS 10.15+ | bash/zsh | Fully supported |
| Windows Server 2016+ | PowerShell 5.0+ | Fully supported |
| Windows Server 2012 R2 | PowerShell 4.x | **Not supported** |

### Platform-Specific Caveats

**Windows:**
- **No sudo**: Windows doesn't have `sudo`. The `use_sudo` parameter is ignored. Connect as Administrator for elevated operations.
- **Unicode in command output**: PowerShell over SSH uses OEM code page (CP437/CP1252) for stdout, corrupting UTF-8 characters. Use `ssh_file_read` (SFTP-based) instead of `ssh_cmd_run` with `Get-Content` for reading files with Unicode.
- **PowerShell 5.0+ required**: Cmdlets like `Compress-Archive` and `Get-ChildItem -Depth` require PS 5.0+.
- **Archive format**: Windows uses `.zip` (via `Compress-Archive`), not `.tar.gz`.

**macOS:**
- `stat` command syntax differs from Linux (`-f` flags vs `-c` flags).

### Implementation Workarounds & Tricks

**1. Windows Unicode Fix (SFTP bypass)**
- Problem: PowerShell stdout uses OEM code page, corrupting Unicode
- Solution: `ssh_file_read` uses SFTP to read raw bytes, decodes client-side
- SFTP bypasses the console encoding layer entirely

**2. Windows Directory Removal Pre-check**
- Problem: `Remove-Item` without `-Recurse` hangs on non-empty directories (vs Linux `rmdir` which fails fast)
- Solution: Pre-check with `Get-ChildItem | Measure-Object` and fail early if not empty
- Location: `ops/file.py` → `SshFileOperations_Win.rmdir()`

**3. PowerShell Version Check at Connection**
- Problem: PS 4.x lacks required cmdlets
- Solution: Check `$PSVersionTable.PSVersion.Major` on connect, raise `SshError` if < 5
- Location: `client.py` → `_check_windows_powershell_version()`

**4. Platform-Specific Operation Classes**
- Architecture uses inheritance: `SshFileOperations` base → `_Linux`, `_Mac`, `_Win` subclasses
- OS detected at connection time, correct class instantiated automatically
- Each platform implements abstract methods with native commands

**5. Heredoc for Sudo Password**
- Linux/macOS: Pipe sudo password via heredoc to avoid command-line exposure
- Pattern: `cat <<'EOF' | sudo -S command`

### Platform-Specific Tests

```bash
# Run tests against specific platforms
TEST_PLATFORM=linux python -m pytest testing_mcp/ -v    # Linux (default)
TEST_PLATFORM=windows python -m pytest testing_mcp/ -v  # Windows
TEST_PLATFORM=macos python -m pytest testing_mcp/ -v    # macOS

# Unicode tests are Linux-only (marked with @linux_only decorator)
```

## GitHub CLI

The `gh` CLI is installed but not in the bash PATH. Use full path:

```bash
"/c/Program Files/GitHub CLI/gh.exe" <command>
```

Example:
```bash
"/c/Program Files/GitHub CLI/gh.exe" repo list
```
