# Failed sudo task launch leaves private launcher script in `/tmp` on Alpine

**Date/harness:** 2026-09-28, OpenCode `openai/gpt-6-sol`, target `alpine-test` (Alpine Linux 3.24.1 / BusyBox, no sudo, `ps_pgid:false`). **Priority:** P2 cleanup correctness. Black-box consumer finding; no server source or launcher contents read.

## Exact repro

`ssh_conn_connect({"host_name":"alpine-test"})` → `status:"success"`, `capabilities.sudo:false`, warning “Not available on this host: sudo.” `ssh_conn_verify_sudo({})` → `{"available":false,"passwordless":false,"requires_password":false}`.

With session-owned `/tmp/llm_test` created:

```json
ssh_task_launch({"command":"echo should-not-run > /tmp/llm_test/sudo-marker","use_sudo":true,"stdout_log":"/tmp/llm_test/sudo-fail.log"})
```

MCP error, verbatim: `Error calling tool 'ssh_task_launch': Failed to launch task: Task NOT launched: sudo failed: /tmp/launch_script_1790586608.sh: line 7: sudo: not found. Check with ssh_conn_verify_sudo.` No PID returned. `ssh_file_stat({"path":"/tmp/llm_test/sudo-marker"})` → `exists:false` (good: no phantom command). `ssh_dir_list_files_basic({"path":"/tmp/llm_test"})` showed no `sudo-fail.log`.

**But** `ssh_file_stat({"path":"/tmp/launch_script_1790586608.sh"})` → `{"exists":true,"path":"/tmp/launch_script_1790586608.sh","type":"file","mode":"0o100700","uid":1000,"gid":1000,"size":1091,...}`. We did **not** read it; the 0700 permission is appropriate but a failed launcher should not leave a private script behind. Cleanup by the tester: `ssh_cmd_run({"command":"rm -f -- /tmp/launch_script_1790586608.sh; test ! -e /tmp/launch_script_1790586608.sh && echo REMOVED","wait_timeout":5})` → success/output `REMOVED\n`; subsequent `ssh_file_stat` → `exists:false`. The session scratch was also previewed/deleted and verified absent.

## Expected/fix direction

Fail before staging a launcher when `ssh_conn_verify_sudo` already knows sudo is unavailable, or ensure all failure paths remove the staged launcher in a `finally`/equivalent cleanup. Preserve the current truthful `Task NOT launched`/no-PID behavior. Add a regression assertion for no `/tmp/launch_script_*` artifact after failed pre-check without reading or exposing any password/passphrase content.

## Fix (2026-09-28, branch `feature/operation-progress`)

- **Root cause (a regression from the 2026-09-26 sudo-launcher fix, `e55bed6`):** the POSIX
  launcher script deleted itself only on its last line. The new early exits ("sudo failed" = exit
  4, "log not writable" = exit 3) returned before that line, leaving the script (mode 0700, and it
  can contain the sudo password) in `/tmp`.
- **Wider than reported:** the "log not writable" path is exercised by the test suite, so every
  full-suite run since 2026-09-26 left one script behind: 13 found on `linux-test`, 3 on
  `MACBOOK-2015` (plus 1 older one from July on `freebsd-test`). All were removed (metadata checked,
  contents never read).
- **Fix:** the launcher starts with `trap 'rm -f <script>' EXIT`, so it's removed on every exit
  path. Background children don't inherit the trap.
- **Verified live:** failed sudo launches on Alpine and OpenWrt leave 0 scripts; normal tasks still
  run and log.
- **Test:** `test_tool__task_launch_logs.py::test_unwritable_log_fails_launch_clearly` now also
  asserts no launcher script is left in `/tmp`. Removing the trap makes it fail.
