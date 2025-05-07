import pytest
import asyncio
from test__fixtures import setup_test_environment, teardown_test_environment, get_mcp_client, ssh_client
from test_utils import print_test_header, print_test_footer

@pytest.mark.asyncio
async def test_ssh_status():
    """Test retrieving SSH connection status."""
    print_test_header("Testing 'ssh_status' tool")
    
    async with await get_mcp_client() as client:
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
        assert 'os_info' in system, "System info should include OS info"
        assert 'hardware_info' in system, "System info should include hardware info"
    
    print_test_footer()
