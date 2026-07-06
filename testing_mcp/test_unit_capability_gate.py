import pytest
from cygnus_ssh_mcp.models import SshError
from cygnus_ssh_mcp.client import parse_capability_probe_output
from cygnus_ssh_mcp.ops.capability_gate import (
    CapabilityGate, require_capability, require_capability_when_sudo,
    require_archive_extract_capabilities, describe_capabilities
)


class _FakeSshClient:
    """Minimal stand-in - CapabilityGate only ever reads .capabilities."""
    def __init__(self, capabilities=None):
        self.capabilities = capabilities or {}


class _Wrapped:
    """Stand-in for a real ops instance - some methods, one plain attribute."""
    def __init__(self):
        self.some_property = "not callable"

    def guarded_method(self, *args, **kwargs):
        return "real result"

    def unguarded_method(self):
        return "unguarded result"


def test_gate_blocks_when_capability_confirmed_missing():
    ssh_client = _FakeSshClient(capabilities={'find_printf': False})
    gate = CapabilityGate(_Wrapped(), ssh_client, {
        'guarded_method': require_capability('find_printf', "find -printf missing")
    })
    with pytest.raises(SshError, match="find -printf missing"):
        gate.guarded_method()


def test_gate_allows_when_capability_confirmed_present():
    ssh_client = _FakeSshClient(capabilities={'find_printf': True})
    gate = CapabilityGate(_Wrapped(), ssh_client, {
        'guarded_method': require_capability('find_printf', "find -printf missing")
    })
    assert gate.guarded_method() == "real result"


def test_gate_allows_when_capability_unconfirmed():
    """A probe hiccup (key simply absent) must never block an operation that
    would otherwise have worked - only a *confirmed* False blocks anything.
    """
    ssh_client = _FakeSshClient(capabilities={})
    gate = CapabilityGate(_Wrapped(), ssh_client, {
        'guarded_method': require_capability('find_printf', "find -printf missing")
    })
    assert gate.guarded_method() == "real result"


def test_gate_passes_through_unguarded_methods_regardless_of_capabilities():
    ssh_client = _FakeSshClient(capabilities={'find_printf': False})
    gate = CapabilityGate(_Wrapped(), ssh_client, {
        'guarded_method': require_capability('find_printf', "find -printf missing")
    })
    assert gate.unguarded_method() == "unguarded result"


def test_gate_passes_through_non_callable_attributes():
    ssh_client = _FakeSshClient()
    gate = CapabilityGate(_Wrapped(), ssh_client, {})
    assert gate.some_property == "not callable"


def test_gate_raises_attribute_error_for_missing_attribute():
    ssh_client = _FakeSshClient()
    gate = CapabilityGate(_Wrapped(), ssh_client, {})
    with pytest.raises(AttributeError):
        gate.does_not_exist()


def test_require_capability_when_sudo_only_blocks_when_sudo_true_kwarg():
    guard = require_capability_when_sudo('ps_pgid', "ps -o pgid= missing", sudo_arg_index=2)
    ssh_client = _FakeSshClient(capabilities={'ps_pgid': False})

    # sudo=False (kwarg) - never blocked, even though the capability is missing.
    assert guard(ssh_client, (123, 15), {'sudo': False}) is None
    # sudo=True (kwarg) - blocked.
    assert guard(ssh_client, (123, 15), {'sudo': True}) == "ps -o pgid= missing"


def test_require_capability_when_sudo_only_blocks_when_sudo_true_positional():
    guard = require_capability_when_sudo('ps_pgid', "ps -o pgid= missing", sudo_arg_index=2)
    ssh_client = _FakeSshClient(capabilities={'ps_pgid': False})

    # sudo passed positionally as the 3rd arg (index 2), matching kill_task's
    # actual call site in client.py.
    assert guard(ssh_client, (123, 15, False), {}) is None
    assert guard(ssh_client, (123, 15, True), {}) == "ps -o pgid= missing"


def test_require_capability_when_sudo_allows_when_capability_unconfirmed():
    guard = require_capability_when_sudo('ps_pgid', "ps -o pgid= missing", sudo_arg_index=2)
    ssh_client = _FakeSshClient(capabilities={})
    assert guard(ssh_client, (123, 15, True), {}) is None


def test_archive_extract_guard_blocks_on_missing_strip_components_regardless_of_overwrite():
    guard = require_archive_extract_capabilities(
        'tar_strip_components', "strip-components missing",
        'tar_keep_old_files', "keep-old-files missing",
        overwrite_arg_index=2
    )
    ssh_client = _FakeSshClient(capabilities={'tar_strip_components': False, 'tar_keep_old_files': True})
    # Even with overwrite=True, --strip-components is always used, so a
    # confirmed-missing strip-components must still block.
    assert guard(ssh_client, ('a', 'b', True), {}) == "strip-components missing"
    assert guard(ssh_client, ('a', 'b', False), {}) == "strip-components missing"


def test_archive_extract_guard_blocks_on_missing_keep_old_files_only_without_overwrite():
    guard = require_archive_extract_capabilities(
        'tar_strip_components', "strip-components missing",
        'tar_keep_old_files', "keep-old-files missing",
        overwrite_arg_index=2
    )
    ssh_client = _FakeSshClient(capabilities={'tar_strip_components': True, 'tar_keep_old_files': False})
    # overwrite=False (or omitted) - --keep-old-files gets added, so a
    # confirmed-missing keep-old-files must block.
    assert guard(ssh_client, ('a', 'b', False), {}) == "keep-old-files missing"
    # overwrite=True - --keep-old-files is never added, so this must NOT block
    # even though the capability is confirmed missing.
    assert guard(ssh_client, ('a', 'b', True), {}) is None


def test_archive_extract_guard_allows_when_both_present():
    guard = require_archive_extract_capabilities(
        'tar_strip_components', "strip-components missing",
        'tar_keep_old_files', "keep-old-files missing",
        overwrite_arg_index=2
    )
    ssh_client = _FakeSshClient(capabilities={'tar_strip_components': True, 'tar_keep_old_files': True})
    assert guard(ssh_client, ('a', 'b', False), {}) is None
    assert guard(ssh_client, ('a', 'b', True), {}) is None


def test_parse_capability_probe_output_basic():
    output = "bash:yes\nfind_printf:no\nstat_c:yes\n"
    parsed = parse_capability_probe_output(output)
    assert parsed == {'bash': True, 'find_printf': False, 'stat_c': True}


def test_parse_capability_probe_output_ignores_malformed_lines():
    output = "bash:yes\nsome random noise\nfind_printf:maybe\ndu_sb:no\n"
    parsed = parse_capability_probe_output(output)
    # 'find_printf:maybe' isn't 'yes'/'no' - must be dropped, not guessed.
    assert parsed == {'bash': True, 'du_sb': False}


def test_parse_capability_probe_output_empty():
    assert parse_capability_probe_output("") == {}


def test_describe_capabilities_only_reports_confirmed_missing():
    capabilities = {'find_printf': False, 'bash': True, 'ps_pgid': False}
    warnings = describe_capabilities(capabilities)
    assert len(warnings) == 2
    assert any('find' in w.lower() for w in warnings)
    assert any('ps' in w.lower() for w in warnings)
    assert not any('bash' in w.lower() for w in warnings)


def test_describe_capabilities_empty_for_all_present():
    assert describe_capabilities({'find_printf': True, 'bash': True}) == []
