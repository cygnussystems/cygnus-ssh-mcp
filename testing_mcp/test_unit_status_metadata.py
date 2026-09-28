"""Connect-time system metadata on minimal hosts (issues/_archive_/2026-09-28-openwrt-connection-metadata-*):
OpenWrt has no whoami/hostname, so user and hostname came back as "" - and the disconnect
message read "disconnected from @host". Offline."""
from types import SimpleNamespace
from unittest.mock import MagicMock

from cygnus_ssh_mcp.ops.os_ops import SshOsOperations_Linux, SshOsOperations_Mac


def _ops(cls, output, user='root'):
    ops = cls.__new__(cls)
    ops.logger = MagicMock()
    ops.ssh_client = MagicMock()
    ops.ssh_client.user = user
    lines = [line + '\n' for line in output.splitlines()]
    ops.ssh_client.run_ops.execute_command.return_value = SimpleNamespace(
        total_lines=len(lines), tail=lambda n: lines)
    return ops


def test_empty_probe_values_are_unknown_not_empty():
    result = _ops(SshOsOperations_Linux, "HOSTNAME:\nIFACE:lo|IPS:127.0.0.1/8\n").network_info()
    assert result['hostname'] == 'n/a', result


def test_user_falls_back_to_the_ssh_login_user():
    result = _ops(SshOsOperations_Linux, "USER:\nCWD:/root\nTIME:x\nOS_TYPE:Linux\n").user_status()
    assert result['user'] == 'root', result


def test_probe_scripts_have_fallbacks_for_minimal_hosts():
    linux = SshOsOperations_Linux.__new__(SshOsOperations_Linux)
    assert '/proc/meminfo' in linux._cmd_hardware_info() and 'free -m' not in linux._cmd_hardware_info()
    assert '/proc/sys/kernel/hostname' in linux._cmd_network_info()
    assert 'id -un' in linux._cmd_user_status()
    mac = SshOsOperations_Mac.__new__(SshOsOperations_Mac)   # also used for flex (FreeBSD)
    assert 'OS_TYPE:macos"' not in mac._cmd_user_status().replace('Darwin) echo "OS_TYPE:macos"', '')
    assert 'hw.physmem' in mac._cmd_hardware_info()
