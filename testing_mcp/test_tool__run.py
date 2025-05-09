import pytest
import json
import asyncio
import logging

# Configure logging
logger = logging.getLogger(__name__)

@pytest.mark.asyncio
async def test_ssh_run_basic(mcp_client):
    """Test basic command execution with ssh_run."""
    # Simple echo command
    run_params = {
        "command": "echo 'Hello from MCP SSH!'",
        "io_timeout": 10.0
    }
    
    # Run the command via MCP
    logger.info("Running basic SSH command test")
    async for client in mcp_client:
        run_result = await client.call_tool("ssh_run", run_params)
    
    # Verify the result
    assert run_result is not None, "Expected non-empty result"
    assert isinstance(run_result, list), f"Expected list result, got {type(run_result)}"
    assert len(run_result) > 0, "Expected non-empty list result"
    assert hasattr(run_result[0], 'text'), "Expected TextContent object with 'text' attribute"
    
    # Parse the JSON response
    result_json = json.loads(run_result[0].text)
    logger.info(f"Command result: {result_json}")
    
    assert result_json['exit_code'] == 0, f"Expected exit code 0, got {result_json['exit_code']}"
    assert "Hello from MCP SSH!" in result_json['output'], "Expected output not found"
    assert 'pid' in result_json, "PID should be included in result"
    assert 'start_time' in result_json, "Start time should be included in result"
    assert 'end_time' in result_json, "End time should be included in result"

@pytest.mark.asyncio
async def test_ssh_run_multiline(mcp_client):
    """Test command execution with multiple output lines."""
    # Test with a command that produces multiple lines
    run_params = {
        "command": "for i in {1..5}; do echo \"Line $i\"; done",
        "io_timeout": 10.0
    }
    
    # Run the multi-line command via MCP
    logger.info("Running multi-line command test")
    async for client in mcp_client:
        run_result = await client.call_tool("ssh_run", run_params)
    
    # Verify the result
    assert run_result is not None, "Expected non-empty result"
    assert isinstance(run_result, list), f"Expected list result, got {type(run_result)}"
    assert len(run_result) > 0, "Expected non-empty list result"
    
    # Parse the JSON response
    result_json = json.loads(run_result[0].text)
    logger.info(f"Multi-line command result: {result_json}")
    
    # Verify the result
    assert result_json['exit_code'] == 0, f"Expected exit code 0, got {result_json['exit_code']}"
    assert "Line 1" in result_json['output'], "Expected 'Line 1' not found in output"
    assert "Line 5" in result_json['output'], "Expected 'Line 5' not found in output"

@pytest.mark.asyncio
async def test_ssh_run_failure(mcp_client):
    """Test command execution with a failing command."""
    # Test with a failing command
    run_params = {
        "command": "exit 42",
        "io_timeout": 10.0
    }
    
    # Run the failing command via MCP
    logger.info("Running command failure test")
    async for client in mcp_client:
        with pytest.raises(Exception) as excinfo:
            await client.call_tool("ssh_run", run_params)
    
    # Verify the exception
    error_message = str(excinfo.value)
    logger.info(f"Received expected error: {error_message}")
    assert "exit code 42" in error_message, "Exception should mention exit code 42"
if __name__ == "__main__":
    """
    Allow running this test directly without pytest
    """
    import sys
    from conftest import setup_test_environment, teardown_test_environment, get_mcp_client
    
    async def run_tests():
        """Run all tests in this file"""
        logger.info("Setting up test environment")
        await setup_test_environment()
        
        try:
            # Get a client
            client = await get_mcp_client()
            
            try:
                # Create a simple generator to mimic the fixture behavior
                async def mock_client_fixture():
                    yield client
                
                # Run the tests
                logger.info("Running tests")
                await test_ssh_run_basic(mock_client_fixture())
                await test_ssh_run_multiline(mock_client_fixture())
                
                # The failure test is expected to raise an exception
                try:
                    await test_ssh_run_failure(mock_client_fixture())
                    print("ERROR: Failure test did not raise an exception as expected")
                except Exception as e:
                    if "exit code 42" in str(e):
                        logger.info("Failure test passed with expected exception")
                    else:
                        logger.error(f"Failure test raised unexpected exception: {e}")
                        raise
                
                logger.info("All tests completed successfully")
                
            finally:
                # Close the client
                if hasattr(client, 'close'):
                    await client.close()
                    
        finally:
            # Clean up
            logger.info("Tearing down test environment")
            await teardown_test_environment()
    
    try:
        # Configure logging for direct execution
        logging.basicConfig(level=logging.INFO, 
                           format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        
        # Run the tests
        asyncio.run(run_tests())
        print("All tests completed successfully")
    except Exception as e:
        print(f"Tests failed: {e}")
        sys.exit(1)
