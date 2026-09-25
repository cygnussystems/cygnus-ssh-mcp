"""
Configuration for cross-platform test matrix.

Defines runner machines and target platforms for the 3x3 test matrix.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# The macOS password lives only in testing_mcp/.env (MACOS_SSH_PASSWORD), not here
load_dotenv(Path(__file__).resolve().parent.parent / 'testing_mcp' / '.env')
MACOS_PASSWORD = os.environ.get('MACOS_SSH_PASSWORD')

# Runner machines - these execute the tests
RUNNERS = {
    'linux': {
        'alias': 'linux-test',
        'host': '192.168.1.27',
        'port': 22,
        'user': 'test',
        'password': 'testpwd',
        'home': '/home/test',
        'python': 'python3',
        'venv_activate': 'source venv/bin/activate',
        'path_sep': '/',
    },
    'windows': {
        'alias': 'win-server-2016',
        'host': '192.168.1.9',
        'port': 22,
        'user': 'claude',
        'password': 'claudepwd',
        'home': 'C:\\Users\\claude',
        # NOTE: python path unverified on win-server-2016 (the old win-test VM this
        # replaced had Python at this path, but win-server-2016 hasn't been checked
        # as a runner - it was only ever used as a target before 2026-07-05).
        'python': 'C:\\Program Files\\Python312\\python.exe',  # Full path for SSH sessions (no quotes, use & in PS)
        'venv_activate': '.\\venv\\Scripts\\Activate.ps1',
        'path_sep': '\\',
    },
    'macos': {
        'alias': 'MACBOOK-2015',
        'host': '192.168.1.109',
        'port': 22,
        'user': 'claude',
        'password': MACOS_PASSWORD,
        'home': '/Users/claude',
        # NOTE: MACBOOK-2015 (2026-09-25) only has Apple's bundled Python 3.9.6 -
        # too old for this project's >=3.10 requirement, so it can't be a runner
        # yet. Install a newer Python (e.g. python.org's installer puts it at
        # /usr/local/bin/python3) before using --runner=macos.
        'python': '/usr/local/bin/python3',
        'venv_activate': 'source venv/bin/activate',
        'path_sep': '/',
    },
}

# Target platforms - tests connect TO these
TARGETS = {
    'linux': {
        'host': '192.168.1.27',
        'port': 22,
        'user': 'test',
        'password': 'testpwd',
    },
    'windows': {
        'host': '192.168.1.9',
        'port': 22,
        'user': 'claude',
        'password': 'claudepwd',
    },
    'macos': {
        'host': '192.168.1.109',  # MACBOOK-2015
        'port': 22,
        'user': 'claude',
        'password': MACOS_PASSWORD,
    },
}

# Test workspace directory name (created in runner's home)
MATRIX_WORKSPACE = 'mcp_matrix_test'

# Test dependencies to install alongside the wheel
TEST_DEPENDENCIES = [
    'pytest',
    'pytest-asyncio==0.23.8',  # Pin to compatible version
    'python-dotenv',
    'fastmcp',
]
