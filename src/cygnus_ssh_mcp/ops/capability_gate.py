"""Capability-gating support for non-standard SSH targets ('flex' platform,
and BusyBox-flavored devices that still detect as os_type='linux').

Design rationale: rather than editing the already-working, already-verified
_Linux/_Mac ops classes to add capability checks inline, CapabilityGate wraps
an existing ops instance and intercepts only the specific methods that depend
on a probed capability (see SshClient._probe_capabilities). Everything else
passes through unmodified via __getattr__ - the wrapped classes are never
touched. See planning/2026-07-06-non-standard-ssh-targets-capability-scoping.md
for the full design history.

Known limitation (deliberately out of scope for this pass): ops/run.py's
_handle_sudo (bash -c wrapping for use_sudo=True) can't be gated this way,
since it's only ever called internally by execute_command on the same
instance - an outer wrapper never sees that call. A bash-less sudo attempt
will fail with a less-clear error than the gated methods below, rather than
a clean capability message.
"""
from cygnus_ssh_mcp.models import SshError


def _extract_arg_flag(args, kwargs, arg_name, arg_index):
    """Best-effort extraction of a named bool argument that might be passed
    positionally or by keyword depending on the call site (confirmed both
    occur across this codebase's actual call sites). Returns None if the
    argument wasn't supplied at all (so the caller can distinguish "not
    passed, so the method's own default applies" from "explicitly False")."""
    if arg_name in kwargs:
        return bool(kwargs[arg_name])
    if arg_index is not None and len(args) > arg_index:
        return bool(args[arg_index])
    return None


def _extract_sudo_flag(args, kwargs, sudo_arg_index):
    """Best-effort extraction of a 'sudo' argument - see _extract_arg_flag.
    Every guarded method here defaults sudo to False, so an unsupplied
    argument is treated as False (unlike _extract_arg_flag's raw None)."""
    return bool(_extract_arg_flag(args, kwargs, 'sudo', sudo_arg_index))


def require_capability(capability_key, message):
    """Guard factory: unconditionally requires a capability to be confirmed
    present. Only a *confirmed* False (from SshClient.capabilities) blocks the
    call - an absent key (probe never ran, or didn't cover this key) is
    treated as 'not confirmed missing', so a probe hiccup can never regress an
    operation that would otherwise have worked.
    """
    def guard(ssh_client, args, kwargs):
        if ssh_client.capabilities.get(capability_key) is False:
            return message
        return None
    return guard


def require_capability_when_sudo(capability_key, message, sudo_arg_index):
    """Guard factory for kill-process-style methods where a capability only
    matters when sudo=True is actually requested for this specific call
    (process-group killing only applies to sudo'd commands - see
    ops/task.py's _cmd_kill_process)."""
    def guard(ssh_client, args, kwargs):
        if not _extract_sudo_flag(args, kwargs, sudo_arg_index):
            return None
        if ssh_client.capabilities.get(capability_key) is False:
            return message
        return None
    return guard


def require_archive_extract_capabilities(strip_components_key, strip_components_msg,
                                          keep_old_files_key, keep_old_files_msg,
                                          overwrite_arg_index):
    """Guard for extract_archive_to_directory: --strip-components is always
    used; --keep-old-files is only added when overwrite is NOT explicitly
    True (its default is False). Checks both independently so either gap
    produces its own clear message - live-verified against Alpine: BusyBox
    tar accepts --strip-components but rejects --keep-old-files outright,
    two genuinely separate capabilities."""
    def guard(ssh_client, args, kwargs):
        if ssh_client.capabilities.get(strip_components_key) is False:
            return strip_components_msg
        overwrite = _extract_arg_flag(args, kwargs, 'overwrite', overwrite_arg_index)
        if not overwrite and ssh_client.capabilities.get(keep_old_files_key) is False:
            return keep_old_files_msg
        return None
    return guard


class CapabilityGate:
    """Wraps an existing ops instance, gating only the methods named in
    `guards`; every other attribute/method passes straight through to the
    wrapped instance untouched.

    guards: dict of method_name -> callable(ssh_client, args, kwargs) ->
        Optional[str]. Returns an error message (blocks the call, raised as
        SshError) or None (allows the call through to the real method).
    """

    def __init__(self, wrapped, ssh_client, guards):
        self._wrapped = wrapped
        self._ssh_client = ssh_client
        self._guards = guards

    def __getattr__(self, name):
        guard = self._guards.get(name)
        real_attr = getattr(self._wrapped, name)
        if guard is None or not callable(real_attr):
            return real_attr

        def _gated(*args, **kwargs):
            error_message = guard(self._ssh_client, args, kwargs)
            if error_message:
                raise SshError(error_message)
            return real_attr(*args, **kwargs)

        return _gated


# ==========================================================================
# Guard dicts per ops module / underlying concrete class
# ==========================================================================
# Split by which concrete class is actually being wrapped: gating a method
# with a capability its own implementation never depends on would risk a
# false-positive block (e.g. Mac's directory ops never use GNU find -printf,
# so gating them on find_printf could wrongly block an operation Mac's own
# code would have handled fine).

_FIND_PRINTF_MSG = (
    "This operation needs GNU find's -printf, which this host's find doesn't "
    "support (looks like a BusyBox-style find). Fallback: ssh_dir_list_files_basic "
    "(non-recursive filenames) plus ssh_file_stat per entry for metadata - both are "
    "SFTP-based and unaffected by this, at the cost of one call per directory level "
    "instead of one call for the whole tree."
)
_DU_SB_MSG = (
    "This operation needs du's combined -s and -b flags, which this host's "
    "du doesn't support (looks like a BusyBox-style du). Fallback: "
    "ssh_cmd_run(\"du -sk <path>\") - dropping -b for -k (kilobytes instead of "
    "exact bytes) works on most BusyBox du builds even when -sb doesn't."
)
_TAR_STRIP_MSG = (
    "This operation needs tar's --strip-components, which this host's tar "
    "doesn't support (looks like a BusyBox-style tar). No clean single-tool "
    "fallback - extract without stripping via ssh_cmd_run (the archive's own "
    "top-level directory will remain), then relocate its contents up one level "
    "yourself (ssh_cmd_run/ssh_dir_copy)."
)
_TAR_KEEP_OLD_FILES_MSG = (
    "This operation needs tar's --keep-old-files (used whenever overwrite=False, "
    "the default), which this host's tar doesn't support (looks like a "
    "BusyBox-style tar). Pass overwrite=True to avoid needing this flag."
)
_XARGS_0_MSG = (
    "This operation needs xargs's -0 (null-delimited input), which this "
    "host's xargs doesn't support (looks like a BusyBox-style xargs). Fallback: "
    "ssh_cmd_run with 'find ... -exec <command> +' instead of "
    "'find ... -print0 | xargs -0 <command>' - portable, and still safe with "
    "spaces in filenames."
)
_PS_PGID_MSG = (
    "Killing a sudo'd command by process group needs ps's -o pgid= support, "
    "which this host's ps doesn't have (looks like a BusyBox-style ps). No "
    "clean fallback - killing just the captured PID (not the whole process "
    "group) can leave the sudo'd command's real child process(es) running as "
    "orphans, which is the exact failure mode this check exists to prevent."
)

# Methods only present in SshDirectoryOperations_Linux's own overrides
# (_cmd_find_with_type/_cmd_dir_size/_cmd_list_with_metadata), which use
# GNU-only find/du syntax - not relevant when the Mac-flavored overrides are
# in use (they never generate these forms in the first place).
LINUX_DIRECTORY_GUARDS = {
    'search_files_recursive': require_capability('find_printf', _FIND_PRINTF_MSG),
    'list_directory_recursive': require_capability('find_printf', _FIND_PRINTF_MSG),
    'calculate_directory_size': require_capability('du_sb', _DU_SB_MSG),
    # extract_archive_to_directory(archive_path, destination_path, overwrite, sudo)
    # - client.py's wrapper passes overwrite positionally as the 3rd arg
    # (index 2 after archive_path/destination_path).
    'extract_archive_to_directory': require_archive_extract_capabilities(
        'tar_strip_components', _TAR_STRIP_MSG,
        'tar_keep_old_files', _TAR_KEEP_OLD_FILES_MSG,
        overwrite_arg_index=2
    ),
    'batch_delete_by_pattern': require_capability('xargs_0', _XARGS_0_MSG),
    'search_file_contents': require_capability('xargs_0', _XARGS_0_MSG),
}

# Methods shared via the base SshDirectoryOperations class (not overridden
# per-platform), so these apply identically whether the underlying instance
# is Linux- or Mac-flavored (used by 'flex', which reuses Mac's dir_ops).
FLEX_DIRECTORY_GUARDS = {
    'extract_archive_to_directory': require_archive_extract_capabilities(
        'tar_strip_components', _TAR_STRIP_MSG,
        'tar_keep_old_files', _TAR_KEEP_OLD_FILES_MSG,
        overwrite_arg_index=2
    ),
    'batch_delete_by_pattern': require_capability('xargs_0', _XARGS_0_MSG),
    'search_file_contents': require_capability('xargs_0', _XARGS_0_MSG),
}

# ops/task.py has no Mac-flavored variant at all (macOS and 'flex' both reuse
# SshTaskOperations_Linux wholesale, same as run_ops) - one guard dict covers
# both dispatch cases.
TASK_GUARDS = {
    # kill_task(pid, signal=15, sudo=False, ...) - client.py's wrapper passes
    # sudo positionally as the 3rd arg (index 2 after `self`/pid/signal).
    'kill_task': require_capability_when_sudo('ps_pgid', _PS_PGID_MSG, sudo_arg_index=2),
    # _kill_remote_process(pid, sudo=False) - called by keyword from
    # ops/run.py's _kill_on_runtime_timeout, but handle positional too.
    '_kill_remote_process': require_capability_when_sudo('ps_pgid', _PS_PGID_MSG, sudo_arg_index=1),
}


# ==========================================================================
# Human-readable reporting (for ssh_conn_connect/ssh_conn_status, so gaps are
# visible upfront instead of discovered by trial and error)
# ==========================================================================

CAPABILITY_DESCRIPTIONS = {
    'bash': "bash shell",
    'find_printf': "GNU find's -printf (used for fast directory listings/metadata)",
    'find_depth': "GNU find's -depth",
    'stat_c': "GNU stat's -c format (falls back gracefully today; permissions may not be restored after a sudo'd file edit)",
    'du_sb': "du's combined -s/-b flags (used for directory size calculation)",
    'tar_strip_components': "tar's --strip-components (used for archive extraction)",
    'tar_keep_old_files': "tar's --keep-old-files (used for archive extraction when overwrite=False, the default)",
    'ps_pgid': "ps's -o pgid= (used to kill a sudo'd command's whole process group)",
    'xargs_0': "xargs's -0 null-delimited input (used for batch file operations)",
    'sudo': "sudo",
    'tmp_writable': "a writable /tmp",
}


def describe_capabilities(capabilities: dict) -> list:
    """Turn a probed capabilities dict into plain-English warnings for any
    *confirmed* missing capability, so ssh_conn_connect/ssh_conn_status can
    surface gaps proactively. Only reports on confirmed False - an absent key
    means the probe never confirmed either way, not a known gap.
    """
    warnings = []
    for key, supported in capabilities.items():
        if supported is False:
            description = CAPABILITY_DESCRIPTIONS.get(key, key)
            warnings.append(f"Not available on this host: {description}.")
    return warnings
