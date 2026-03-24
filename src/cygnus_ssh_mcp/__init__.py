"""
cygnus-ssh-mcp: Professional-grade SSH MCP server for AI assistants.

This package provides 43+ tools for remote server management via SSH,
including command execution, file operations, directory operations,
background task management, and archive handling.
"""

from cygnus_ssh_mcp.server import main, mcp

__version__ = "1.2.1"
__all__ = ["main", "mcp"]
