"""Progress of long-running operations, shown by ssh_cmd_check_status while they run.

A 46-minute macOS upload over slow Wi-Fi (2026-09-27) reported only "running" the whole
time - impossible to tell "slow but moving" from "stuck". Running operations now report
a stage, bytes done/total for transfers, and when progress last changed.

To make "a transfer is visibly in flight" deterministic, the SFTP progress callback is
wrapped so each chunk takes a few milliseconds (an 8 MB upload then takes seconds).
"""
import os
import time
import json
import shutil
import tempfile
import logging

import pytest
from conftest import (
    print_test_header, print_test_footer, make_connection, disconnect_ssh,
    mcp_test_environment, extract_result_text, TEST_WORKSPACE, PATH_SEP, cleanup_command
)

from cygnus_ssh_mcp import server, models
from cygnus_ssh_mcp.ops import file as file_ops
from cygnus_ssh_mcp.server import mcp
from fastmcp import Client

logger = logging.getLogger(__name__)

SIZE = 8 * 1024 * 1024


def _json(result):
    return json.loads(extract_result_text(result))


def _slow_sftp(monkeypatch, per_chunk=0.02):
    original = models.sftp_progress_callback

    def slow(done, total):
        time.sleep(per_chunk)
        original(done, total)

    monkeypatch.setattr(file_ops, 'sftp_progress_callback', slow)


async def _poll_progress(client, handle_id, timeout=120):
    """Poll until done; return (list of progress snapshots seen while running, final status)."""
    seen = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = _json(await client.call_tool("ssh_cmd_check_status", {"handle_id": handle_id, "wait_seconds": 0.5}))
        if status['status'] != 'running':
            return seen, status
        assert 'elapsed_seconds' in status, status
        if status.get('progress'):
            seen.append(status['progress'])
    raise AssertionError("operation didn't finish")


def _local_file(size):
    fd, path = tempfile.mkstemp(prefix='progress_test_')
    with os.fdopen(fd, 'wb') as f:
        f.write(os.urandom(size))
    return path


@pytest.mark.asyncio
async def test_file_transfer_reports_advancing_bytes(mcp_test_environment, monkeypatch):
    print_test_header("Testing progress of a file upload")
    local = _local_file(SIZE)
    remote = f"{TEST_WORKSPACE}{PATH_SEP}progress_upload_{int(time.time())}.bin"
    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            monkeypatch.setattr(server, 'max_foreground_wait', 1.0)
            _slow_sftp(monkeypatch)
            first = _json(await client.call_tool("ssh_file_transfer", {
                "direction": "upload", "local_path": local, "remote_path": remote}))
            assert first['status'] == 'in_progress', first
            assert first.get('progress', {}).get('stage') == 'uploading file', first

            seen, final = await _poll_progress(client, first['handle_id'])
            assert final['status'] == 'completed', final
            uploads = [p for p in seen if p.get('stage') == 'uploading file' and 'bytes_done' in p]
            assert len(uploads) >= 2, f"expected several progress snapshots, got {seen}"
            assert uploads[-1]['bytes_done'] > uploads[0]['bytes_done'], "bytes didn't advance"
            assert all(p['bytes_total'] == SIZE and 0 <= p['percent'] <= 100 for p in uploads), uploads
            assert all(p['seconds_since_update'] < 10 for p in uploads), uploads

            monkeypatch.setattr(server, 'max_foreground_wait', 50.0)
            stat = _json(await client.call_tool("ssh_file_stat", {"path": remote}))
            assert stat.get('size') == SIZE, stat
        finally:
            monkeypatch.setattr(server, 'max_foreground_wait', 50.0)
            await client.call_tool("ssh_cmd_run", {"command": cleanup_command(remote)})
            os.unlink(local)
            await disconnect_ssh(client)
            print_test_footer()


@pytest.mark.asyncio
async def test_dir_transfer_reports_stages(mcp_test_environment, monkeypatch):
    print_test_header("Testing progress stages of a directory upload")
    local_dir = tempfile.mkdtemp(prefix='progress_dir_')
    with open(os.path.join(local_dir, 'big.bin'), 'wb') as f:
        f.write(os.urandom(SIZE))
    remote = f"{TEST_WORKSPACE}{PATH_SEP}progress_dir_{int(time.time())}"
    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            monkeypatch.setattr(server, 'max_foreground_wait', 1.0)
            _slow_sftp(monkeypatch)
            first = _json(await client.call_tool("ssh_dir_transfer", {
                "direction": "upload", "local_path": local_dir, "remote_path": remote}))
            assert first['status'] == 'in_progress', first
            seen, final = await _poll_progress(client, first['handle_id'])
            assert final['status'] == 'completed' and final['result']['success'], final
            stages = [p['stage'] for p in seen]
            assert 'uploading archive' in stages, f"stages seen: {stages}"
            assert any('bytes_done' in p for p in seen if p['stage'] == 'uploading archive'), seen
        finally:
            monkeypatch.setattr(server, 'max_foreground_wait', 50.0)
            await client.call_tool("ssh_cmd_run", {"command": cleanup_command(remote), "wait_timeout": 45})
            shutil.rmtree(local_dir, ignore_errors=True)
            await disconnect_ssh(client)
            print_test_footer()


@pytest.mark.asyncio
async def test_broken_progress_reporting_cannot_break_a_transfer(mcp_test_environment, monkeypatch):
    print_test_header("Testing that a progress bug can't break a transfer")
    local = _local_file(1024 * 1024)
    remote = f"{TEST_WORKSPACE}{PATH_SEP}progress_broken_{int(time.time())}.bin"

    def broken(self, *args, **kwargs):
        raise RuntimeError("progress bug")

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            monkeypatch.setattr(models.OperationProgress, 'update', broken)
            result = _json(await client.call_tool("ssh_file_transfer", {
                "direction": "upload", "local_path": local, "remote_path": remote}))
            if result.get('status') == 'in_progress':
                _, final = await _poll_progress(client, result['handle_id'])
                result = final['result']
            assert result.get('success') is not False and 'error' not in result, result
            stat = _json(await client.call_tool("ssh_file_stat", {"path": remote}))
            assert stat.get('size') == 1024 * 1024, stat
        finally:
            await client.call_tool("ssh_cmd_run", {"command": cleanup_command(remote)})
            os.unlink(local)
            await disconnect_ssh(client)
            print_test_footer()
