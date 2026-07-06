import pytest
from cygnus_ssh_mcp.ops.task import SshTaskOperations_Linux


class _FakeSshClient:
    """Minimal stand-in - _build_launch_script only reads .capabilities and
    .sudo_password."""
    def __init__(self, capabilities=None, sudo_password=None):
        self.capabilities = capabilities if capabilities is not None else {}
        self.sudo_password = sudo_password


def test_build_launch_script_prefers_bash_when_confirmed_present():
    """Regression test: live-verified against Alpine (BusyBox, no bash) and
    FreeBSD (bash not installed by default) that the launch script used to
    hardcode #!/bin/bash and invoke itself via the bare script path, relying
    on the kernel's shebang-exec - which fails with a confusing "not found"
    when /bin/bash doesn't exist. Confirmed-present bash should still be used
    for the user's own command, preserving full bash-feature support.
    """
    task_ops = SshTaskOperations_Linux(_FakeSshClient(capabilities={'bash': True}))
    execution_cmd, script_content, _ = task_ops._build_launch_script(
        "echo hi", None, None, sudo=False
    )
    assert execution_cmd.startswith("sh "), (
        f"The wrapper script itself must always be invoked via sh (confirmed "
        f"present by every capability probe), not rely on a bash shebang - "
        f"got: {execution_cmd!r}"
    )
    assert "bash -c 'echo hi'" in script_content


def test_build_launch_script_falls_back_to_sh_when_bash_confirmed_missing():
    task_ops = SshTaskOperations_Linux(_FakeSshClient(capabilities={'bash': False}))
    execution_cmd, script_content, _ = task_ops._build_launch_script(
        "echo hi", None, None, sudo=False
    )
    assert execution_cmd.startswith("sh ")
    assert "sh -c 'echo hi'" in script_content
    assert "bash -c 'echo hi'" not in script_content


def test_build_launch_script_prefers_bash_when_unconfirmed():
    """macOS/Windows never populate .capabilities at all (empty dict) - an
    unconfirmed capability must default to 'assume present', preserving
    today's exact behavior on those platforms (this method is also reused
    as-is for macOS's task_ops, same as before this fix)."""
    task_ops = SshTaskOperations_Linux(_FakeSshClient(capabilities={}))
    execution_cmd, script_content, _ = task_ops._build_launch_script(
        "echo hi", None, None, sudo=False
    )
    assert "bash -c 'echo hi'" in script_content


def test_build_launch_script_execution_cmd_uses_actual_script_path():
    task_ops = SshTaskOperations_Linux(_FakeSshClient(capabilities={'bash': False}))
    execution_cmd, script_content, create_script_cmd = task_ops._build_launch_script(
        "echo hi", None, None, sudo=False
    )
    # The script's own self-cleanup ("rm -f <path>") must reference the same
    # bare path that's embedded in execution_cmd ("sh <path>").
    assert execution_cmd.startswith("sh /tmp/launch_script_")
    bare_path = execution_cmd[len("sh "):]
    assert f"rm -f {bare_path}" in script_content


def test_build_launch_script_sudo_with_password_falls_back_to_sh():
    task_ops = SshTaskOperations_Linux(
        _FakeSshClient(capabilities={'bash': False}, sudo_password="secret")
    )
    execution_cmd, script_content, _ = task_ops._build_launch_script(
        "echo hi", None, None, sudo=True
    )
    assert execution_cmd.startswith("sh ")
    assert "sh -c 'echo" in script_content  # outer plumbing wrapper
    assert 'sh -c "$__SUDO_CMD"' in script_content  # innermost user-command invocation
    assert "bash" not in script_content


def test_build_launch_script_sudo_passwordless_prefers_bash_when_present():
    task_ops = SshTaskOperations_Linux(
        _FakeSshClient(capabilities={'bash': True}, sudo_password=None)
    )
    execution_cmd, script_content, _ = task_ops._build_launch_script(
        "echo hi", None, None, sudo=True
    )
    assert 'bash -c "$__SUDO_CMD"' in script_content
