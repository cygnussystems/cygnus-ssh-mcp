import pytest
import asyncio
from test_mcp_fixtures import setup_test_environment, teardown_test_environment

@pytest.fixture(scope="session")
async def mcp_test_environment():
    """Session-wide test environment setup and teardown."""
    await setup_test_environment()
    yield
    await teardown_test_environment()

# This allows running the tests with pytest
def pytest_configure(config):
    """Configure pytest."""
    # Register the asyncio marker
    config.addinivalue_line("markers", "asyncio: mark test as an asyncio test")
