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
        'alias': 'win-test',
        'host': '192.168.1.28',
        'port': 22,
        'user': 'test',
        'password': 'testpwd',
        'home': 'C:\\Users\\test',
        'python': 'C:\\Program Files\\Python312\\python.exe',  # Full path for SSH sessions (no quotes, use & in PS)
        'venv_activate': '.\\venv\\Scripts\\Activate.ps1',
        'path_sep': '\\',
    },
    'macos': {
        'alias': 'testmac',
        'host': '192.168.1.53',
        'port': 22,
        'user': 'claude',
        'password': 'claudepwd',
        'home': '/Users/claude',
        'python': '/usr/local/bin/python3.12',  # Full path required for SSH sessions
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
        'host': '192.168.1.28',
        'port': 22,
        'user': 'test',
        'password': 'testpwd',
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
