"""
Configuration for cross-platform test matrix.

Defines runner machines and target platforms for the 3x3 test matrix.
"""

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
        'alias': 'macbook-2014',
        'host': '192.168.1.200',
        'port': 22,
        'user': 'claude',
        'password': 'claudepwd',
        'home': '/Users/claude',
        # NOTE: plain 'python3' on PATH still resolves to Apple's bundled 3.8.9
        # (too old for this project's >=3.10 requirement) - a newer Python 3.13.5
        # was installed via python.org's installer at /usr/local/bin/python3,
        # verified working as a runner 2026-07-05.
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
        'host': '192.168.1.53',
        'port': 22,
        'user': 'claude',
        'password': 'claudepwd',
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
