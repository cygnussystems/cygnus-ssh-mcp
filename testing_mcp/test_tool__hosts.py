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


def get_host_keys(hosts_list):
    """Extract host keys from the hosts list (handles both old string format and new dict format)."""
    keys = []
    for host in hosts_list:
        if isinstance(host, dict):
            keys.append(host['key'])
        else:
            keys.append(host)
    return keys


def host_key_in_list(key, hosts_list):
    """Check if a host key is in the hosts list (handles both formats)."""
    return key in get_host_keys(hosts_list)

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
            initial_hosts = host_data['hosts']
            assert isinstance(initial_hosts, list), "Hosts should be a list"
            logger.info(f"Initial hosts: {initial_hosts}")

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
            assert host_key_in_list(test_host, updated_hosts), "New host not in list"
            logger.info(f"Updated hosts list: {get_host_keys(updated_hosts)}")

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
            assert not host_key_in_list(test_host, final_hosts), "Test host not cleaned up"
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
            hosts_list = host_data['hosts']

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
                assert host_key_in_list(test_host, updated_hosts_list), f"Test host '{test_host}' not found in updated host list"

            logger.info(f"Updated host list contains {len(updated_hosts_list)} hosts")

            # Validate each host entry format (now dictionaries with 'key' field)
            for host_entry in hosts_list:
                assert isinstance(host_entry, dict), f"Each host entry should be a dict, got {type(host_entry)}"
                assert 'key' in host_entry, f"Host entry missing 'key' field: {host_entry}"
                host_key = host_entry['key']
                assert "@" in host_key, f"Host key '{host_key}' missing @ symbol"
                parts = host_key.split("@")
                assert len(parts) == 2, f"Invalid host format for '{host_key}'"
                username, hostname = parts
                assert username, f"Missing username in host entry '{host_key}'"
                assert hostname, f"Missing hostname in host entry '{host_key}'"
                # Alias and description are optional
                if 'alias' in host_entry:
                    assert isinstance(host_entry['alias'], str), f"Alias should be a string"
                if 'description' in host_entry:
                    assert isinstance(host_entry['description'], str), f"Description should be a string"
                logger.info(f"Validated host entry format: {host_key}")
            
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
                assert host_key_in_list(test_host, verify_host_data['hosts']), f"Test host '{test_host}' not found after adding"

                # Remove the test host
                remove_params = {"host_name": test_host}
                remove_result = await client.call_tool("ssh_host_remove", remove_params)
                remove_json = json.loads(extract_result_text(remove_result))
                assert remove_json['status'] == 'success', f"Remove host failed: {remove_json}"

                # Verify it was removed
                final_list_result = await client.call_tool("ssh_host_list", {})
                final_host_data = json.loads(extract_result_text(final_list_result))
                assert not host_key_in_list(test_host, final_host_data['hosts']), f"Test host '{test_host}' still present after removal"

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
                assert not host_key_in_list(test_host, final_hosts_list), f"Test host '{test_host}' still present after removal"
            
            logger.info(f"Successfully removed all {len(test_hosts)} sample test hosts")
            logger.info("Host list structure and configuration validation passed")

        except Exception as e:
            logger.error(f"Error in host list structure test: {e}", exc_info=True)
            raise

    print_test_footer()


@pytest.mark.asyncio
async def test_ssh_host_alias_and_description(mcp_test_environment):
    """Test host alias and description functionality."""
    print_test_header("Testing host alias and description")
    logger.info("Starting host alias and description test")

    async with Client(mcp) as client:
        try:
            timestamp = int(time.time())

            # Test adding host with alias and description
            test_host_key = f"testuser@testhost_alias_{timestamp}"
            test_alias = f"testalias{timestamp}"
            test_description = "Test host for alias testing"

            add_params = {
                "user": "testuser",
                "host": f"testhost_alias_{timestamp}",
                "password": "testpass",
                "port": 2222,
                "alias": test_alias,
                "description": test_description
            }

            add_result = await client.call_tool("ssh_conn_add_host", add_params)
            add_json = json.loads(extract_result_text(add_result))
            assert add_json['status'] == 'success', f"Add host with alias failed: {add_json}"
            assert add_json.get('alias') == test_alias, "Alias not returned in add response"
            assert add_json.get('description') == test_description, "Description not returned in add response"
            logger.info(f"Successfully added host with alias '{test_alias}' and description")

            # Verify alias and description appear in host list
            list_result = await client.call_tool("ssh_host_list", {})
            host_data = json.loads(extract_result_text(list_result))
            hosts = host_data['hosts']

            # Find our host in the list
            host_entry = None
            for h in hosts:
                if h.get('key') == test_host_key:
                    host_entry = h
                    break

            assert host_entry is not None, f"Host '{test_host_key}' not found in list"
            assert host_entry.get('alias') == test_alias, f"Alias mismatch in host list"
            assert host_entry.get('description') == test_description, f"Description mismatch in host list"
            logger.info("Host list correctly shows alias and description")

            # Clean up
            remove_params = {"host_name": test_host_key}
            remove_result = await client.call_tool("ssh_host_remove", remove_params)
            remove_json = json.loads(extract_result_text(remove_result))
            assert remove_json['status'] == 'success', f"Remove host failed: {remove_json}"
            logger.info("Test host cleanup successful")

        except Exception as e:
            logger.error(f"Error in alias and description test: {e}", exc_info=True)
            raise

    print_test_footer()


@pytest.mark.asyncio
async def test_ssh_host_duplicate_alias_prevention(mcp_test_environment):
    """Test that duplicate aliases are prevented."""
    print_test_header("Testing duplicate alias prevention")
    logger.info("Starting duplicate alias prevention test")

    async with Client(mcp) as client:
        try:
            timestamp = int(time.time())
            test_alias = f"dupalias{timestamp}"

            # Add first host with alias
            host1_key = f"testuser@testhost_dup1_{timestamp}"
            add_params1 = {
                "user": "testuser",
                "host": f"testhost_dup1_{timestamp}",
                "password": "testpass1",
                "port": 2222,
                "alias": test_alias
            }

            add_result1 = await client.call_tool("ssh_conn_add_host", add_params1)
            add_json1 = json.loads(extract_result_text(add_result1))
            assert add_json1['status'] == 'success', f"Add first host failed: {add_json1}"
            logger.info(f"Added first host with alias '{test_alias}'")

            # Try to add second host with same alias - should fail
            host2_key = f"testuser@testhost_dup2_{timestamp}"
            add_params2 = {
                "user": "testuser",
                "host": f"testhost_dup2_{timestamp}",
                "password": "testpass2",
                "port": 2223,
                "alias": test_alias  # Same alias
            }

            add_result2 = await client.call_tool("ssh_conn_add_host", add_params2)
            add_json2 = json.loads(extract_result_text(add_result2))
            assert add_json2['status'] == 'error', "Duplicate alias should fail"
            assert "already in use" in add_json2['error'], f"Expected 'already in use' error, got: {add_json2['error']}"
            logger.info("Duplicate alias correctly prevented")

            # Clean up first host
            remove_params = {"host_name": host1_key}
            remove_result = await client.call_tool("ssh_host_remove", remove_params)
            remove_json = json.loads(extract_result_text(remove_result))
            assert remove_json['status'] == 'success', f"Remove host failed: {remove_json}"
            logger.info("Test host cleanup successful")

        except Exception as e:
            logger.error(f"Error in duplicate alias prevention test: {e}", exc_info=True)
            raise

    print_test_footer()
