"""Regression tests for issues/2026-09-26-*.md:

- ssh_task_launch(use_sudo=True) must work when the log's directory is root-owned (the
  launcher used to open the log in the unprivileged shell, so the job silently never ran
  but a PID was still returned)
- a log that can't be created must fail the launch with a clear error, not return a PID
- a sudo task with the default log path still ends up at the returned /tmp/task-<pid>.log
- ssh_cmd_check_status clamps wait_seconds to the per-call wait cap
"""
import pytest
import json
import asyncio
import logging
import time
from conftest import (
    print_test_header, print_test_footer, make_connection, disconnect_ssh,
    mcp_test_environment, extract_result_text, sleep_then_echo, skip_on_windows
)

from cygnus_ssh_mcp import server
from cygnus_ssh_mcp.server import mcp
from fastmcp import Client

logger = logging.getLogger(__name__)


def _json(result):
    return json.loads(extract_result_text(result))


async def _run(client, command, use_sudo=False):
    return _json(await client.call_tool("ssh_cmd_run", {"command": command, "use_sudo": use_sudo}))


async def _wait_until_exited(client, pid, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = _json(await client.call_tool("ssh_task_status", {"pid": pid}))
        if status['status'] != 'running':
            return status
        await asyncio.sleep(1)
    raise AssertionError(f"task {pid} still running after {timeout}s")


@pytest.mark.asyncio
@skip_on_windows
async def test_sudo_task_log_in_root_owned_dir(mcp_test_environment):
    """A sudo task can write its log inside a root-owned directory."""
    print_test_header("Testing sudo task log in a root-owned directory")
    root_dir = f"/tmp/mcp_root_owned_{int(time.time())}"
    log = f"{root_dir}/task.log"

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            made = await _run(client, f"mkdir -p {root_dir} && chown root {root_dir} && chmod 755 {root_dir}", use_sudo=True)
            assert made['status'] == 'success', made

            launch = _json(await client.call_tool("ssh_task_launch", {
                "command": "echo OUT_LINE; echo ERR_LINE >&2; whoami",
                "use_sudo": True,
                "stdout_log": log,
            }))
            assert launch['stdout_log'] == log and launch['stderr_log'] == log, launch
            await _wait_until_exited(client, launch['pid'])

            content = await _run(client, f"cat {log}", use_sudo=True)
            assert content['status'] == 'success', f"log was never created: {content}"
            assert "OUT_LINE" in content['output'] and "ERR_LINE" in content['output'], content['output']
            assert "root" in content['output'], "task should have run as root"
        finally:
            await _run(client, f"rm -rf {root_dir}", use_sudo=True)
            await disconnect_ssh(client)
            print_test_footer()


@pytest.mark.asyncio
@skip_on_windows
async def test_unwritable_log_fails_launch_clearly(mcp_test_environment):
    """Without sudo, an unwritable log path fails the launch instead of returning a dead PID."""
    print_test_header("Testing task launch with an unwritable log")
    marker = f"/tmp/mcp_should_not_exist_{int(time.time())}"

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            with pytest.raises(Exception) as exc_info:
                await client.call_tool("ssh_task_launch", {
                    "command": f"touch {marker}",
                    "stdout_log": "/nonexistent_dir_for_mcp_test/task.log",
                })
            message = str(exc_info.value)
            assert "NOT launched" in message and "nonexistent_dir_for_mcp_test" in message, message

            await asyncio.sleep(2)
            check = await _run(client, f"test -e {marker} && echo EXISTS || echo ABSENT")
            assert "ABSENT" in check['output'], "the command must not have run"
        finally:
            await _run(client, f"rm -f {marker}")
            await disconnect_ssh(client)
            print_test_footer()


@pytest.mark.asyncio
@skip_on_windows
async def test_sudo_task_default_log_path_exists(mcp_test_environment):
    """With sudo and no log paths given, the returned default log really exists and has the output."""
    print_test_header("Testing sudo task default log path")

    async with Client(mcp) as client:
        log = None
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            launch = _json(await client.call_tool("ssh_task_launch", {
                "command": "echo DEFAULT_OUT; echo DEFAULT_ERR >&2",
                "use_sudo": True,
            }))
            log = launch['stdout_log']
            assert log == launch['stderr_log'] and log.endswith(f"task-{launch['pid']}.log"), launch
            await _wait_until_exited(client, launch['pid'])
            await asyncio.sleep(1)  # the rename to the pid-based name follows launch

            content = await _run(client, f"cat {log}")
            assert content['status'] == 'success', f"default log missing: {content}"
            assert "DEFAULT_OUT" in content['output'] and "DEFAULT_ERR" in content['output'], content['output']
        finally:
            if log:
                await _run(client, f"rm -f {log}", use_sudo=True)
            await disconnect_ssh(client)
            print_test_footer()


@pytest.mark.asyncio
async def test_check_status_wait_is_capped(mcp_test_environment, monkeypatch):
    """ssh_cmd_check_status never waits longer than the per-call cap."""
    print_test_header("Testing ssh_cmd_check_status wait cap")
    monkeypatch.setattr(server, 'max_foreground_wait', 3.0)

    async with Client(mcp) as client:
        try:
            assert await make_connection(client), "Failed to establish SSH connection"
            run = _json(await client.call_tool("ssh_cmd_run", {
                "command": sleep_then_echo(15, "cap-check"), "wait_timeout": 1.0}))
            assert run['status'] == 'wait_timeout', run

            start = time.monotonic()
            status = _json(await client.call_tool(
                "ssh_cmd_check_status", {"handle_id": run['id'], "wait_seconds": 90}))
            elapsed = time.monotonic() - start
            assert elapsed < 10, f"check_status waited {elapsed:.1f}s despite the 3s cap"
            assert status['waited_seconds'] == 3.0, status
            assert status['status'] == 'running', status
            await asyncio.sleep(12)
        finally:
            await disconnect_ssh(client)
            print_test_footer()
