"""Can these hosts be administered remotely through the MCP? Walks the everyday admin
workflow on every limited host (incl. the production Synology NAS - everything here stays in
the session's scratch folder, and privileged checks are read-only):

- inspect the system; run privileged read-only commands
- long commands: hand-off, status, output, kill; background tasks: launch, status, kill
- file administration: search, list, grep, copy, move, mkdir, batch delete, remove,
  archive round trip

The bar for every tool: either the right answer (verified independently via the shell), or a
clear error that says what to use instead. A wrong answer - or a "success" that did nothing,
or did it somewhere else - fails the test.
"""
import asyncio
import json

import pytest

NO_SFTP = "SFTP is not available on this host"


def _dump(result):
    return json.dumps(result, ensure_ascii=False)


def _refused_without_sftp(s, result):
    """True if the tool clearly refused because this host lacks what it needs - no usable
    SFTP, or a confirmed-missing capability (e.g. BusyBox find without -printf) - and said
    what to do instead. That's acceptable; a wrong answer is not."""
    text = _dump(result)
    if not s.host.has_sftp and NO_SFTP in text and "ssh_cmd_run" in text:
        return True
    return "This operation needs" in text and any(hint in text for hint in ("instead", "Pass ", "ssh_cmd_run"))


def _handle(result):
    return result.get('handle_id') or result.get('id')


async def _exists(s, path, kind='e'):
    return (await s.sh(f"test -{kind} {path} && echo YES || echo NO")).strip() == "YES"


async def _wait_until_done(s, handle_id, timeout=30):
    for _ in range(timeout):
        status = await s.call("ssh_cmd_check_status", {"handle_id": handle_id, "wait_seconds": 1})
        if status.get('status') != 'running':
            return status
    raise AssertionError(f"command {handle_id} still running after {timeout}s")


# --- inspection and privileged (read-only) commands --------------------------------------

async def test_inspect_system(session):
    s = session
    info = await s.call("ssh_conn_host_info", {})
    system = info.get('system', {})
    for key in ('user', 'hostname', 'kernel', 'mem_total_mb', 'disk_total'):
        assert system.get(key) not in (None, '', 'n/a'), (key, system)
    out = await s.sh("uptime; df -h / | tail -1; ps | head -3")
    assert 'load average' in out, out


async def test_privileged_read_only_commands(session):
    s = session
    if not s.host.has_sudo:
        r = await s.call("ssh_cmd_run", {"command": "id -u", "use_sudo": True})
        assert r.get('status') != 'success' and 'sudo' in _dump(r).lower(), r  # clear refusal
        return
    sudo = await s.call("ssh_conn_verify_sudo", {})
    assert sudo.get('available') is True, sudo
    assert (await s.sh("id -u", use_sudo=True)).strip() == "0"
    # a root-only read: listing /root (names are not printed)
    assert (await s.sh("ls -a /root >/dev/null && echo READABLE", use_sudo=True)).strip() == "READABLE"


# --- long-running commands and background tasks ------------------------------------------

async def test_long_command_hands_off_and_completes(session):
    s = session
    r = await s.call("ssh_cmd_run", {"command": "sleep 4; echo finished-ok", "wait_timeout": 1})
    assert r.get('status') == 'wait_timeout' and r.get('still_running') is True, r
    done = await _wait_until_done(s, _handle(r))
    output = await s.call("ssh_cmd_output", {"handle_id": _handle(r)})
    assert 'finished-ok' in _dump(output) or 'finished-ok' in _dump(done), (done, output)


async def test_running_command_can_be_killed(session):
    s = session
    r = await s.call("ssh_cmd_run", {"command": "sleep 120", "wait_timeout": 1})
    assert r.get('status') == 'wait_timeout', r
    killed = await s.call("ssh_cmd_kill", {"handle_id": _handle(r)})
    assert killed.get('result') in ('killed', 'terminated', 'already_exited'), killed
    status = await s.call("ssh_cmd_check_status", {"handle_id": _handle(r), "wait_seconds": 2})
    assert status.get('status') != 'running', status


async def test_background_task_lifecycle(session):
    s = session
    log = f"{s.workdir}/task.log"
    launched = await s.call("ssh_task_launch", {"command": "echo started; sleep 120", "stdout_log": log})
    pid = launched.get('pid')
    assert pid, launched
    await asyncio.sleep(1)
    assert (await s.call("ssh_task_status", {"pid": pid})).get('status') == 'running'
    assert (await s.sh(f"cat {log}")).strip() == "started"
    killed = await s.call("ssh_task_kill", {"pid": pid})
    assert killed.get('result') in ('killed', 'terminated'), killed
    await asyncio.sleep(1)
    assert (await s.call("ssh_task_status", {"pid": pid})).get('status') != 'running'
    assert (await s.sh(f"kill -0 {pid} 2>/dev/null && echo ALIVE || echo GONE")).strip() == "GONE"


# --- file administration ------------------------------------------------------------------

async def _fixture_tree(s):
    d = s.workdir
    await s.sh(f"mkdir -p {d}/etc/sub && printf 'port=22\\nneedle=found-it\\nmode=on\\n' > {d}/etc/app.conf && "
               f"printf 'other=1\\n' > {d}/etc/sub/other.conf && printf 'x' > {d}/etc/old1.bak && "
               f"printf 'y' > {d}/etc/sub/old2.bak")
    return f"{d}/etc"


async def test_find_and_inspect_files(session):
    s = session
    etc = await _fixture_tree(s)
    conf = f"{etc}/app.conf"

    r = await s.call("ssh_dir_search_glob", {"path": etc, "pattern": "*.conf"})
    if not _refused_without_sftp(s, r):
        paths = sorted(e['path'] for e in r) if isinstance(r, list) else r
        assert paths == [conf, f"{etc}/sub/other.conf"], r

    r = await s.call("ssh_dir_list_advanced", {"path": etc, "max_depth": 3})
    if not _refused_without_sftp(s, r):
        assert isinstance(r, list) and {conf, f"{etc}/sub/old2.bak"} <= {e['path'] for e in r}, r

    r = await s.call("ssh_dir_search_files_content", {"dir_path": etc, "pattern": "found-it"})
    if not _refused_without_sftp(s, r):
        assert conf in _dump(r) and 'other.conf' not in _dump(r), r

    r = await s.call("ssh_file_find_lines_with_pattern", {"file_path": conf, "pattern": "needle"})
    if not _refused_without_sftp(s, r):
        assert r.get('total_matches') == 1 and 'found-it' in _dump(r), r

    r = await s.call("ssh_file_get_context_around_line", {"file_path": conf, "match_line": "needle=found-it",
                                                         "context": 1})
    if not _refused_without_sftp(s, r):
        assert 'port=22' in _dump(r) and 'mode=on' in _dump(r), r


async def test_change_files(session):
    s = session
    etc = await _fixture_tree(s)
    conf, copy, moved = f"{etc}/app.conf", f"{etc}/app.copy", f"{etc}/app.moved"

    r = await s.call("ssh_file_copy", {"source_path": conf, "destination_path": copy})
    if not _refused_without_sftp(s, r):
        assert await s.sh(f"cat {copy}") == await s.sh(f"cat {conf}"), r
        r = await s.call("ssh_file_move", {"source": copy, "destination": moved})
        if not _refused_without_sftp(s, r):
            assert not await _exists(s, copy) and await _exists(s, moved, 'f'), r

    newdir = f"{etc}/newdir"
    r = await s.call("ssh_dir_mkdir", {"path": newdir})
    if _refused_without_sftp(s, r):
        await s.sh(f"mkdir {newdir}")
    else:
        assert await _exists(s, newdir, 'd'), r

    r = await s.call("ssh_dir_batch_delete_files", {"path": etc, "pattern": "*.bak", "dry_run": False})
    if not _refused_without_sftp(s, r):
        assert (await s.sh(f"find {etc} -name '*.bak' | wc -l")).strip() == "0", r
        assert await _exists(s, conf, 'f'), "batch delete removed a file that didn't match"

    r = await s.call("ssh_dir_remove", {"path": newdir})
    if not _refused_without_sftp(s, r):
        assert not await _exists(s, newdir), r

    r = await s.call("ssh_dir_delete", {"path": f"{etc}/sub", "dry_run": True})
    assert await _exists(s, f"{etc}/sub", 'd'), "a dry run deleted something"
    r = await s.call("ssh_dir_delete", {"path": f"{etc}/sub", "dry_run": False})
    if not _refused_without_sftp(s, r):
        assert not await _exists(s, f"{etc}/sub") and await _exists(s, conf, 'f'), r


async def test_archive_round_trip(session):
    s = session
    etc = await _fixture_tree(s)
    archive, dest = f"{s.workdir}/backup.tar.gz", f"{s.workdir}/restore"
    r = await s.call("ssh_archive_create", {"source_path": etc, "archive_path": archive})
    assert r.get('status') == 'success' and await _exists(s, archive, 'f'), r

    r = await s.call("ssh_archive_extract", {"archive_path": archive, "destination_path": dest})
    if _refused_without_sftp(s, r) and "overwrite=True" in _dump(r):
        # BusyBox tar without --keep-old-files: follow the advice (dest is new and empty)
        r = await s.call("ssh_archive_extract", {"archive_path": archive, "destination_path": dest,
                                                 "overwrite": True})
    if _refused_without_sftp(s, r):
        # e.g. tar without --strip-components: refused up front with a manual fallback
        assert 'strip-components' in _dump(r) and not await _exists(s, dest), r
    else:
        assert r.get('status') == 'success', r
        restored = await s.sh(f"cat {dest}/app.conf 2>/dev/null || cat {dest}/etc/app.conf")
        assert 'needle=found-it' in restored, r
