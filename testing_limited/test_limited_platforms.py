"""Behaviors that broke on Alpine / FreeBSD / OpenWrt (issues/2026-09-28-*), checked on the
real hosts. Each test runs once per configured host - see conftest.py."""
import asyncio

import pytest

from cygnus_ssh_mcp import server

# 3 files in 2 subfolders: 26 + 1000 + 7 bytes
TREE_BYTES = 26 + 1000 + 7


async def _make_tree(s):
    src = f"{s.workdir}/src"
    await s.sh(f"mkdir -p {src}/a/b && printf '%026d' 0 > {src}/one.txt && "
               f"printf '%01000d' 0 > {src}/a/two.txt && printf 'seven!\\n' > {src}/a/b/three.txt")
    return src


def _needs_sudo(s):
    if not s.host.has_sudo:
        pytest.skip(f"{s.host.name} has no sudo")


def _needs_sftp(s):
    if not s.host.has_sftp:
        pytest.skip(f"{s.host.name} has no SFTP")


# --- connect: platform detection and capability reporting -------------------------------

async def test_connect_detects_platform_and_capabilities(session):
    s = session
    assert server.mcp.ssh_client.os_type == s.host.os_type
    caps = s.connect_result.get('capabilities', {})
    assert caps.get('sftp') is s.host.has_sftp, caps
    assert caps.get('sudo') is s.host.has_sudo, caps
    warnings = " ".join(s.connect_result.get('capability_warnings', []))
    assert ("SFTP subsystem" in warnings) is (not s.host.has_sftp), warnings


# --- directory size / copy (find -printf fallback; OpenWrt reported bytes_copied: 0) ----

async def test_dir_size_and_copy_report_exact_bytes(session):
    s = session
    src = await _make_tree(s)
    size = await s.call("ssh_dir_calc_size", {"path": src})
    assert size.get('size_bytes') == TREE_BYTES, size

    copied = await s.call("ssh_dir_copy", {"source_path": src, "destination_path": f"{s.workdir}/copy"})
    assert copied.get('files_copied') == 3 and copied.get('bytes_copied') == TREE_BYTES, copied
    tree = await s.sh(f"cd {s.workdir}/copy && find . -type f | sort")
    assert tree.split() == ['./a/b/three.txt', './a/two.txt', './one.txt'], tree


# --- archive create (OpenWrt: no `stat` -> error after the archive was created) ----------

async def test_archive_create_reports_success_and_real_size(session):
    s = session
    src = await _make_tree(s)
    archive = f"{s.workdir}/out.tar.gz"
    result = await s.call("ssh_archive_create", {"source_path": src, "archive_path": archive})
    real = int((await s.sh(f"wc -c < {archive}")).strip())
    assert result.get('status') == 'success' and result.get('size_bytes') == real, (result, real)
    assert 'size_error' not in result, result


# --- file stat / SFTP (OpenWrt: no SFTP -> stat said an existing file didn't exist) ------

async def test_file_stat_is_truthful(session):
    s = session
    await s.sh(f"printf 'hello\\n' > {s.workdir}/f.txt")
    present = await s.call("ssh_file_stat", {"path": f"{s.workdir}/f.txt"})
    missing = await s.call("ssh_file_stat", {"path": f"{s.workdir}/missing.txt"})
    if s.host.has_sftp:
        assert present.get('exists') is True and present.get('size') == 6, present
        assert missing.get('exists') is False, missing
    else:
        # Can't check -> unknown, never "doesn't exist"
        for result in (present, missing):
            assert result.get('exists') is None and "SFTP is not available" in result.get('error', ''), result


async def test_without_sftp_file_tools_point_to_ssh_cmd_run(session):
    s = session
    if s.host.has_sftp:
        pytest.skip(f"{s.host.name} has SFTP")
    await s.sh(f"printf 'hello\\n' > {s.workdir}/f.txt")
    for tool, params in (("ssh_file_read", {"file_path": f"{s.workdir}/f.txt"}),
                         ("ssh_file_write", {"file_path": f"{s.workdir}/g.txt", "content": "x\n"}),
                         ("ssh_dir_list_files_basic", {"path": s.workdir})):
        result = await s.call(tool, params)
        text = str(result)
        assert "SFTP is not available" in text and "ssh_cmd_run" in text, (tool, result)
        assert "EOF during negotiation" not in text, (tool, result)


# --- sudo edits / writes of a root-only file (FreeBSD: false success, owner changed) -----

async def _root_file(s):
    path = f"{s.workdir}/root.conf"
    await s.sh(f"printf 'a=1\\nprivate=before\\nz=9\\n' > {path} && chown 0:0 {path} && chmod 600 {path}",
               use_sudo=True)
    return path


async def _content_and_meta(s, path):
    out = await s.sh(f"cat {path}; echo '---'; ls -ln {path} | awk '{{print $1, $3, $4}}'", use_sudo=True)
    content, meta = out.split('---\n')
    return content, meta.split()


async def test_sudo_line_edits_on_root_only_file(session):
    s = session
    _needs_sudo(s)
    _needs_sftp(s)
    path = await _root_file(s)

    for params in ({"match_line": "private=before", "new_line": "private=after"},
                   {"match_line": "private=after", "new_line": "private=forced", "force": True}):
        r = await s.call("ssh_file_replace_line", {"file_path": path, "use_sudo": True, **params})
        assert r.get('success') is True and 'No changes' not in r.get('message', ''), r
    r = await s.call("ssh_file_replace_line", {"file_path": path, "match_line": "not-there",
                                               "new_line": "x", "use_sudo": True, "force": True})
    assert r.get('success') is False and "not found" in r.get('error', ''), r

    r = await s.call("ssh_file_insert_lines_after_match", {"file_path": path, "match_line": "a=1",
                                                           "lines_to_insert": ["b=2"], "use_sudo": True})
    assert r.get('success') is True, r
    r = await s.call("ssh_file_delete_line_by_content", {"file_path": path, "match_line": "z=9", "use_sudo": True})
    assert r.get('success') is True, r

    content, meta = await _content_and_meta(s, path)
    assert content == "a=1\nb=2\nprivate=forced\n", content
    assert meta == ["-rw-------", "0", "0"], meta


async def test_sudo_write_keeps_existing_owner_and_mode(session):
    s = session
    _needs_sudo(s)
    _needs_sftp(s)
    path = await _root_file(s)

    r = await s.call("ssh_file_write", {"file_path": path, "content": "one\n", "use_sudo": True})
    assert r.get('success'), r
    content, meta = await _content_and_meta(s, path)
    assert content == "one\n" and meta == ["-rw-------", "0", "0"], meta

    r = await s.call("ssh_file_write", {"file_path": path, "content": "two\n", "use_sudo": True, "mode": 0o640})
    assert r.get('success'), r
    content, meta = await _content_and_meta(s, path)
    assert content == "two\n" and meta == ["-rw-r-----", "0", "0"], meta


# --- task launch (Alpine: a failed launch left its launcher script - with the sudo password - in /tmp)

async def _launcher_count(s):
    return int((await s.sh("ls /tmp/launch_script_*.sh 2>/dev/null | wc -l")).strip())


@pytest.mark.parametrize("use_sudo", [False, True], ids=["user", "sudo"])
async def test_failed_task_launch_leaves_no_launcher(session, use_sudo):
    """Launch fails because the log can't be written - or, for a sudo launch on a host
    without sudo (the original Alpine case), because sudo itself is missing."""
    s = session
    before = await _launcher_count(s)
    marker = f"{s.workdir}/should_not_exist"
    sudo_missing = use_sudo and not s.host.has_sudo
    log = f"{s.workdir}/task.log" if sudo_missing else "/nonexistent_dir_for_mcp_test/task.log"
    result = await s.call("ssh_task_launch", {"command": f"touch {marker}", "use_sudo": use_sudo,
                                              "stdout_log": log})
    assert "NOT launched" in str(result), result
    await asyncio.sleep(2)
    assert (await s.sh(f"test -e {marker} && echo EXISTS || echo ABSENT")).strip() == "ABSENT"
    assert await _launcher_count(s) == before, "failed launch left its launcher script in /tmp"


async def test_task_launch_runs_and_cleans_up(session):
    s = session
    before = await _launcher_count(s)
    result = await s.call("ssh_task_launch", {"command": "echo ok-task", "stdout_log": f"{s.workdir}/ok.log"})
    assert result.get('pid'), result
    for _ in range(20):
        await asyncio.sleep(0.5)
        if (await s.sh(f"cat {s.workdir}/ok.log 2>/dev/null || true")).strip() == "ok-task":
            break
    assert (await s.sh(f"cat {s.workdir}/ok.log")).strip() == "ok-task"
    assert await _launcher_count(s) == before


# --- connect metadata (OpenWrt: empty user/hostname, KiB labelled MB; FreeBSD: "macos", 0 MB)

async def test_connect_metadata_is_truthful(session):
    s = session
    system = s.connect_result.get('system', {})
    user, hostname, kernel_name = (await s.sh("id -un; uname -n; uname -s")).split()
    assert s.connect_result.get('connection', {}).get('user') == user, s.connect_result.get('connection')
    assert system.get('user') == user and system.get('hostname') == hostname, system
    expected_os_type = 'macos' if kernel_name == 'Darwin' else kernel_name.lower()
    assert system.get('os_type') == expected_os_type, system
    assert system.get('os_name') not in (None, '', 'n/a'), system

    # Real memory in MB, from the host's own unit-explicit source
    real_mb = int((await s.sh(
        "if [ -r /proc/meminfo ]; then awk '/^MemTotal:/{print int($2/1024)}' /proc/meminfo; "
        "else echo $(( $(sysctl -n hw.physmem) / 1048576 )); fi")).strip())
    assert int(system.get('mem_total_mb')) == real_mb, (system.get('mem_total_mb'), real_mb)
    for key in ('mem_free_mb', 'mem_available_mb'):
        assert 0 < int(system.get(key)) <= real_mb, (key, system.get(key), real_mb)

    await s.sh(f"rmdir {s.workdir}")  # the fixture can't clean up after the disconnect below
    disconnect = await s.call("ssh_host_disconnect", {})
    assert f"{user}@" in disconnect.get('message', ''), disconnect
