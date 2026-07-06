import pytest
from cygnus_ssh_mcp.models import CommandHandle
from cygnus_ssh_mcp.ops.run import SshRunOperations_Linux


class _DummySshClient:
    """Minimal stand-in - _feed_output_chunk/_flush_pending_output never touch ssh_client."""
    pass


@pytest.fixture
def run_ops():
    return SshRunOperations_Linux(_DummySshClient())


def test_feed_output_chunk_single_byte_fragments_no_spurious_newlines(run_ops):
    """Regression test for the character-split output bug (found by GPT-5.5's
    2026-07-06 command-stress test): curl's unbuffered stderr arrived as tiny
    fragments, and the pre-fix code did `line if line.endswith('\\n') else
    line + '\\n'` on every chunk in isolation - synthesizing a fake newline
    after every single-byte fragment and corrupting
    "curl: (7) Failed to connect to host" into "c\\nu\\nr\\nl\\n:\\n...".
    """
    handle = CommandHandle(1, "test")
    text = "curl: (7) Failed to connect to host\n"
    for ch in text:
        run_ops._feed_output_chunk(handle, ch, is_stderr=True)

    assert handle.get_full_stderr() == text, (
        f"Expected the full line reassembled with a single trailing newline, got "
        f"{handle.get_full_stderr()!r} - byte-at-a-time chunks should never "
        f"synthesize one newline per fragment"
    )


def test_feed_output_chunk_leaves_trailing_partial_line_pending(run_ops):
    """A chunk that ends mid-line (no trailing newline yet) must not emit
    anything for that fragment - it should stay buffered until a later chunk
    (or an explicit flush) completes it.
    """
    handle = CommandHandle(2, "test")
    run_ops._feed_output_chunk(handle, "partial line, no newline yet", is_stderr=False)

    assert handle.get_full_output() == '', (
        "An incomplete line must not be emitted early with a synthetic newline"
    )
    assert handle._pending_stdout == "partial line, no newline yet"

    run_ops._feed_output_chunk(handle, " - now complete\n", is_stderr=False)
    assert handle.get_full_output() == "partial line, no newline yet - now complete\n"
    assert handle._pending_stdout == ''


def test_flush_pending_output_emits_trailing_fragment_without_fake_newline(run_ops):
    """_flush_pending_output must emit a still-buffered partial line as-is (e.g.
    a prompt with no trailing newline) once a command is confirmed done -
    without adding a newline that was never actually there.
    """
    handle = CommandHandle(3, "test")
    run_ops._feed_output_chunk(handle, "$ ", is_stderr=False)
    assert handle.get_full_output() == ''

    run_ops._flush_pending_output(handle)
    assert handle.get_full_output() == "$ ", (
        "The trailing fragment should be flushed exactly as received, with no "
        "synthetic newline appended"
    )
    assert handle._pending_stdout == ''

    # Safe to call again once drained - no-op, no duplicate emission.
    run_ops._flush_pending_output(handle)
    assert handle.get_full_output() == "$ "


def test_feed_output_chunk_accepts_raw_bytes(run_ops):
    """The recv()-driven call sites pass raw bytes, not str - confirm decoding
    happens correctly and a trailing incomplete line is still held back.
    """
    handle = CommandHandle(4, "test")
    run_ops._feed_output_chunk(handle, b"line one\nline two", is_stderr=False)
    assert handle.get_full_output() == "line one\n"
    assert handle._pending_stdout == "line two"
