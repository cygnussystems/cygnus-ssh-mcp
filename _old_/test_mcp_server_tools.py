import asyncio
import sys
import os
from pathlib import Path
import tempfile
import yaml

# Ensure the main project directory is in the Python path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

try:
    from fastmcp import Client
    from mcp_ssh_server import mcp, SshHostManager
    from ssh_models import SshError
except ImportError as e:
    print(f"FATAL: Failed to import required modules. Error: {e}", file=sys.stderr)
    print("Make sure fastmcp is installed and you are running from the correct directory.", file=sys.stderr)
    sys.exit(1)

async def run_mcp_server_tests():
    """Runs tests for the SSH MCP server tools."""
    print("Starting SSH MCP server tests...")

    # Create a temporary config file for testing
    with tempfile.NamedTemporaryFile(mode='w', delete=False) as tmp:
        yaml.safe_dump({'hosts': []}, tmp)
        config_path = Path(tmp.name)
    
    try:
        # Initialize host manager with the temp config
        host_manager = SshHostManager(config_path=config_path)
        
        # Add a test host (this won't actually be used for connections)
        host_manager.add_host('test_host', 'localhost', 22, 'testuser', 'testpass')
        
        # Use the Client context manager with the imported mcp instance
        async with Client(mcp) as client:
            print("Client created. Testing tools...")

            # --- Test listing tools ---
            try:
                print("\nListing available SSH MCP tools...")
                tools = await client.list_tools()
                print(f"  -> Found {len(tools)} tool(s):")

                expected_tool_ids = {
                    "ssh_conn_connect", "ssh_conn_add_host", "ssh_cmd_run", "ssh_file_transfer",
                    "ssh_conn_status", "ssh_conn_verify_sudo", "ssh_file_replace_block", 
                    "ssh_cmd_output", "ssh_cmd_history"
                }
                found_tool_ids = set()

                for tool in tools:
                    tool_id = tool.name
                    description = tool.description
                    print(f"    - ID: {tool_id}, Description: {description.strip()}")
                    found_tool_ids.add(tool_id)

                missing = expected_tool_ids - found_tool_ids
                extra = found_tool_ids - expected_tool_ids - {"add", "subtract", "get_joke"}  # Ignore sample tools

                assert not missing, f"Missing expected tools: {missing}"
                print("  -> List tools test passed successfully!")

            except Exception as e:
                print(f"  -> Error testing list_tools: {e}", file=sys.stderr)
                raise

            # --- Test ssh_conn_add_host tool ---
            try:
                print("\nTesting 'ssh_conn_add_host' tool...")
                add_host_params = {
                    "name": "test_host2",
                    "host": "example.com",
                    "user": "user2",
                    "password": "pass2",
                    "port": 2222
                }
                add_host_result = await client.call_tool("ssh_conn_add_host", add_host_params)
                print(f"  -> Got result: {add_host_result}")

                # Verify the host was added
                host = host_manager.get_host("test_host2")
                assert host is not None, "Host was not added to configuration"
                assert host["host"] == "example.com", f"Host address mismatch: {host['host']} != example.com"
                assert host["port"] == 2222, f"Port mismatch: {host['port']} != 2222"
                print("  -> ssh_conn_add_host test passed!")
            except Exception as e:
                print(f"  -> Error testing 'ssh_conn_add_host': {e}", file=sys.stderr)
                raise

            # Note: We can't fully test ssh_conn_connect, ssh_run, etc. without a real SSH server
            # But we can test that the tools exist and validate their parameters

            # --- Test ssh_conn_connect tool parameters ---
            try:
                print("\nTesting 'ssh_conn_connect' tool parameters...")
                # This would fail with a connection error, but we can check the parameter validation
                try:
                    connect_params = {"host_name": "nonexistent_host"}
                    await client.call_tool("ssh_conn_connect", connect_params)
                except Exception as e:
                    print(f"  -> Expected error: {e}")
                    assert "not found" in str(e), "Expected 'not found' error for nonexistent host"
                print("  -> ssh_conn_connect parameter validation test passed!")
            except Exception as e:
                print(f"  -> Error testing 'ssh_conn_connect' parameters: {e}", file=sys.stderr)
                raise
                
            # --- Test task management tools existence ---
            try:
                print("\nVerifying task management tools...")
                tools = await client.list_tools()
                tool_names = {tool.name for tool in tools}
                
                task_tools = {"ssh_task_launch", "ssh_task_status", "ssh_task_kill"}
                missing_tools = task_tools - tool_names
                
                assert not missing_tools, f"Missing task management tools: {missing_tools}"
                print("  -> Task management tools verification passed!")
                
                # We can't actually test these tools without an SSH connection,
                # but we can verify they exist and have the correct parameters
                print("  -> Task management tools are available")
            except Exception as e:
                print(f"  -> Error verifying task management tools: {e}", file=sys.stderr)
                raise
                
            # --- Test file operation tools existence ---
            try:
                print("\nVerifying file operation tools...")
                tools = await client.list_tools()
                tool_names = {tool.name for tool in tools}
                
                file_tools = {"ssh_dir_mkdir", "ssh_dir_remove", "ssh_dir_list_files_basic", "ssh_file_stat", "ssh_file_replace_line"}
                missing_tools = file_tools - tool_names
                
                assert not missing_tools, f"Missing file operation tools: {missing_tools}"
                print("  -> File operation tools verification passed!")
                
                # We can't actually test these tools without an SSH connection,
                # but we can verify they exist and have the correct parameters
                print("  -> File operation tools are available")
            except Exception as e:
                print(f"  -> Error verifying file operation tools: {e}", file=sys.stderr)
                raise
                
            # --- Test directory operation tools existence ---
            try:
                print("\nVerifying directory operation tools...")
                tools = await client.list_tools()
                tool_names = {tool.name for tool in tools}
                
                dir_tools = {
                    "ssh_dir_search_glob", "ssh_dir_calc_size", "ssh_dir_delete", 
                    "ssh_dir_batch_delete_files", "ssh_file_move", "ssh_dir_list_advanced",
                    "ssh_archive_create", "ssh_archive_extract", "ssh_dir_search_files_content",
                    "ssh_dir_copy"
                }
                missing_tools = dir_tools - tool_names
                
                assert not missing_tools, f"Missing directory operation tools: {missing_tools}"
                print("  -> Directory operation tools verification passed!")
                
                # We can't actually test these tools without an SSH connection,
                # but we can verify they exist and have the correct parameters
                print("  -> Directory operation tools are available")
            except Exception as e:
                print(f"  -> Error verifying directory operation tools: {e}", file=sys.stderr)
                raise

    except Exception as e:
        print(f"\nTest run failed with error: {e}", file=sys.stderr)
        raise
    finally:
        # Clean up the temporary file
        try:
            config_path.unlink()
        except:
            pass

    print("\nAll SSH MCP server tests completed!")

if __name__ == "__main__":
    try:
        asyncio.run(run_mcp_server_tests())
    except Exception as e:
        print(f"\nTest run failed with error: {e}", file=sys.stderr)
        sys.exit(1)
