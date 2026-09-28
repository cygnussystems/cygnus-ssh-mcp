"""Directory size on Linux-class hosts: find -printf where available, else a POSIX
'find -exec ls -ln' fallback (BusyBox: Alpine, OpenWrt). Regression from 2026-09-27's
switch away from du -sb: without a fallback, ssh_dir_copy reported bytes_copied: 0 and
ssh_dir_calc_size refused to run on BusyBox (found 2026-09-28)."""
from unittest.mock import MagicMock

from cygnus_ssh_mcp.ops.directory import SshDirectoryOperations_Linux
from cygnus_ssh_mcp.ops import capability_gate


def _ops(capabilities):
    ops = SshDirectoryOperations_Linux.__new__(SshDirectoryOperations_Linux)
    ops.ssh_client = MagicMock()
    ops.ssh_client.capabilities = capabilities
    return ops


def test_gnu_find_uses_printf():
    cmd = _ops({'find_printf': True})._cmd_dir_size('/data')
    assert "-printf '%s\\n'" in cmd and 'ls -ln' not in cmd


def test_unconfirmed_capabilities_default_to_printf():
    assert '-printf' in _ops({})._cmd_dir_size('/data')


def test_busybox_find_falls_back_to_ls():
    cmd = _ops({'find_printf': False})._cmd_dir_size('/my data')
    assert "find '/my data' -type f -exec ls -ln {} +" in cmd and '$5' in cmd
    assert '-printf' not in cmd and 'du ' not in cmd


def test_calculate_directory_size_is_not_gated():
    for guards in (capability_gate.LINUX_DIRECTORY_GUARDS, capability_gate.FLEX_DIRECTORY_GUARDS):
        assert 'calculate_directory_size' not in guards


# --- ssh_dir_search_glob on BusyBox: POSIX fallback instead of refusing (2026-09-28) ---

def test_glob_search_uses_printf_on_gnu_find():
    assert r"-printf '%p\t%y\n'" in _ops({})._cmd_find_with_type('/data', '*.log', None, False)


def test_glob_search_falls_back_without_printf():
    cmd = _ops({'find_printf': False})._cmd_find_with_type('/my data', '*.log', 2, True)
    assert cmd.startswith("find '/my data' -maxdepth 2 -name '*.log' -exec sh -c ") and '-printf' not in cmd
    assert "_ {} +" in cmd and '[ -d "$f" ]' in cmd


def test_glob_search_is_not_gated_on_printf():
    assert 'search_files_recursive' not in capability_gate.LINUX_DIRECTORY_GUARDS
