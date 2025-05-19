# Production Server Testing

This directory contains tests that can be run against actual production servers to verify functionality in real-world environments.

## Setting Up Production Sudo Tests

The production sudo tests in `test_tool__sudo_production.py` are designed to test sudo functionality against real Linux servers. These tests are disabled by default and will be skipped unless explicitly enabled.

### Environment Variables

To run the production sudo tests, you need to set the following environment variables:

```
PROD_SUDO_TEST_ENABLED=true
PROD_SSH_HOST=your-server-hostname
PROD_SSH_PORT=22
PROD_SSH_USER=your-username
PROD_SSH_PASSWORD=your-password
PROD_SSH_SUDO_PASSWORD=your-sudo-password
```

### Setting Environment Variables

#### Windows (Command Prompt)
```
set PROD_SUDO_TEST_ENABLED=true
set PROD_SSH_HOST=your-server-hostname
set PROD_SSH_PORT=22
set PROD_SSH_USER=your-username
set PROD_SSH_PASSWORD=your-password
set PROD_SSH_SUDO_PASSWORD=your-sudo-password
```

#### Windows (PowerShell)
```
$env:PROD_SUDO_TEST_ENABLED="true"
$env:PROD_SSH_HOST="your-server-hostname"
$env:PROD_SSH_PORT="22"
$env:PROD_SSH_USER="your-username"
$env:PROD_SSH_PASSWORD="your-password"
$env:PROD_SSH_SUDO_PASSWORD="your-sudo-password"
```

#### Linux/macOS
```
export PROD_SUDO_TEST_ENABLED=true
export PROD_SSH_HOST=your-server-hostname
export PROD_SSH_PORT=22
export PROD_SSH_USER=your-username
export PROD_SSH_PASSWORD=your-password
export PROD_SSH_SUDO_PASSWORD=your-sudo-password
```

### Running the Tests

After setting the environment variables, you can run the production sudo tests with:

```
pytest -xvs testing_mcp/test_tool__sudo_production.py
```

### Security Considerations

- **Never commit credentials to version control**
- Consider using a dedicated test server rather than a production server
- Use a user account with limited privileges for testing
- Clean up any test files or changes after testing
