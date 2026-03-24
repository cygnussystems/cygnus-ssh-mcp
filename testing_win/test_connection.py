"""Tests for Windows SSH connection and basic operations."""
import pytest
import json
import logging
from fastmcp import Client
from cygnus_ssh_mcp.server import mcp

from conftest import (
    make_connection, extract_result_text, print_test_header, print_test_footer,
    SSH_TEST_HOST, SSH_TEST_USER, mcp_test_environment
)

logger = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_windows_connection(mcp_test_environment):
    """Test connecting to Windows Server and verifying OS detection."""
    print_test_header("test_windows_connection")

    async with Client(mcp) as client:
        connected = await make_connection(client)
        assert connected, "Failed to connect to Windows server"

        # Get connection status
        status_result = await client.call_tool("ssh_conn_status", {})
        status_text = extract_result_text(status_result)
        assert status_text, "Failed to get status"

        status = json.loads(status_text)
        assert status.get('os_type') == 'windows', f"Expected Windows, got {status.get('os_type')}"
        assert SSH_TEST_USER in status.get('user', ''), f"User mismatch: {status.get('user')}"

        print(f"Connected to Windows: {status.get('os_type')}")
        print(f"User: {status.get('user')}")
        print(f"CWD: {status.get('cwd')}")

    print_test_footer()


@pytest.mark.asyncio
async def test_windows_verify_elevation(mcp_test_environment):
    """Test that elevation (admin) status is correctly detected."""
    print_test_header("test_windows_verify_elevation")

    async with Client(mcp) as client:
        connected = await make_connection(client)
        assert connected, "Failed to connect"

        # Verify sudo/elevation status
        verify_result = await client.call_tool("ssh_conn_verify_sudo", {})
        verify_text = extract_result_text(verify_result)
        assert verify_text, "Failed to get elevation status"

        verify = json.loads(verify_text)
        print(f"Elevation status: available={verify.get('available')}, passwordless={verify.get('passwordless')}")

        # The 'available' key indicates if elevated (admin) access is available
        assert 'available' in verify, "Missing 'available' key in response"

    print_test_footer()


@pytest.mark.asyncio
async def test_windows_host_info(mcp_test_environment):
    """Test getting detailed host information from Windows."""
    print_test_header("test_windows_host_info")

    async with Client(mcp) as client:
        connected = await make_connection(client)
        assert connected, "Failed to connect"

        # Get full host info
        info_result = await client.call_tool("ssh_conn_host_info", {})
        info_text = extract_result_text(info_result)
        assert info_text, "Failed to get host info"

        info = json.loads(info_text)

        # Verify Windows-specific info is present
        assert 'system' in info, "Missing 'system' key"
        system = info['system']

        # Check CPU info
        assert system.get('cpu_count') != 'n/a', f"CPU count not detected: {system.get('cpu_count')}"
        print(f"CPU: {system.get('cpu_count')} cores - {system.get('cpu_model')}")

        # Check memory info
        assert system.get('mem_total_mb') != 'n/a', f"Memory not detected: {system.get('mem_total_mb')}"
        print(f"Memory: {system.get('mem_total_mb')} MB total")

        # Check OS info
        assert 'Windows' in system.get('os_name', ''), f"OS name not Windows: {system.get('os_name')}"
        print(f"OS: {system.get('os_name')}")

        # Check disk info
        assert system.get('disk_total') != 'n/a', f"Disk not detected: {system.get('disk_total')}"
        print(f"Disk: {system.get('disk_total')} total, {system.get('disk_free')} free")

    print_test_footer()


@pytest.mark.asyncio
async def test_windows_run_command(mcp_test_environment):
    """Test running basic commands on Windows."""
    print_test_header("test_windows_run_command")

    async with Client(mcp) as client:
        connected = await make_connection(client)
        assert connected, "Failed to connect"

        # Run whoami
        result = await client.call_tool("ssh_cmd_run", {"command": "whoami"})
        result_text = extract_result_text(result)
        assert result_text, "Failed to run whoami"

        result_json = json.loads(result_text)
        assert result_json.get('exit_code') == 0, f"whoami failed: {result_json}"
        assert SSH_TEST_USER.lower() in result_json.get('output', '').lower(), f"User not in output: {result_json.get('output')}"
        print(f"whoami: {result_json.get('output').strip()}")

        # Run dir command
        result = await client.call_tool("ssh_cmd_run", {"command": "dir C:\\"})
        result_text = extract_result_text(result)
        result_json = json.loads(result_text)
        assert result_json.get('exit_code') == 0, f"dir failed: {result_json}"
        assert 'Windows' in result_json.get('output', ''), "Expected 'Windows' in dir output"
        print("dir C:\\ executed successfully")

        # Run PowerShell command
        result = await client.call_tool("ssh_cmd_run", {"command": "powershell -Command \"Get-Date\""})
        result_text = extract_result_text(result)
        result_json = json.loads(result_text)
        assert result_json.get('exit_code') == 0, f"PowerShell failed: {result_json}"
        print(f"PowerShell Get-Date: {result_json.get('output').strip()}")

    print_test_footer()
