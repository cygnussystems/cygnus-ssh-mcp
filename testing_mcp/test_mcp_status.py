import pytest
import asyncio
from test_mcp_fixtures import setup_test_environment, teardown_test_environment, ssh_client
from conftest import print_test_header, print_test_footer

@pytest.mark.asyncio
async def test_ssh_status(mcp_client):
    """Test retrieving SSH connection status."""
    print_test_header("Testing 'ssh_status' tool")
    
    # Debug the client type
    print(f"Client type: {type(mcp_client)}")
    
    # Get the actual client by awaiting the async generator
    client = await anext(mcp_client.__aiter__())
    
    status_result = await client.call_tool("ssh_status", {})
    
    print(f"Status result: {status_result}")
    
    # Verify the result structure
    assert 'connection' in status_result, "Result should include connection info"
    assert 'system' in status_result, "Result should include system info"
    
    # Check connection details
    conn = status_result['connection']
    assert conn['host'] == ssh_client.host, f"Host mismatch: {conn['host']} != {ssh_client.host}"
    
    # Check system details
    system = status_result['system']
    assert 'os_name' in system, "System info should include OS name"
    assert 'cpu_count' in system, "System info should include CPU count"
    
    print_test_footer()
