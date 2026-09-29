"""Remote paths are split by the TARGET's rules, never the local machine's. On a macOS/Linux
client, os.path.dirname('C:\\a\\b.txt') is '' - so ssh_file_write(create_dirs=True) never
created a Windows file's parent directories (cross-platform matrix, macOS runner -> Windows,
2026-09-29). Offline: simulates a POSIX client by swapping the local path module."""
import posixpath
from types import SimpleNamespace

from cygnus_ssh_mcp import server


def _target(monkeypatch, os_type):
    monkeypatch.setattr(server.mcp, 'ssh_client', SimpleNamespace(os_type=os_type), raising=False)
    monkeypatch.setattr(server.os, 'path', posixpath)  # as on a macOS/Linux client


def test_windows_parent_dir_on_a_posix_client(monkeypatch):
    _target(monkeypatch, 'windows')
    assert server._remote_dirname('C:\\Users\\claude\\ws\\nested\\file.txt') == 'C:\\Users\\claude\\ws\\nested'
    assert server._remote_dirname('C:/Users/claude/file.txt') == 'C:/Users/claude'


def test_posix_parent_dir(monkeypatch):
    _target(monkeypatch, 'linux')
    assert server._remote_dirname('/home/test/a b/file.txt') == '/home/test/a b'
