"""Test helper: run the real MCP server over stdio, with one client method slowed down.

Used by test_tool__long_operations_stdio.py to drive a genuinely separate server process
through a real MCP client with a real request timeout - the way OpenCode and other
harnesses use it - while keeping "an operation that takes N seconds" deterministic.

    python slow_server.py --config <hosts.toml> [--max-wait N]

MCP_SSH_TEST_SLOW_SECONDS (env): how long SshClient.calculate_directory_size sleeps
before doing its real work (default 0). Not a test file itself (no test_ prefix).
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from cygnus_ssh_mcp import server  # noqa: E402
from cygnus_ssh_mcp.client import SshClient  # noqa: E402

_delay = float(os.environ.get("MCP_SSH_TEST_SLOW_SECONDS", "0"))
_original = SshClient.calculate_directory_size


def _slow_calculate_directory_size(self, *args, **kwargs):
    time.sleep(_delay)
    return _original(self, *args, **kwargs)


SshClient.calculate_directory_size = _slow_calculate_directory_size

if __name__ == "__main__":
    server.main()
