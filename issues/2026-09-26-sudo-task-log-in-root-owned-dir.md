# `ssh_task_launch(use_sudo=true)` returns a PID but silently creates no job/log in root-owned scratch directory

| | |
|---|---|
| **Severity** | 🔴 Blocker for detached job when stdout log parent is not user-writable |
| **Status** | **Fixed, 2026-09-26** (see "Fix"). Reproduced in OpenCode 2026-09-26 |
| **Target** | `TEST_MCP_SSH_LINUX` (Debian 12) |
| **Evidence** | `test_results/2026-09-26-opencode-timeout-fix-retest.md`, section 1 |

Created `/tmp/llm_test` using `ssh_cmd_run(command="rm -rf /tmp/llm_test && mkdir -p /tmp/llm_test && ls -ld /tmp/llm_test", use_sudo=true, wait_timeout=10, runtime_timeout=30)` → `status: success`, `id: 8`, `pid: 667`, output `drwxr-xr-x 2 root root … /tmp/llm_test`.

Then called:

```text
ssh_task_launch(command="curl --fail --location --limit-rate 3M --output /tmp/llm_test/debian-12.11.0-amd64-netinst.iso https://cdimage.debian.org/cdimage/archive/12.11.0/amd64/iso-cd/debian-12.11.0-amd64-netinst.iso && curl --fail --location --output /tmp/llm_test/SHA256SUMS https://cdimage.debian.org/cdimage/archive/12.11.0/amd64/iso-cd/SHA256SUMS && cd /tmp/llm_test && grep ' debian-12.11.0-amd64-netinst.iso$' SHA256SUMS | sha256sum --check -", use_sudo=true, stdout_log="/tmp/llm_test/download.log")
→ {"pid":768,"start_time":"2026-09-25T23:48:31.768489+00:00","stdout_log":"/tmp/llm_test/download.log","stderr_log":"/tmp/llm_test/download.log"} [command field omitted]
ssh_task_status(pid=768) → {"pid":768,"status":"exited","running":false} [timestamp omitted]
ssh_file_read(file_path="/tmp/llm_test/download.log", max_size=3000) → {"success":false,"file_path":"/tmp/llm_test/download.log","error":"File not found: /tmp/llm_test/download.log"}
ssh_file_stat(path="/tmp/llm_test/debian-12.11.0-amd64-netinst.iso") → {"exists":false,"path":"/tmp/llm_test/debian-12.11.0-amd64-netinst.iso","error":"File or directory not found."}
```

The URL was healthy (`302` then `200 OK`); after `ssh_cmd_run(command="chown claude:claude /tmp/llm_test", use_sudo=true)` → `status: success`, `id: 12`, `pid: 865`, retrying the same detached command without sudo returned `pid: 876`, produced its log/ISO, and verified checksum `OK`. A shorter sudo detached task with the now user-writable directory also produced its log. Likely the launching shell opens the log before sudo elevation; at minimum the tool should fail clearly rather than reporting a viable-looking PID and advertised log path when setup cannot work.

## Fix (2026-09-26)

- **Root cause (confirmed in code):** the POSIX launcher ran
  `nohup sudo … <cmd> 1>/tmp/llm_test/download.log 2>&1 &`. The redirect is opened by the
  unprivileged launcher shell *before* sudo runs, so in a root-owned directory it fails, the
  background job never starts, and the launcher still printed `$!`: the PID of a process that
  had already died. The same phantom PID happened without sudo whenever a log path wasn't
  writable (e.g. a missing directory).
- **Fixed** (`ops/task.py`, Linux/macOS/flex launcher):
  - Before launching, each log is created (`: >>`, no truncation) as the user if possible,
    otherwise via sudo when `use_sudo=True`. If neither works, the launcher exits with
    `LOG_NOT_WRITABLE` and `ssh_task_launch` fails with "Task NOT launched: can't create log
    file …"; no PID is returned.
  - When a log could only be created via sudo, the task opens its logs **as root** (the
    redirect happens inside the sudo shell). Otherwise logs are opened by the user's shell
    exactly as before, so default `/tmp/task-<pid>.log` logs stay user-owned and are opened
    immediately (they're renamed right after launch; a slow sudo start would race that).
  - With `use_sudo`, sudo is checked before launching; a failure returns "Task NOT launched:
    sudo failed: …" instead of a PID (previously it only showed up inside the log).
  - Also fixed on the way: the launcher script (which contains the sudo password) was written
    to `/tmp` with the default umask, so it was briefly world-readable. It's now created with
    `umask 077` / `chmod 700`, and the password is passed via an env var + `printf` rather
    than as an `echo` argument visible in `ps`.
- **Regression tests:** `testing_mcp/test_tool__task_launch_logs.py`: root-owned directory with
  sudo (log created, contains stdout+stderr, ran as root; **fails on the old code** with "No
  such file or directory"); unwritable log without sudo fails clearly and the command never
  runs; sudo + default log path ends up at the returned `task-<pid>.log`. The launcher unit
  tests in `test_unit_flex_bash_fallback.py` were updated for the new script shape.
- **Verified:** full Linux suite on `linux-test`: 185 passed, 0 failed (the 5 launcher unit
  tests that failed on the first run were updated for the new script text).
