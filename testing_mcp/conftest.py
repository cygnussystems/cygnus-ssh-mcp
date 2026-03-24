import pytest
import asyncio
import sys
import os
import logging
import json
import time

# Add project src to path (must be before importing cygnus_ssh_mcp)
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
src_path = os.path.join(project_root, 'src')
sys.path.insert(0, src_path)

from cygnus_ssh_mcp.server import mcp

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logging.getLogger('paramiko').setLevel(logging.INFO)
logging.getLogger('PIL').setLevel(logging.WARNING)

# =============================================================================
# Platform Detection
# =============================================================================
# Set TEST_PLATFORM=windows to run against Windows, defaults to linux
TEST_PLATFORM = os.environ.get('TEST_PLATFORM', 'linux').lower()

if TEST_PLATFORM not in ('linux', 'windows'):
    raise ValueError(f"TEST_PLATFORM must be 'linux' or 'windows', got '{TEST_PLATFORM}'")

IS_WINDOWS = TEST_PLATFORM == 'windows'
IS_LINUX = TEST_PLATFORM == 'linux'

# =============================================================================
# Platform-specific Configuration
# =============================================================================
if IS_WINDOWS:
    # Windows Server VM
    SSH_TEST_HOST = os.environ.get('SSH_TEST_HOST', '192.168.1.218')
    SSH_TEST_PORT = int(os.environ.get('SSH_TEST_PORT', 22))
    SSH_TEST_USER = os.environ.get('SSH_TEST_USER', 'claude')
    SSH_TEST_PASSWORD = os.environ.get('SSH_TEST_PASSWORD', 'claudepwd')
    TEST_WORKSPACE = f"C:\\Users\\{SSH_TEST_USER}\\mcp_test_workspace"
    PATH_SEP = '\\'
else:
    # Linux VM (Debian 12)
    SSH_TEST_HOST = os.environ.get('SSH_TEST_HOST', '192.168.1.27')
    SSH_TEST_PORT = int(os.environ.get('SSH_TEST_PORT', 22))
    SSH_TEST_USER = os.environ.get('SSH_TEST_USER', 'test')
    SSH_TEST_PASSWORD = os.environ.get('SSH_TEST_PASSWORD', 'testpwd')
    TEST_WORKSPACE = f"/home/{SSH_TEST_USER}/mcp_test_workspace"
    PATH_SEP = '/'

# =============================================================================
# Workspace Setup Functions
# =============================================================================
def setup_linux_workspace():
    """Create and clean workspace directory on Linux VM using paramiko directly."""
    import paramiko
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(SSH_TEST_HOST, port=SSH_TEST_PORT, username=SSH_TEST_USER, password=SSH_TEST_PASSWORD)

        workspace = f"/home/{SSH_TEST_USER}/mcp_test_workspace"
        cmd = f"mkdir -p {workspace} && echo {SSH_TEST_PASSWORD} | sudo -S chown {SSH_TEST_USER}:{SSH_TEST_USER} {workspace} 2>/dev/null; rm -rf {workspace}/* 2>/dev/null; echo 'Workspace ready'"
        stdin, stdout, stderr = client.exec_command(cmd)
        result = stdout.read().decode().strip()
        logging.info(f"Linux workspace setup: {result}")
        client.close()
    except Exception as e:
        logging.error(f"Failed to setup Linux workspace: {e}")
        raise


def setup_windows_workspace():
    """Create and clean workspace directory on Windows VM using paramiko directly."""
    import paramiko
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(SSH_TEST_HOST, port=SSH_TEST_PORT, username=SSH_TEST_USER, password=SSH_TEST_PASSWORD)

        workspace_ps = TEST_WORKSPACE.replace('\\', '\\\\')
        cmd = f'''powershell -Command "if (Test-Path '{workspace_ps}') {{ Remove-Item -Path '{workspace_ps}' -Recurse -Force }}; New-Item -Path '{workspace_ps}' -ItemType Directory -Force | Out-Null; Write-Output 'Workspace ready'"'''
        stdin, stdout, stderr = client.exec_command(cmd)
        result = stdout.read().decode().strip()
        logging.info(f"Windows workspace setup: {result}")
        client.close()
    except Exception as e:
        logging.error(f"Failed to setup Windows workspace: {e}")
        raise


def setup_workspace():
    """Setup workspace for the current platform."""
    if IS_WINDOWS:
        setup_windows_workspace()
    else:
        setup_linux_workspace()


# Run workspace setup at module load time
setup_workspace()

# =============================================================================
# Test Environment Setup
# =============================================================================
async def setup_test_environment():
    """Setup test environment for the current platform."""
    logging.info(f"Test environment: {TEST_PLATFORM.upper()} at {SSH_TEST_HOST}:{SSH_TEST_PORT}")
    return


# =============================================================================
# Helper Functions
# =============================================================================
def extract_result_text(result):
    """Extract text from a tool result."""
    if hasattr(result, 'content') and len(result.content) > 0:
        return result.content[0].text
    elif isinstance(result, list) and len(result) > 0:
        if hasattr(result[0], 'text'):
            return result[0].text
    return None


async def is_ssh_connected(client):
    """Check if SSH is connected."""
    try:
        is_connected_result = await client.call_tool("ssh_conn_is_connected", {})
        result_text = extract_result_text(is_connected_result)
        if result_text:
            return json.loads(result_text)
        return False
    except Exception as e:
        logging.error(f"Error checking SSH connection: {e}")
        return False


async def disconnect_ssh(client):
    """Disconnect any existing SSH connection."""
    if await is_ssh_connected(client):
        logger = logging.getLogger("test_cleanup")
        logger.info("Disconnecting existing SSH connection")
        try:
            if mcp.ssh_client:
                mcp.ssh_client.close()
                mcp.ssh_client = None
                logger.info("SSH connection closed successfully")
        except Exception as e:
            logger.error(f"Error disconnecting SSH: {e}")


async def make_connection(client):
    """Ensure an SSH connection exists, creating one if needed."""
    if await is_ssh_connected(client):
        logging.info("SSH connection already established")
        return True

    logging.info(f"Adding {TEST_PLATFORM} server configuration for {SSH_TEST_USER}@{SSH_TEST_HOST}")
    add_host_params = {
        "user": SSH_TEST_USER,
        "host": SSH_TEST_HOST,
        "password": SSH_TEST_PASSWORD,
        "port": SSH_TEST_PORT,
        "sudo_password": SSH_TEST_PASSWORD
    }
    await client.call_tool("ssh_conn_add_host", add_host_params)

    host_key = f"{SSH_TEST_USER}@{SSH_TEST_HOST}"
    logging.info(f"Connecting to {TEST_PLATFORM} server: {host_key}")
    connect_result = await client.call_tool("ssh_conn_connect", {"host_name": host_key})
    result_text = extract_result_text(connect_result)
    if not result_text:
        logging.error("Failed to get connection result")
        return False

    connect_json = json.loads(result_text)
    if connect_json.get('status') == 'success':
        logging.info(f"SSH connection established to {connect_json.get('connected_to')}")
        return True
    else:
        logging.error(f"Failed to establish SSH connection: {connect_json}")
        return False


def remote_temp_path(base_name):
    """Generate a temporary path on the remote system."""
    timestamp = int(time.time())
    return f"{TEST_WORKSPACE}{PATH_SEP}{base_name}_{timestamp}"


def print_test_header(test_name):
    """Print a formatted test header."""
    print("\n" + "=" * 40)
    print(f"Running test [{TEST_PLATFORM.upper()}]: {test_name}")
    print("=" * 40)


def print_test_footer():
    """Print a formatted test footer."""
    print("\n" + "=" * 40)
    print("Test completed")
    print("=" * 40 + "\n")


# =============================================================================
# Skip Markers for Platform-Specific Tests
# =============================================================================
skip_on_windows = pytest.mark.skipif(IS_WINDOWS, reason="Not supported on Windows")
skip_on_linux = pytest.mark.skipif(IS_LINUX, reason="Not supported on Linux")
windows_only = pytest.mark.skipif(IS_LINUX, reason="Windows-only test")
linux_only = pytest.mark.skipif(IS_WINDOWS, reason="Linux-only test")


# =============================================================================
# Pytest Configuration
# =============================================================================
def pytest_configure(config):
    """Configure pytest."""
    config.addinivalue_line("markers", "asyncio: mark test as an asyncio test")
    config.addinivalue_line("markers", "windows: mark test as Windows-specific")
    config.addinivalue_line("markers", "linux: mark test as Linux-specific")
    if hasattr(config, '_inicache'):
        config._inicache["asyncio_default_fixture_loop_scope"] = "function"


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for each test session."""
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session", autouse=True)
async def mcp_test_environment():
    """Session-wide test environment setup and teardown."""
    try:
        await setup_test_environment()
        await asyncio.sleep(2)
        logging.info(f"Test environment ready [{TEST_PLATFORM.upper()}]. SSH: {SSH_TEST_HOST}:{SSH_TEST_PORT}")
        yield
    except Exception as e:
        logging.error(f"Error setting up test environment: {e}", exc_info=True)
        raise
