# SSH Host Configuration File Documentation

## File Locations
The MCP server looks for host configurations in this order:
1. Explicit path provided via `--config` command-line parameter
2. `~/.ssh_hosts.toml` (User home directory)
3. `./ssh_hosts.toml` (Current working directory)

## File Format
The TOML file should contain host configurations using this structure:
```toml
[user@hostname]
password = "plaintext_password"  # Required for password authentication
port = 22                        # Optional (default: 22)
# keyfile = "/path/to/ssh_key"   # Optional alternative to password
```

## Example Configuration
```toml
[testuser@localhost]
password = "testpass"
port = 2222

[admin@production-server]
password = "secureProdPassword"
port = 2222
```

## Testing Configuration
For automated tests, ensure you have a test host configured:
```toml
[testuser@localhost]
password = "testpass"
port = 2222
```

If you're running tests in a Docker environment, make sure the SSH server is running on port 2222.
For local testing without an SSH server, use the mock-based tests in `test_mcp_status.py`.

## Security Best Practices
1. Set strict file permissions:
```bash
chmod 600 ~/.ssh_hosts.toml
```
2. Prefer SSH keys over passwords when possible
3. Never commit the config file to version control
4. Use environment variables for passwords in CI/CD systems

## Command Line Usage
```bash
# Use custom config location
python mcp_ssh_server.py --config /path/to/custom_hosts.toml
```
