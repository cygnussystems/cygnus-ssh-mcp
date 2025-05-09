#!/usr/bin/env python
"""
Test runner for MCP SSH integration tests.
This script runs all the tests in sequence without using pytest.
"""

import asyncio
import logging
import sys
import os
from pathlib import Path

# Add project root to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("test_runner")

# Import test environment setup/teardown
from conftest import setup_test_environment, teardown_test_environment

async def run_all_tests():
    """Run all tests in sequence."""
    logger.info("Starting MCP SSH integration tests")
    
    # Set up the test environment
    await setup_test_environment()
    
    try:
        # Import all test functions
        from test_tool__run import test_ssh_run_basic, test_ssh_run_multiline, test_ssh_run_failure
        from test_mcp_status import test_ssh_status
        from test_tool__history import test_ssh_command_history
        
        # Run the tests
        tests = [
            ("SSH Run Basic", test_ssh_run_basic),
            ("SSH Run Multiline", test_ssh_run_multiline),
            ("SSH Run Failure", test_ssh_run_failure),
            ("SSH Status", test_ssh_status),
            ("SSH Command History", test_ssh_command_history)
        ]
        
        success_count = 0
        failure_count = 0
        
        for test_name, test_func in tests:
            logger.info(f"Running test: {test_name}")
            try:
                await test_func()
                logger.info(f"✅ Test passed: {test_name}")
                success_count += 1
            except Exception as e:
                logger.error(f"❌ Test failed: {test_name}")
                logger.error(f"Error: {e}")
                failure_count += 1
        
        # Print summary
        logger.info("=" * 50)
        logger.info(f"Test Summary: {success_count} passed, {failure_count} failed")
        logger.info("=" * 50)
        
        if failure_count > 0:
            logger.error("Some tests failed!")
            return False
        else:
            logger.info("All tests passed!")
            return True
            
    finally:
        # Clean up
        await teardown_test_environment()

if __name__ == "__main__":
    try:
        success = asyncio.run(run_all_tests())
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        logger.info("Tests interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.critical(f"Unhandled exception in test runner: {e}", exc_info=True)
        sys.exit(1)
