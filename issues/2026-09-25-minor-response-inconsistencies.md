# Minor response inconsistencies seen during the 2026-09-25 timeout repro

| | |
|---|---|
| **Severity** | 🟡 Data loss (item 1) / 🟢 Minor (items 2-3) |
| **Status** | **Fixed (uncommitted), 2026-09-25.** All items confirmed live on `linux-test`, traced to code, and fixed. Regression tests in `testing_mcp/test_tool__responsiveness.py` |
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
  192.168.1.27/24` and `lo 127.0.0.1/8`. Windows/macOS not yet re-tested.
