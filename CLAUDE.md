# PR_MCP_SSH Project

## Test Environment

**IMPORTANT**: Tests run against a Debian 12 VM, NOT Docker.

- **Host**: 192.168.1.27
- **Port**: 22
- **User**: test
- **Password**: testpwd
- **Sudo Password**: testpwd (same as user password)

The `USE_VM = True` flag is hardcoded in `testing_mcp/conftest.py`.

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
