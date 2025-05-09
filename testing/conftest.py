import pytest
from test_utils import get_client, cleanup_client, print_test_header, print_test_footer

@pytest.fixture(scope="module")
def ssh_client():
    """Pytest fixture to provide a connected SshClient instance."""
    print("\n--- Setting up SSH client fixture ---")
    client = get_client(force_new=True)
    yield client
    print("\n--- Tearing down SSH client fixture ---")
    cleanup_client(client)
