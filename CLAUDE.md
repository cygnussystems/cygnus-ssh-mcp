# PR_MCP_SSH Project

## Test Environment

**IMPORTANT**: Tests run against a Debian 12 VM, NOT Docker.

- **Host**: 192.168.1.27
- **Port**: 22
- **User**: test
- **Password**: testpwd
- **Sudo Password**: testpwd (same as user password)

The `USE_VM = True` flag is hardcoded in `testing_mcp/conftest.py`.

## SSH Key Authentication Testing

The VM has SSH keys configured for key-based auth testing. Keys are stored locally at:

- `~/.ssh/test_vm_key` - Unencrypted key (no passphrase)
- `~/.ssh/test_vm_key_encrypted` - Encrypted key (passphrase: `testpassphrase123`)

### If VM is Recreated

If the VM needs to be rebuilt, recreate the keys and copy them to the VM:

```bash
# Generate unencrypted key
ssh-keygen -t ed25519 -f ~/.ssh/test_vm_key -N "" -C "test_vm_key"

# Generate encrypted key with passphrase
ssh-keygen -t ed25519 -f ~/.ssh/test_vm_key_encrypted -N "testpassphrase123" -C "test_vm_key_encrypted"

# Copy public keys to VM (will prompt for password: testpwd)
ssh-copy-id -i ~/.ssh/test_vm_key.pub test@192.168.1.27
ssh-copy-id -i ~/.ssh/test_vm_key_encrypted.pub test@192.168.1.27
```

Or copy manually using password auth:
```bash
cat ~/.ssh/test_vm_key.pub | ssh test@192.168.1.27 "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
cat ~/.ssh/test_vm_key_encrypted.pub | ssh test@192.168.1.27 "cat >> ~/.ssh/authorized_keys"
```

### Host Config Entries

The `~/.mcp_ssh_hosts.toml` should have entries like:

```toml
# Key-based auth (unencrypted key)
["test@192.168.1.27"]
keyfile = "~/.ssh/test_vm_key"
port = 22
sudo_password = "testpwd"
alias = "vm-key"

# Or with encrypted key
["test@192.168.1.27"]
keyfile = "~/.ssh/test_vm_key_encrypted"
key_passphrase = "testpassphrase123"
port = 22
sudo_password = "testpwd"
alias = "vm-encrypted"
```

## Running Tests

Run all tests (except specific exclusions):
```bash
python -m pytest testing_mcp/ -v
```

Run a single test:
```bash
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

## GitHub CLI

The `gh` CLI is installed but not in the bash PATH. Use full path:

```bash
"/c/Program Files/GitHub CLI/gh.exe" <command>
```

Example:
```bash
"/c/Program Files/GitHub CLI/gh.exe" repo list
```
