import pytest
import asyncio
import sys
import os
import logging
import json
import time

# Load test credentials from .env file
from dotenv import load_dotenv
env_path = os.path.join(os.path.dirname(__file__), '.env')
if os.path.exists(env_path):
    load_dotenv(env_path)
else:
    # Fall back to .env.example for CI or first-time setup
    example_path = os.path.join(os.path.dirname(__file__), '.env.example')
    if os.path.exists(example_path):
        load_dotenv(example_path)

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
# Set TEST_PLATFORM=windows|macos to run against those platforms, defaults to linux
TEST_PLATFORM = os.environ.get('TEST_PLATFORM', 'linux').lower()

if TEST_PLATFORM not in ('linux', 'windows', 'macos'):
    raise ValueError(f"TEST_PLATFORM must be 'linux', 'windows', or 'macos', got '{TEST_PLATFORM}'")

IS_WINDOWS = TEST_PLATFORM == 'windows'
IS_LINUX = TEST_PLATFORM == 'linux'
IS_MACOS = TEST_PLATFORM == 'macos'

# Platform-specific paths and commands
if IS_MACOS:
    ROOT_HOME = '/var/root'  # macOS root home directory
    ROOT_GROUP = 'wheel'     # macOS root group
elif IS_WINDOWS:
    ROOT_HOME = 'C:\\Windows\\System32'  # Not really used on Windows
    ROOT_GROUP = 'Administrators'
else:  # Linux
    ROOT_HOME = '/root'
    ROOT_GROUP = 'root'

# =============================================================================
# Platform-specific Configuration (loaded from .env)
# =============================================================================
if IS_WINDOWS:
    SSH_TEST_HOST = os.environ.get('WINDOWS_SSH_HOST')
    SSH_TEST_PORT = int(os.environ.get('WINDOWS_SSH_PORT', 22))
    SSH_TEST_USER = os.environ.get('WINDOWS_SSH_USER')
    SSH_TEST_PASSWORD = os.environ.get('WINDOWS_SSH_PASSWORD')
    TEST_WORKSPACE = f"C:\\Users\\{SSH_TEST_USER}\\mcp_test_workspace"
    PATH_SEP = '\\'
elif IS_MACOS:
    SSH_TEST_HOST = os.environ.get('MACOS_SSH_HOST')
    SSH_TEST_PORT = int(os.environ.get('MACOS_SSH_PORT', 22))
    SSH_TEST_USER = os.environ.get('MACOS_SSH_USER')
    SSH_TEST_PASSWORD = os.environ.get('MACOS_SSH_PASSWORD')
    TEST_WORKSPACE = f"/Users/{SSH_TEST_USER}/mcp_test_workspace"
    PATH_SEP = '/'
else:  # Linux (default)
    SSH_TEST_HOST = os.environ.get('LINUX_SSH_HOST')
    SSH_TEST_PORT = int(os.environ.get('LINUX_SSH_PORT', 22))
    SSH_TEST_USER = os.environ.get('LINUX_SSH_USER')
    SSH_TEST_PASSWORD = os.environ.get('LINUX_SSH_PASSWORD')
    TEST_WORKSPACE = f"/home/{SSH_TEST_USER}/mcp_test_workspace"
    PATH_SEP = '/'

# Validate required credentials are set
if not SSH_TEST_HOST or not SSH_TEST_USER or not SSH_TEST_PASSWORD:
    raise ValueError(
        f"Missing test credentials for platform '{TEST_PLATFORM}'. "
        f"Copy testing_mcp/.env.example to testing_mcp/.env and fill in your test server details."
    )

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


def setup_macos_workspace():
    """Create and clean workspace directory on macOS using paramiko directly."""
    import paramiko
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(SSH_TEST_HOST, port=SSH_TEST_PORT, username=SSH_TEST_USER, password=SSH_TEST_PASSWORD)

        workspace = f"/Users/{SSH_TEST_USER}/mcp_test_workspace"
        cmd = f"mkdir -p {workspace} && echo {SSH_TEST_PASSWORD} | sudo -S chown {SSH_TEST_USER}:staff {workspace} 2>/dev/null; rm -rf {workspace}/* 2>/dev/null; echo 'Workspace ready'"
        stdin, stdout, stderr = client.exec_command(cmd)
        result = stdout.read().decode().strip()
        logging.info(f"macOS workspace setup: {result}")
        client.close()
    except Exception as e:
        logging.error(f"Failed to setup macOS workspace: {e}")
        raise


def setup_workspace():
    """Setup workspace for the current platform."""
    if IS_WINDOWS:
        setup_windows_workspace()
    elif IS_MACOS:
        setup_macos_workspace()
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


def cleanup_command(path):
    """Return platform-appropriate command to delete a file or directory."""
    if IS_WINDOWS:
        # PowerShell command that works for both files and directories
        return f'powershell -Command "Remove-Item -Path \'{path}\' -Recurse -Force -ErrorAction SilentlyContinue"'
    else:
        return f"rm -rf {path}"


def cleanup_file_command(path):
    """Return platform-appropriate command to delete a file."""
    if IS_WINDOWS:
        return f'powershell -Command "Remove-Item -Path \'{path}\' -Force -ErrorAction SilentlyContinue"'
    else:
        return f"rm -f {path}"


def read_file_command(path):
    """Return platform-appropriate command to read a file."""
    if IS_WINDOWS:
        return f'powershell -Command "Get-Content -Path \'{path}\' -Raw -Encoding UTF8"'
    else:
        return f"cat {path}"


async def cleanup_remote_path(client, path):
    """Clean up a remote file or directory using the appropriate platform command."""
    cmd = cleanup_command(path)
    await client.call_tool("ssh_cmd_run", {"command": cmd, "io_timeout": 10.0})


# =============================================================================
# Independent Verification Helpers (bypass the MCP tool entirely)
# =============================================================================
# Tests that need to confirm ground truth on the remote host (e.g. "is the real
# process actually dead") shouldn't check that via the MCP tool's own
# ssh_cmd_run/ssh_task_launch machinery - if that machinery has a bug, the
# verification could share it, giving false confidence. These use a raw paramiko
# connection instead, same pattern as setup_linux_workspace/setup_windows_workspace
# above, so they're a genuinely independent oracle.
_paramiko_verify_client = None


def _get_paramiko_verify_client():
    """Lazily open (and cache) a raw paramiko connection, independent of the MCP
    tool's own SshClient."""
    global _paramiko_verify_client
    import paramiko
    if _paramiko_verify_client is not None:
        transport = _paramiko_verify_client.get_transport()
        if transport is not None and transport.is_active():
            return _paramiko_verify_client
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(SSH_TEST_HOST, port=SSH_TEST_PORT, username=SSH_TEST_USER, password=SSH_TEST_PASSWORD)
    _paramiko_verify_client = client
    return _paramiko_verify_client


def paramiko_verify(cmd: str) -> tuple:
    """Run a command via a raw paramiko connection, bypassing the MCP tool
    entirely. Returns (exit_code, stdout, stderr) - all str except exit_code."""
    client = _get_paramiko_verify_client()
    _, stdout, stderr = client.exec_command(cmd)
    exit_code = stdout.channel.recv_exit_status()
    out = stdout.read().decode('utf-8', errors='replace')
    err = stderr.read().decode('utf-8', errors='replace')
    return exit_code, out, err


def process_exists(pid: int, name: str = None) -> bool:
    """Check whether a PID is genuinely alive on the remote host (Linux/macOS).
    If `name` is given, also confirms the process's command name matches - guards
    against PID-reuse false positives (a dead pid getting recycled by an
    unrelated new process before the check runs)."""
    exit_code, out, _ = paramiko_verify(f"ps -o comm= -p {pid}")
    if exit_code != 0:
        return False
    return name in out if name is not None else True


def get_process_group(pid: int):
    """Look up the real process group ID for a PID (Linux/macOS) - NOT assumed
    from the pid itself. Verified live 2026-07-05: a ssh_task_launch sudo'd
    command's real PGID differs from its captured pid (the launch script that
    was the group's leader has already exited by the time the pid is captured),
    so never assume a fixed offset - always look this up live. Returns None if
    the pid doesn't exist."""
    exit_code, out, _ = paramiko_verify(f"ps -o pgid= -p {pid}")
    if exit_code != 0:
        return None
    stripped = out.strip()
    return int(stripped) if stripped.isdigit() else None


def process_group_is_alive(pgid: int) -> bool:
    """Check whether any process in a process group is still alive (Linux/macOS),
    via a plain `ps -eo pid,pgid` scan - avoids relying on `ps -g`/`-G`, whose
    exact semantics (session vs. group) differ between GNU and BSD ps."""
    exit_code, out, _ = paramiko_verify(f"ps -eo pid,pgid | awk '$2 == {pgid} {{print $1}}'")
    return exit_code == 0 and out.strip() != ""


def _close_paramiko_verify_client():
    global _paramiko_verify_client
    if _paramiko_verify_client is not None:
        try:
            _paramiko_verify_client.close()
        except Exception:
            pass
        _paramiko_verify_client = None


def pytest_sessionfinish(session, exitstatus):
    """Close the cached independent-verification connection at the end of the
    test session."""
    _close_paramiko_verify_client()


# =============================================================================
# Cross-Platform Command Helpers
# =============================================================================
def echo_command(message: str) -> str:
    """Return platform-appropriate echo command."""
    if IS_WINDOWS:
        # PowerShell Write-Output
        return f"powershell -Command \"Write-Output '{message}'\""
    else:
        return f"echo '{message}'"


def sleep_command(seconds: float) -> str:
    """Return platform-appropriate sleep command."""
    if IS_WINDOWS:
        return f"powershell -Command \"Start-Sleep -Seconds {seconds}\""
    else:
        return f"sleep {seconds}"


def sleep_then_echo(seconds: float, message: str) -> str:
    """Return platform-appropriate sleep followed by echo."""
    if IS_WINDOWS:
        return f"powershell -Command \"Start-Sleep -Seconds {seconds}; Write-Output '{message}'\""
    else:
        return f"sleep {seconds} && echo '{message}'"


def long_running_command(seconds: float, message: str = "Command completed") -> str:
    """Return a command that runs for specified seconds then outputs a message."""
    if IS_WINDOWS:
        return f"powershell -Command \"Write-Output 'Starting long process'; Start-Sleep -Seconds {seconds}; Write-Output '{message}'\""
    else:
        return f"echo 'Starting long process'; sleep {seconds}; echo '{message}'"


def multiline_echo_command(count: int) -> str:
    """Return platform-appropriate command to echo multiple numbered lines."""
    if IS_WINDOWS:
        # PowerShell: 1..5 | ForEach-Object { Write-Output "Line $_" }
        return f'powershell -Command "1..{count} | ForEach-Object {{ Write-Output \\"Line $_\\" }}"'
    else:
        return f"for i in $(seq 1 {count}); do echo \"Line $i\"; done"


def exit_with_code_command(code: int) -> str:
    """Return platform-appropriate command to exit with specific code."""
    if IS_WINDOWS:
        return f"powershell -Command \"exit {code}\""
    else:
        return f"exit {code}"


def failing_command() -> str:
    """Return a command that intentionally fails (for error handling tests)."""
    if IS_WINDOWS:
        return "powershell -Command \"exit 42\""
    else:
        return "exit 42"


def get_expected_exit_code_error() -> str:
    """Return the expected error message pattern for exit code 42."""
    return "exit code 42"


def noop_success_command() -> str:
    """Return a command that succeeds without producing output (like bash 'true')."""
    if IS_WINDOWS:
        return "powershell -Command \"exit 0\""
    else:
        return "true"


def multiline_printf_command(lines: list) -> str:
    """Return platform-appropriate command to output multiple specific lines."""
    if IS_WINDOWS:
        # PowerShell: output each line
        ps_lines = "; ".join([f"Write-Output '{line}'" for line in lines])
        return f'powershell -Command "{ps_lines}"'
    else:
        # Use printf for precise control
        escaped = "\\n".join(lines)
        return f"printf '{escaped}'"


def success_with_stderr_command(stderr_message: str) -> str:
    """Return a command that exits 0 (success) but also writes to stderr - for
    testing that stderr is still captured/returned on a successful command, not
    just on failures."""
    if IS_WINDOWS:
        return f"powershell -Command \"[Console]::Error.WriteLine('{stderr_message}'); exit 0\""
    else:
        return f"echo '{stderr_message}' >&2"


def silent_failing_command() -> str:
    """Return a command that fails but produces no output (for testing error cases)."""
    if IS_WINDOWS:
        # PowerShell command that fails silently
        return 'powershell -Command "Get-Item C:\\nonexistent_path_xyz 2>$null; exit 0"'
    else:
        return "ls /nonexistent_path_for_history_test_stderr > /dev/null 2>&1 || true"


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
skip_on_macos = pytest.mark.skipif(IS_MACOS, reason="Not supported on macOS")
windows_only = pytest.mark.skipif(not IS_WINDOWS, reason="Windows-only test")
linux_only = pytest.mark.skipif(not IS_LINUX, reason="Linux-only test")
macos_only = pytest.mark.skipif(not IS_MACOS, reason="macOS-only test")


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
