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

# Windows Server test environment configuration
SSH_TEST_HOST = os.environ.get('SSH_WIN_HOST', '192.168.1.218')
SSH_TEST_PORT = int(os.environ.get('SSH_WIN_PORT', 22))
SSH_TEST_USER = os.environ.get('SSH_WIN_USER', 'claude')
SSH_TEST_PASSWORD = os.environ.get('SSH_WIN_PASSWORD', 'claudepwd')

# Test workspace directory on Windows
TEST_WORKSPACE = f"C:\\Users\\{SSH_TEST_USER}\\mcp_test_workspace"


def setup_win_workspace():
    """Create and clean workspace directory on Windows VM using paramiko directly."""
    import paramiko
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(SSH_TEST_HOST, port=SSH_TEST_PORT, username=SSH_TEST_USER, password=SSH_TEST_PASSWORD)

        # Create workspace directory and clean it using PowerShell
        workspace_ps = TEST_WORKSPACE.replace('\\', '\\\\')
        cmd = f'''powershell -Command "if (Test-Path '{workspace_ps}') {{ Remove-Item -Path '{workspace_ps}' -Recurse -Force }}; New-Item -Path '{workspace_ps}' -ItemType Directory -Force | Out-Null; Write-Output 'Workspace ready'"'''
        stdin, stdout, stderr = client.exec_command(cmd)
        result = stdout.read().decode().strip()
        logging.info(f"Windows workspace setup: {result}")
        client.close()
    except Exception as e:
        logging.error(f"Failed to setup Windows workspace: {e}")
        raise


# Run workspace setup at module load time
setup_win_workspace()


async def setup_test_environment():
    """Setup test environment for Windows."""
    logging.info(f"Windows test environment: {SSH_TEST_HOST}:{SSH_TEST_PORT}")
    return


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
    """Ensure an SSH connection exists to Windows server."""
    if await is_ssh_connected(client):
        logging.info("SSH connection already established")
        return True

    # Add host configuration
    logging.info(f"Adding Windows server configuration for {SSH_TEST_USER}@{SSH_TEST_HOST}")
    add_host_params = {
        "user": SSH_TEST_USER,
        "host": SSH_TEST_HOST,
        "password": SSH_TEST_PASSWORD,
        "port": SSH_TEST_PORT,
    }
    await client.call_tool("ssh_conn_add_host", add_host_params)

    # Connect to the Windows server
    host_key = f"{SSH_TEST_USER}@{SSH_TEST_HOST}"
    logging.info(f"Connecting to Windows server: {host_key}")
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


def pytest_configure(config):
    """Configure pytest."""
    config.addinivalue_line("markers", "asyncio: mark test as an asyncio test")
    config.addinivalue_line("markers", "windows: mark test as Windows-specific")


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
        logging.info(f"Windows test environment ready. Using SSH port: {SSH_TEST_PORT}")
        yield
    except Exception as e:
        logging.error(f"Error setting up test environment: {e}", exc_info=True)
        raise


def print_test_header(test_name):
    """Print a formatted test header."""
    print("\n" + "=" * 40)
    print(f"Running test: {test_name}")
    print("=" * 40)


def print_test_footer():
    """Print a formatted test footer."""
    print("\n" + "=" * 40)
    print("Test completed")
    print("=" * 40 + "\n")


def remote_temp_path(base_name):
    """Generate a temporary path on the Windows system."""
    timestamp = int(time.time())
    return f"{TEST_WORKSPACE}\\{base_name}_{timestamp}"
