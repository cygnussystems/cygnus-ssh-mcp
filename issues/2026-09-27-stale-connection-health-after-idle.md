# Connection health reports true after idle, but next Windows operation hits dead SSH session

**Date/harness:** 2026-09-27, OpenCode `openai/gpt-6-sol`, `cygnus_ssh` target `TEST_MCP_SSH_WIN`. **Priority:** P2 connection lifecycle/next-step clarity. Single observed occurrence after a long overnight idle; do not generalize to every connection without retesting. No server source read.

## Exact sequence

1. The Windows connection was active during a 300-file content search that ended `2026-09-26T21:13:51.732038+00:00`, with result returned after an overnight gap.
2. The next search on the same alias (no deliberate disconnect), `ssh_dir_search_files_content({"dir_path":"C:\\Users\\claude\\llm_test\\search-20260926","pattern":"SESSION3-ABSENT-NEEDLE-20260926"})`, returned MCP tool error verbatim: `Error calling tool 'ssh_dir_search_files_content': Unexpected error during command execution: [WinError 10054] An existing connection was forcibly closed by the remote host`.
3. Parallel `ssh_conn_is_connected({})` returned `true`. Immediately afterward `ssh_conn_status({})` raised: `Error calling tool 'ssh_conn_status': Unexpected error during command execution: SSH session not active`.
4. `ssh_conn_connect({"host_name":"TEST_MCP_SSH_WIN"})` → `status:"success"` with the expected Windows host. `ssh_file_stat({"path":"C:\\Users\\claude\\llm_test\\search-20260926\\file-217.txt"})` → `exists:true`; reconnect did not remove user data. Retried the **read-only** no-match search after reconnect: `in_progress, handle_id:1000090`; `ssh_cmd_check_status({"handle_id":1000090,"wait_seconds":40})` → `status:"completed", result:[]`. Scratch was later removed and absence verified.

## Expected and suggested resolution

The description of `ssh_conn_is_connected` promises a check for an active SSH connection; `true` is misleading when the very next command and `ssh_conn_status` see an inactive session. Either perform a live liveness check, invalidate cached connection state upon WinError 10054, or return `unknown/stale` with reconnect guidance. For the command/tool error include an actionable next step (“connection dropped; reconnect by configured alias; determine whether in-flight work actually completed before retry”). If a check has intentionally weaker semantics, describe that distinction clearly.
