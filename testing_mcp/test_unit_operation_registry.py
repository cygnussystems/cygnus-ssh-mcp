"""Unit tests for the long-operation registry in server.py (no SSH needed)."""
from cygnus_ssh_mcp import server


def _op(op_id, done=True):
    op = server._Operation(op_id, 'ssh_test_tool', f'op {op_id}')
    if done:
        op.done.set()
    return op


def test_registry_keeps_at_most_the_limit_of_finished_operations(monkeypatch):
    monkeypatch.setattr(server, '_operations', server.OrderedDict())
    for i in range(server._MAX_OPERATIONS_KEPT + 10):
        server._remember_operation(_op(i))
    assert len(server._operations) == server._MAX_OPERATIONS_KEPT
    assert next(iter(server._operations)) == 10  # the oldest ones went first


def test_registry_never_evicts_a_running_operation(monkeypatch):
    monkeypatch.setattr(server, '_operations', server.OrderedDict())
    running = _op(0, done=False)
    server._remember_operation(running)
    for i in range(1, server._MAX_OPERATIONS_KEPT + 10):
        server._remember_operation(_op(i))
    assert 0 in server._operations, "a still-running operation must stay pollable"


def test_summary_never_contains_secrets_or_file_content():
    summary = server._summarize_args({
        'path': '/etc/app.conf', 'content': 'SECRET-FILE-BODY', 'password': 'hunter2',
        'sudo_password': 'hunter3', 'key_passphrase': 'hunter4', 'use_sudo': False})
    for secret in ('SECRET-FILE-BODY', 'hunter2', 'hunter3', 'hunter4'):
        assert secret not in summary, summary
    assert "path='/etc/app.conf'" in summary


def test_operation_status_shapes():
    op = _op(7, done=False)
    assert server._operation_status_response(op, 1.0)['status'] == 'running'
    op.result = {'ok': True}
    op.done.set()
    done = server._operation_status_response(op, 1.0)
    assert done['status'] == 'completed' and done['result'] == {'ok': True}
    failed = _op(8)
    failed.error = RuntimeError("boom")
    response = server._operation_status_response(failed, 1.0)
    assert response['status'] == 'failed' and response['error'] == 'boom'


async def test_result_stays_collectable_after_a_direct_return(monkeypatch):
    """A client can give up on a request (its own timeout) before the operation finishes and
    the server returns the result - which then reaches nobody. The result must stay
    collectable with ssh_cmd_check_status: it used to be removed from the registry on that
    direct return, so polling fell through to 'not found' (cross-platform matrix, 2026-09-29)."""
    import json
    from fastmcp import Client
    monkeypatch.setattr(server, '_operations', server.OrderedDict())

    @server.operation_tool
    async def ssh_fake_quick_tool(path: str) -> dict:
        return {'size_bytes': 42}

    assert await ssh_fake_quick_tool(path='/data') == {'size_bytes': 42}
    op_id, op = next(iter(server._operations.items()))
    assert op.returned_directly is True

    async with Client(server.mcp) as client:
        status = json.loads((await client.call_tool(
            "ssh_cmd_check_status", {"handle_id": op_id, "wait_seconds": 0.1})).content[0].text)
    assert status['status'] == 'completed' and status['result'] == {'size_bytes': 42}, status
