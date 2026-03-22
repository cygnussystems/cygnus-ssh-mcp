import pytest
import json
import logging
import time
import asyncio
from conftest import print_test_header, print_test_footer, extract_result_text
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
            host_data = json.loads(extract_result_text(list_result))
            assert isinstance(host_data, dict), "Host data should be a dictionary"
            assert 'hosts' in host_data, "Response missing 'hosts' key"
            assert 'config_path' in host_data, "Response missing 'config_path' key"
            initial_hosts = host_data['hosts']
            assert isinstance(initial_hosts, list), "Hosts should be a list"
            logger.info(f"Initial hosts: {initial_hosts}")
            logger.info(f"Config path: {host_data['config_path']}")

            # Test adding new host with timestamp to ensure uniqueness
            timestamp = int(time.time())
            test_host = f"testuser@testhost{timestamp}"
            add_params = {
                "user": "testuser",
                "host": f"testhost{timestamp}", 
                "password": "testpass",
                "port": 2222
            }
            
            # Add new host
            add_result = await client.call_tool("ssh_conn_add_host", add_params)
            add_json = json.loads(extract_result_text(add_result))
            assert add_json['status'] == 'success', f"Add host failed: {add_json}"
            assert add_json['key'] == test_host, "Host key mismatch"
            logger.info(f"Successfully added host: {test_host}")

            # Verify host appears in list
            list_result_after_add = await client.call_tool("ssh_host_list", {})
            updated_host_data = json.loads(extract_result_text(list_result_after_add))
            updated_hosts = updated_host_data['hosts']
            assert test_host in updated_hosts, "New host not in list"
            logger.info(f"Updated hosts list: {updated_hosts}")

            # Test duplicate prevention
            duplicate_result = await client.call_tool("ssh_conn_add_host", add_params)
            duplicate_json = json.loads(extract_result_text(duplicate_result))
            assert duplicate_json['status'] == 'error', "Duplicate host should error"
            assert "already exists" in duplicate_json['error'], "Missing duplicate error message"
            logger.info("Duplicate host prevention working correctly")

            # Clean up test host using the dedicated host removal tool
            logger.info("Cleaning up test host configuration")
            remove_params = {
                "host_name": test_host
            }
            remove_result = await client.call_tool("ssh_host_remove", remove_params)
            remove_json = json.loads(extract_result_text(remove_result))
            assert remove_json['status'] == 'success', f"Remove host failed: {remove_json}"
            logger.info(f"Host removal result: {remove_json}")
            
            # Wait a moment to ensure file operations complete
            await asyncio.sleep(1)
            
            # Verify cleanup
            list_result_after_cleanup = await client.call_tool("ssh_host_list", {})
            final_host_data = json.loads(extract_result_text(list_result_after_cleanup))
            final_hosts = final_host_data['hosts']
            assert test_host not in final_hosts, "Test host not cleaned up"
            logger.info("Host cleanup successful")

        except Exception as e:
            logger.error(f"Error in host lifecycle test: {e}", exc_info=True)
            raise
    
    print_test_footer()

@pytest.mark.asyncio
async def test_ssh_host_list_structure(mcp_test_environment):
    """Verify the structure and format of host list responses and validate host configurations."""
    print_test_header("Testing host list structure and configuration")
    logger.info("Starting host list structure and configuration test")
    
    async with Client(mcp) as client:
        try:
            # Get the host list
            list_result = await client.call_tool("ssh_host_list", {})
            host_data = json.loads(extract_result_text(list_result))
            
            # Validate basic structure
            assert isinstance(host_data, dict), "Host data should be a dictionary"
            assert 'hosts' in host_data, "Response missing 'hosts' key"
            assert 'config_path' in host_data, "Response missing 'config_path' key"
            hosts_list = host_data['hosts']
            config_path = host_data['config_path']
            
            # Validate config path
            assert isinstance(config_path, str), "Config path should be a string"
            assert len(config_path) > 0, "Config path should not be empty"
            assert config_path.endswith('.toml'), "Config path should point to a TOML file"
            logger.info(f"Config path validation passed: {config_path}")
            
            # Validate hosts list
            assert isinstance(hosts_list, list), "Hosts should be a list"
            logger.info(f"Found {len(hosts_list)} hosts in configuration")
            
            # Add several test hosts to ensure we have a good sample to test with
            test_hosts = []
            for i in range(1, 4):  # Add 3 test hosts
                timestamp = int(time.time()) + i
                test_host = f"testuser@testhost_sample_{timestamp}"
                test_hosts.append(test_host)
                
                add_params = {
                    "user": "testuser",
                    "host": f"testhost_sample_{timestamp}",
                    "password": f"testpass_sample_{i}",
                    "port": 2222 + i
                }
                add_result = await client.call_tool("ssh_conn_add_host", add_params)
                add_json = json.loads(extract_result_text(add_result))
                assert add_json['status'] == 'success', f"Add sample host {i} failed: {add_json}"
                logger.info(f"Added sample test host {i}: {test_host}")
            
            # Get updated host list after adding test hosts
            updated_list_result = await client.call_tool("ssh_host_list", {})
            updated_host_data = json.loads(extract_result_text(updated_list_result))
            updated_hosts_list = updated_host_data['hosts']
            
            # Verify all test hosts were added
            for test_host in test_hosts:
                assert test_host in updated_hosts_list, f"Test host '{test_host}' not found in updated host list"
            
            logger.info(f"Updated host list contains {len(updated_hosts_list)} hosts")
            
            # Validate each host entry format
            for host in hosts_list:
                assert isinstance(host, str), f"Each host entry should be a string, got {type(host)}"
                assert "@" in host, f"Host entry '{host}' missing @ symbol"
                parts = host.split("@")
                assert len(parts) == 2, f"Invalid host format for '{host}'"
                username, hostname = parts
                assert username, f"Missing username in host entry '{host}'"
                assert hostname, f"Missing hostname in host entry '{host}'"
                logger.info(f"Validated host entry format: {host}")
                
                # For each host, get its configuration details
                if len(hosts_list) <= 5:  # Only do detailed checks if we have a reasonable number of hosts
                    connect_params = {"host_name": host}
                    # We don't actually connect, just check if the host exists in config
                    # by attempting to get its configuration
                    host_list_result = await client.call_tool("ssh_host_list", {})
                    host_list_data = json.loads(extract_result_text(host_list_result))
                    assert host in host_list_data['hosts'], f"Host '{host}' not found in updated host list"
            
            # Test that we can add and remove a test host to verify config file is writable
            if len(hosts_list) <= 5:  # Only do this test if we have a reasonable number of hosts
                # Create a unique test host
                timestamp = int(time.time())
                test_host = f"testuser@testhost_verify_{timestamp}"
                
                # Add the test host
                add_params = {
                    "user": "testuser",
                    "host": f"testhost_verify_{timestamp}",
                    "password": "testpass_verify",
                    "port": 2222
                }
                add_result = await client.call_tool("ssh_conn_add_host", add_params)
                add_json = json.loads(extract_result_text(add_result))
                assert add_json['status'] == 'success', f"Add host failed: {add_json}"
                
                # Verify it was added
                verify_list_result = await client.call_tool("ssh_host_list", {})
                verify_host_data = json.loads(extract_result_text(verify_list_result))
                assert test_host in verify_host_data['hosts'], f"Test host '{test_host}' not found after adding"
                
                # Remove the test host
                remove_params = {"host_name": test_host}
                remove_result = await client.call_tool("ssh_host_remove", remove_params)
                remove_json = json.loads(extract_result_text(remove_result))
                assert remove_json['status'] == 'success', f"Remove host failed: {remove_json}"
                
                # Verify it was removed
                final_list_result = await client.call_tool("ssh_host_list", {})
                final_host_data = json.loads(extract_result_text(final_list_result))
                assert test_host not in final_host_data['hosts'], f"Test host '{test_host}' still present after removal"
                
                logger.info(f"Successfully verified config file write operations with test host '{test_host}'")
            
            # Clean up all the sample test hosts we added
            for test_host in test_hosts:
                remove_params = {"host_name": test_host}
                remove_result = await client.call_tool("ssh_host_remove", remove_params)
                remove_json = json.loads(extract_result_text(remove_result))
                assert remove_json['status'] == 'success', f"Remove sample host '{test_host}' failed: {remove_json}"
                logger.info(f"Removed sample test host: {test_host}")
            
            # Verify all sample hosts were removed
            final_list_result = await client.call_tool("ssh_host_list", {})
            final_host_data = json.loads(extract_result_text(final_list_result))
            final_hosts_list = final_host_data['hosts']
            
            for test_host in test_hosts:
                assert test_host not in final_hosts_list, f"Test host '{test_host}' still present after removal"
            
            logger.info(f"Successfully removed all {len(test_hosts)} sample test hosts")
            logger.info("Host list structure and configuration validation passed")
            
        except Exception as e:
            logger.error(f"Error in host list structure test: {e}", exc_info=True)
            raise
    
    print_test_footer()
