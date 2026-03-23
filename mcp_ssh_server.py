#!/usr/bin/env python3
"""
Wrapper script for backwards compatibility.
The actual server code is in src/cygnus_ssh_mcp/server.py
"""
import sys
from pathlib import Path

# Add src to path so we can import the package
sys.path.insert(0, str(Path(__file__).parent / "src"))

from cygnus_ssh_mcp.server import main

if __name__ == "__main__":
    main()
