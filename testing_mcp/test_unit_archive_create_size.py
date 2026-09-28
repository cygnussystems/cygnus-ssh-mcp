"""ssh_archive_create on a host without `stat` (issues/2026-09-28-openwrt-archive-create-*):
the archive was created, then the size query (`stat -c %s`) failed with exit 127 and the
whole call returned status: error - inviting a retry over a valid archive. Offline."""
from types import SimpleNamespace
from unittest.mock import MagicMock

from cygnus_ssh_mcp.ops.directory import SshDirectoryOperations_Linux
from cygnus_ssh_mcp.models import CommandFailed


def _ops(capabilities, fail_on=None):
    ops = SshDirectoryOperations_Linux.__new__(SshDirectoryOperations_Linux)
    ops.logger = MagicMock()
    ops.ssh_client = MagicMock()
    ops.ssh_client.capabilities = capabilities

    def run(cmd, **kwargs):
        if fail_on and cmd.startswith(fail_on):
            raise CommandFailed(127, "", "ash: stat: not found")
        output = 'exists' if cmd.startswith('[ -f') else '143'
        return SimpleNamespace(exit_code=0, last_nonblank=lambda: output, tail=lambda n: output)
    ops.ssh_client.run.side_effect = run
    return ops


def test_size_uses_wc_without_gnu_stat():
    assert _ops({'stat_c': False})._cmd_file_size('/tmp/a b.tgz') == "wc -c < '/tmp/a b.tgz'"
    assert _ops({})._cmd_file_size('/tmp/a.tgz').startswith('stat -c %s')


def test_busybox_archive_create_reports_real_size():
    # OpenWrt: no `stat` binary at all
    result = _ops({'stat_c': False}, fail_on='stat').create_archive_from_directory('/tmp/src', '/tmp/out.tar.gz')
    assert result['status'] == 'success' and result['size_bytes'] == 143, result
    assert 'size_error' not in result, result


def test_failing_size_query_never_turns_a_created_archive_into_an_error():
    result = _ops({}, fail_on='stat').create_archive_from_directory('/tmp/src', '/tmp/out.tar.gz')
    assert result['status'] == 'success', result
    assert result['archive_created'] == '/tmp/out.tar.gz' and result['size_bytes'] == -1
    assert 'size' in result['size_error'], result


def test_failed_tar_says_archive_may_exist():
    result = _ops({}, fail_on='tar').create_archive_from_directory('/tmp/src', '/tmp/out.tar.gz')
    assert result['status'] == 'error' and '/tmp/out.tar.gz' in result['message'], result
    assert 'before retrying' in result['message'], result
