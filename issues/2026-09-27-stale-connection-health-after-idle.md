# Connection health reports true after idle, but next Windows operation hits dead SSH session

**Date/harness:** 2026-09-27, OpenCode `openai/gpt-6-sol`, `cygnus_ssh` target `TEST_MCP_SSH_WIN`. **Priority:** P2 connection lifecycle/next-step clarity. Single observed occurrence after a long overnight idle; do not generalize to every connection without retesting. No server source read.

## Exact sequence

1. The Windows connection was active during a 300-file content search that ended `2026-09-26T21:13:51.732038+00:00`, with result returned after an overnight gap.
2. The next search on the same alias (no deliberate disconnect), `ssh_dir_search_files_content({"dir_path":"C:\\Users\\claude\\llm_test\\search-20260926","pattern":"SESSION3-ABSENT-NEEDLE-20260926"})`, returned MCP tool error verbatim: `Error calling tool 'ssh_dir_search_files_content': Unexpected error during command execution: [WinError 10054] An existing connection was forcibly closed by the remote host`.
3. Parallel `ssh_conn_is_connected({})` returned `true`. Immediately afterward `ssh_conn_status({})` raised: `Error calling tool 'ssh_conn_status': Unexpected error during command execution: SSH session not active`.
4. `ssh_conn_connect({"host_name":"TEST_MCP_SSH_WIN"})` → `status:"success"` with the expected Windows host. `ssh_file_stat({"path":"C:\\Users\\claude\\llm_test\\search-20260926\\file-217.txt"})` → `exists:true`; reconnect did not remove user data. Retried the **read-only** no-match search after reconnect: `in_progress, handle_id:1000090`; `ssh_cmd_check_status({"handle_id":1000090,"wait_seconds":40})` → `status:"completed", result:[]`. Scratch was later removed and absence verified.

## Expected and suggested resolution

The description of `ssh_conn_is_connected` promises a check for an active SSH connection; `true` is misleading when the very next command and `ssh_conn_status` see an inactive session. Either perform a live liveness check, invalidate cached connection state upon WinError 10054, or return `unknown/stale` with reconnect guidance. For the command/tool error include an actionable next step (“connection dropped; reconnect by configured alias; determine whether in-flight work actually completed before retry”). If a check has intentionally weaker semantics, describe that distinction clearly.

## Fix (2026-09-27, branch `fix/round4-search-and-connection`)

- **Root cause:** `ssh_conn_is_connected` only read paramiko's local `transport.is_active()`
  flag, which stays `True` for a connection that died without the host's side being able to
  tell us (e.g. dropped by a firewall/NAT after a long idle). No SSH keepalives were sent, so
  an idle connection could be dropped silently in the first place. And a call that then hit
  the dead connection surfaced the raw socket error, with no next step.
- **Fix:**
  - SSH keepalives every 30s on every connection (`transport.set_keepalive`).
  - `ssh_conn_is_connected` does a real round trip (opens and closes a channel, 5s limit, in a
    worker thread). On failure it drops the connection and returns `false`.
  - Any call that fails with a connection-level error (reset, WinError 10054/10053/10060,
    "SSH session not active", EOF, broken pipe, …) triggers the same round-trip check; if the
    link is really dead, the connection is dropped and the call reports
    `CONNECTION_LOST: the SSH connection to <alias> is gone (<reason>). Reconnect with
    ssh_conn_connect(host_name='<alias>'). If the call that failed could have changed
    something on the host, check whether it took effect before running it again ...`.
    This covers raised errors and errors tools return in their response
    (`error_type: 'connection_lost'`; misleading result fields such as `ssh_file_stat`'s
    `exists: False` are dropped). `ssh_cmd_run` returns `status: 'error'`,
    `error_type: 'connection_lost'`.
  - All "No active SSH connection" errors now say how to reconnect, and mention when the
    previous connection was lost and why.
- **Tests:** `testing_mcp/test_unit_connection_loss.py` (error classification, message) and
  `testing_mcp/test_tool__connection_loss.py` (Linux/macOS). The session's server-side `sshd`
  is **frozen with SIGSTOP** from an independent connection, which reproduces the silently dead
  connection: the old code reported it as working; now it's `false` within seconds. It's also
  **killed**: operations and `ssh_cmd_run` return CONNECTION_LOST with the reconnect step, and
  reconnecting works. All three live tests fail on the old code. Windows: keepalive (30s) and the
  liveness round trip (~62 ms) verified live; the freeze/kill tests need POSIX signals and are
  skipped there.
- **Limit:** a connection whose peer is frozen can still make an *in-flight* SFTP/command call
  wait until TCP gives up; that call hands off at the 50s cap as usual, and
  `ssh_conn_is_connected` reports the connection dead meanwhile.
