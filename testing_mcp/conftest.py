import pytest
import asyncio
import sys
import os
import logging
from pathlib import Path

# Add project root to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

# Import test fixtures
from test_mcp_fixtures import setup_test_environment, teardown_test_environment, get_mcp_client

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logging.getLogger('paramiko').setLevel(logging.WARNING)

# This allows running the tests with pytest
def pytest_configure(config):
    """Configure pytest."""
    # Register the asyncio marker
    config.addinivalue_line("markers", "asyncio: mark test as an asyncio test")

@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for each test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

@pytest.fixture(scope="session")
async def mcp_test_environment():
    """Session-wide test environment setup and teardown."""
    await setup_test_environment()
    yield
    await teardown_test_environment()

@pytest.fixture
async def mcp_client(mcp_test_environment):
    """Fixture to provide a connected MCP client."""
    client = await get_mcp_client()
    yield client
    # Only try to close if the client has a close method
    if hasattr(client, 'close'):
        await client.close()

def print_test_header(test_name):
    """Print a formatted test header."""
    print("\n" + "=" * 40)
    print(f"Running test: {test_name}")
    print("=" * 40)

def print_test_footer():
    """Print a formatted test footer."""
    print("\n" + "=" * 40)
    print("Test completed")
    print("=" * 40 + "\n")
