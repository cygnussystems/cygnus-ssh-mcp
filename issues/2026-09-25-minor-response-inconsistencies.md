# Minor response inconsistencies seen during the 2026-09-25 timeout repro

| | |
|---|---|
| **Severity** | 🟡 Data loss (item 1) / 🟢 Minor (items 2-7) |
| **Status** | **Fixed, 2026-09-25** (branch `fix/cmd-run-timeout-handoff`). All items confirmed live, traced to code, and fixed. Regression tests in `testing_mcp/test_tool__responsiveness.py` |
| **Found** | 2026-09-25, Claude Code against `TEST_MCP_SSH_LINUX` (Debian 12), side findings from `2026-09-25-cmd-run-client-timeout-loses-handle.md` |

## 1. 🟡 `ssh_task_launch` discards stderr when only `stdout_log` is given, and returns a `stderr_log` path that doesn't exist

- Call: `ssh_task_launch(command="for i in $(seq 1 90); …", stdout_log="/tmp/llm_test/task.log")`
- Schema for `stderr_log`: *"Path to redirect stderr (default: same as stdout)"*
- Response: `"stdout_log":"/tmp/llm_test/task.log","stderr_log":"/tmp/task-967.log"`
- Check: `ls -l /tmp/task-967.log` → `No such file or directory`.
- **Correction (2026-09-25 re-test):** stderr does *not* go to the stdout log. It's thrown
  away. `ssh_task_launch("echo OUT_LINE; echo ERR_LINE >&2", stdout_log="/tmp/llm_stderr_test.log")`
  → the log contains only `OUT_LINE`; the returned `stderr_log` (`/tmp/task-1326.log`) doesn't
  exist. The first repro missed this because its loop never wrote to stderr.
- **Root cause:**
  - `ops/task.py` `launch_task()`: when `stdout_log` is set and `stderr_log` is `None`, it sets
    `effective_stderr_log` to `/dev/null` (or `<log_dir>/null` on Windows), so the launch
    script uses `2>/dev/null` instead of `2>&1`.
  - `server.py` `ssh_task_launch`: the response makes up
    `stderr_log or f"{default_log_dir}/task-{pid}.log"` instead of reporting the path actually used.
- Impact: the error output of a background task is silently lost, and an agent that reads the
  returned `stderr_log` gets a not-found error.
- Fix: when `stderr_log` isn't given, merge stderr into `stdout_log` (as the schema says), and
  return the path that was really used.
- **Fixed:** stderr now defaults to stdout's file; `launch_task` records the real final
  paths on the handle (`None` for a discarded stream; `<name>_err.log` on Windows, where
  cmd.exe can't share one file), and `ssh_task_launch` returns those. Verified live: the
  log contains `OUT_LINE` and `ERR_LINE`, and `stderr_log` equals `stdout_log`.

## 2. 🟢 `ssh_cmd_history` timestamps are malformed (`+00:00Z`)

- `ssh_cmd_history` → `"start_time":"2026-09-25T11:40:58.271001+00:00Z"`: both an offset
  **and** a `Z`, which isn't valid ISO 8601 and will fail strict parsers.
- `ssh_cmd_run` returns the same timestamps correctly: `"start_time":"2026-09-25T11:42:37.365486+00:00"`.
- **Root cause:** `models.py` `CommandHandle.info()` adds `'Z'` to `start_ts.isoformat()` /
  `end_ts.isoformat()`, but those timestamps are already timezone-aware.
- Fix: drop the extra `'Z'`.
- **Fixed.**

## 3. 🟢 `ssh_conn_connect` reports conflicting OS version and no network interfaces

- Call: `ssh_conn_connect(host_name="linux-test")`
- `connection.os_version: "unknown_linux"` but, in the same response,
  `system.os_version: "12 (bookworm)"` and `system.os_name: "Debian GNU/Linux"`.
- `system.interfaces: []` with `raw_output: "lo|IPS:127.0.0.1/8"`, even though the VM has
  `eth0` with `192.168.1.27/24`. (`hostname: localhost` is how the VM is set up, not a bug.)
- **Root cause, OS version:** `client.py` `_detect_linux_distro()` calls `.lower()` on the
  result of `self.run(...)`, but `run()` returns a `CommandHandle`, not a string. The
  `AttributeError` is caught by a bare `except`, so **every** Linux host gets `unknown_linux`.
- **Root cause, interfaces:** `ops/os_ops.py` `_network_key_map` maps the `IFACE` key into
  `raw_output`. The key/value parser keeps only the last interface line (`lo`), and it strips
  the `IFACE:` prefix, so the loop in `network_info()` (which checks
  `line.startswith('IFACE:')`) never matches. Result: `interfaces` is always `[]`. The same
  `IFACE:` format is used for macOS and Windows, so they're probably affected too.
- **Second bug found while fixing:** even with the `.lower()` fixed, `self.run()` can't work
  there, because OS detection runs before `run_ops` exists (`AttributeError: 'NoneType'
  object has no attribute 'execute_command'`, swallowed by the same `except`).
- **Fixed:** distro detection uses a raw `exec_command` (same as `_detect_windows_version`)
  and logs failures instead of swallowing them. `_execute_status_command` can return the
  raw output (`keep_raw_output=True`), and `network_info()` parses every `IFACE:` line,
  merging Windows' one-line-per-address output by interface name. `raw_output` no longer
  leaks into the response. Verified live: `os_version: debian`, interfaces `eth0
  192.168.1.27/24` and `lo 127.0.0.1/8`. macOS verified on `MACBOOK-2015` (`lo0`, `en0
  192.168.1.109`, `utun4`; it returned `[]` before too). Windows not yet re-tested.

## 4. 🟢 `ssh_cmd_check_status` reports at most 50 `output_lines`

- Found 2026-09-25 on `MACBOOK-2015`: a 70-tick loop (71 stdout lines) completed, and
  `ssh_cmd_output` returned `tick 69`, `tick 70`, `FINISHED`, so all output was captured.
  But `ssh_cmd_check_status` said `output_lines: 50`.
- **Root cause:** `server.py` `ssh_cmd_check_status` computed `len(output)`, where `output` is
  `ssh_client.output(handle_id)`, which returns only the last 50 lines by default.
- **Fixed:** uses the handle's `total_lines` instead. Regression test
  `test_ssh_cmd_check_status_output_lines_is_total`.

## 5. 🟢 `ssh_conn_connect` reports `os_version: unknown_windows` on every Windows host

- Found 2026-09-25 on `win-server-2016`: `connection.os_version: "unknown_windows"`, although
  `ssh_cmd_run("ver")` returns `Microsoft Windows [Version 10.0.14393]` (which the code maps
  to `windows_server_2016`).
- **Root cause:** `client.py` `_detect_windows_version()` sends a bare `ver` via raw
  `exec_command`. `ver` is a cmd.exe builtin, and the VM's SSH default shell is PowerShell:
  `ver : The term 'ver' is not recognized …`. Empty output → `unknown_windows`. Same class of
  bug as item 3's Linux distro detection.
- **Fixed:** runs `cmd /c ver`, which works whatever the default shell is. Verified live:
  `os_version: windows_server_2016`. Regression test
  `test_ssh_conn_connect_reports_windows_version` (fails on the old code, passes on the fix).
- **Test note:** the first version of this test, and the Linux distro test, read
  `os_version` from `ssh_conn_status`, which has no such key, so `None != 'unknown_*'` passed
  on the old code. Both now read `ssh_conn_host_info`'s `connection.os_version`.

## 6. 🟢 `busy` wording implied one remote command at a time (from the 2026-09-26 OpenCode retest)

- The tester ran `ssh_cmd_run("echo second")` successfully while an earlier loop, already handed
  off at the 50s cap, was still running, although the `busy` status said "only one command can
  run at a time per connection".
- Not a bug: only one `ssh_cmd_run` can be *waiting in the foreground* at a time; handed-off
  commands keep running in the background and don't block new ones.
- **Fixed (wording):** the `busy` description in `ssh_cmd_run` now says exactly that.

## 7. 🟢 Two `os_version` fields with different meanings (same retest)

- `connection.os_version: "debian"` vs `system.os_version: "12 (bookworm)"`.
- Not a bug: the first is a short platform/distro identifier (`debian`, `windows_server_2016`,
  `unknown_*`; `None` on macOS), the second is the OS's own version string. Renaming would break
  clients.
- **Fixed (documentation):** `ssh_conn_connect`'s description now explains both fields.

## 8. Watch: one-off `Failed during sudo pre-check: SSH session not active` (same retest)

- One setup call failed with this right after a successful connect; reconnect + retry worked.
  Not reproduced. Track if it recurs.
