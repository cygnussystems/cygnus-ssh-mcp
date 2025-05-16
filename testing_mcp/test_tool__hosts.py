import pytest
import json
import logging
import time
from conftest import print_test_header, print_test_footer
from mcp_ssh_server import mcp
from fastmcp import Client

# Configure logging
logger = logging.getLogger(__name__)

@pytest.mark.asyncio
async def test_ssh_host_lifecycle(mcp_test_environment):
    """Test adding, listing, and preventing duplicate hosts."""
    print_test_header("Testing host management tools")
    logger.info("Starting SSH host lifecycle test")
    
    async with Client(mcp) as client:
        try:
            # Test initial host list
            list_result = await client.call_tool("ssh_host_list", {})
            initial_hosts = json.loads(list_result[0].text)
            assert isinstance(initial_hosts, list), "Host list should be a list"
            logger.info(f"Initial hosts: {initial_hosts}")

            # Test adding new host
            test_host = "testuser@testhost"
            add_params = {
                "user": "testuser",
                "host": "testhost", 
                "password": "testpass",
                "port": 2222
            }
            
            # Add new host
            add_result = await client.call_tool("ssh_conn_add_host", add_params)
            add_json = json.loads(add_result[0].text)
            assert add_json['status'] == 'success', f"Add host failed: {add_json}"
            assert add_json['key'] == test_host, "Host key mismatch"
            logger.info(f"Successfully added host: {test_host}")

            # Verify host appears in list
            list_result_after_add = await client.call_tool("ssh_host_list", {})
            updated_hosts = json.loads(list_result_after_add[0].text)
            assert test_host in updated_hosts, "New host not in list"
            logger.info(f"Updated hosts list: {updated_hosts}")

            # Test duplicate prevention
            duplicate_result = await client.call_tool("ssh_conn_add_host", add_params)
            duplicate_json = json.loads(duplicate_result[0].text)
            assert duplicate_json['status'] == 'error', "Duplicate host should error"
            assert "already exists" in duplicate_json['error'], "Missing duplicate error message"
            logger.info("Duplicate host prevention working correctly")

            # Clean up test host
            logger.info("Cleaning up test host configuration")
            await client.call_tool("ssh_cmd_run", {
                "command": f"sed -i '/^{test_host}/d' {mcp.host_manager.config_path}",
                "io_timeout": 5.0
            })
            
            # Verify cleanup
            list_result_after_cleanup = await client.call_tool("ssh_host_list", {})
            final_hosts = json.loads(list_result_after_cleanup[0].text)
            assert test_host not in final_hosts, "Test host not cleaned up"
            logger.info("Host cleanup successful")

        except Exception as e:
            logger.error(f"Error in host lifecycle test: {e}", exc_info=True)
            raise
    
    print_test_footer()

@pytest.mark.asyncio
async def test_ssh_host_list_structure(mcp_test_environment):
    """Verify the structure and format of host list responses."""
    print_test_header("Testing host list structure")
    logger.info("Starting host list structure test")
    
    async with Client(mcp) as client:
        try:
            list_result = await client.call_tool("ssh_host_list", {})
            hosts_list = json.loads(list_result[0].text)
            
            assert isinstance(hosts_list, list), "Host list should be a list"
            for host in hosts_list:
                assert isinstance(host, str), "Each host entry should be a string"
                assert "@" in host, "Host entry missing @ symbol"
                parts = host.split("@")
                assert len(parts) == 2, "Invalid host format"
                assert parts[0], "Missing username in host entry"
                assert parts[1], "Missing hostname in host entry"
            
            logger.info("Host list structure validation passed")
            
        except Exception as e:
            logger.error(f"Error in host list structure test: {e}", exc_info=True)
            raise
    
    print_test_footer()
