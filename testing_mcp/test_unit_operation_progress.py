"""Unit tests for operation progress reporting (models.OperationProgress; no SSH needed)."""
from cygnus_ssh_mcp import models
from cygnus_ssh_mcp.models import OperationProgress, report_progress, set_current_progress


def test_snapshot_is_none_until_something_is_reported():
    assert OperationProgress().snapshot() is None


def test_bytes_and_percent():
    progress = OperationProgress()
    progress.update(stage='uploading file', bytes_done=250, bytes_total=1000)
    snap = progress.snapshot()
    assert snap['stage'] == 'uploading file'
    assert (snap['bytes_done'], snap['bytes_total'], snap['percent']) == (250, 1000, 25.0)
    assert 'last_update' in snap and snap['seconds_since_update'] >= 0


def test_new_stage_resets_byte_counters():
    progress = OperationProgress()
    progress.update(stage='uploading archive', bytes_done=900, bytes_total=1000)
    progress.update(stage='extracting on host')
    snap = progress.snapshot()
    assert snap['stage'] == 'extracting on host' and 'bytes_done' not in snap


def test_item_counts():
    progress = OperationProgress()
    progress.update(stage='searching file contents')
    progress.update(items={'files_searched': 42})
    assert progress.snapshot()['files_searched'] == 42


def test_report_progress_outside_an_operation_does_nothing():
    set_current_progress(None)
    report_progress(stage='x', bytes_done=1, bytes_total=2)  # must not raise


def test_report_progress_never_raises(monkeypatch):
    progress = OperationProgress()

    def broken(*args, **kwargs):
        raise RuntimeError("progress bug")

    monkeypatch.setattr(progress, 'update', broken)
    set_current_progress(progress)
    try:
        report_progress(stage='uploading file')
        models.sftp_progress_callback(10, 100)
    finally:
        set_current_progress(None)


def test_sftp_callback_reports_bytes():
    progress = OperationProgress()
    set_current_progress(progress)
    try:
        report_progress(stage='downloading file')
        models.sftp_progress_callback(512, 2048)
    finally:
        set_current_progress(None)
    snap = progress.snapshot()
    assert (snap['bytes_done'], snap['bytes_total'], snap['percent']) == (512, 2048, 25.0)
