# OpenCode retest round 2: long operations, truncation, Windows task logs, search

| | |
|---|---|
| **Found** | 2026-09-26, OpenCode (`openai/gpt-6-sol`, default request timeout) against Linux, macOS and Windows |
| **Source** | `PR_MCP_SSH__LLM_TEST/findings/2026-09-26-opencode-developer-handoff.md` (+ per-issue files there) |
| **Plan** | `planning/2026-09-26-retest-fix-plan.md` (local) |
| **Status** | In progress on `fix/opencode-retest-issues`: F2 + small items fixed, W1 fixed; F1 and W2/W3 pending |

## Summary

| ID | Finding | Status |
|---|---|---|
| W2/W3 (P1) | `ssh_archive_create` / `ssh_archive_extract` on 18,000 files: client timeout, result lost; during extract **every** tool timed out until OpenCode was restarted | Pending (WS4) |
| W1 (P1) | Windows `ssh_task_launch("echo a & echo b 1>&2", stdout_log=…)`: stdout lost, stderr in the stdout log | **Fixed** |
| F1 (P1) | `ssh_cmd_run("seq 1 105")` returns lines 6–105, no truncation indicator, first lines unrecoverable | Pending (WS3) |
| F2 (P2) | Linux `ssh_dir_search_files_content` with no match → "Command failed with exit code 1" | **Fixed** |
| S1 | Windows commands run under cmd.exe; PowerShell syntax fails | **Fixed** (documented) |
| S2 | Windows `ssh_task_status` transiently `status: error` | **Fixed** (retry + reason) |
| S3 | Unknown handle after reconnect: bare "No command handle or task found" | **Fixed** (message explains) |

## F2: Linux no-match content search

- **Root cause:** `find … -print0 | xargs -0 grep … || [ $? -eq 1 ]`. GNU `xargs` reports a
  child's exit 1 (grep "no match") as **123**, so the guard never matched and "no match"
  surfaced as `CommandFailed`. BSD `xargs` (macOS) passes 1 through, which is why only Linux broke.
- **Fix:** plain `grep -r -n -H -e <pattern> <path>`, reading grep's own exit code via a marker:
  0 = matches, 1 = no match → `[]`, ≥2 = real error (raised with grep's stderr, unless there are
  matches, which are returned with a logged warning). `-e` stops a pattern starting with `-`
  from being read as an option. The `xargs -0` capability guard on this method was removed
  (plain `grep -r` works on BusyBox).
- **Tests:** `testing_mcp/test_tool__search_and_errors.py` (no-match `[]` on all platforms,
  leading-dash pattern, missing directory error). 3 of 4 fail on the old code.
- **Also noticed:** results were parsed from `handle.tail(total_lines)`, which is limited by the
  same 100-line buffer as F1, so large result sets were silently cut. Fixed by WS3.

## S1–S3: wording and diagnostics

- **S1:** `ssh_cmd_run` / `ssh_task_launch` descriptions say Windows commands run under cmd.exe
  and to use `powershell -NoProfile -Command "…"` for PowerShell.
- **S2:** the liveness check (5s channel timeout) is now retried once with a 10s timeout; on
  `status: error` the response adds `reason` (e.g. "the status check timed out (the host may be
  very busy)") and `next_step` ("does NOT mean the task exited").
- **S3:** `TaskNotFound` explains that command handles only live for the current connection,
  that reconnecting or a server restart clears them, and that `ssh_task_launch` PIDs can still be
  checked with `ssh_task_status`.

## W1: Windows task logs with compound commands

- **Root cause:** the launcher ran `cmd.exe /c <command> > "log" 2> "err"`. cmd.exe binds those
  redirects to **only the last command** of an `&` chain; with `echo a & echo b 1>&2`, `echo a`'s
  stdout went to the (invisible) console and `echo b`'s own `1>&2` was overridden by `> "log"`,
  so stderr ended up in the stdout log. (Also seen on 2026-09-25 and wrongly attributed to the
  test command.)
- **Fix:** WMI now starts a small PowerShell wrapper that runs `cmd.exe` with arguments
  `/c <command>` (identical to `ssh_cmd_run`'s Windows path) via `Start-Process
  -RedirectStandardOutput/-RedirectStandardError`, so OS-level redirection covers the whole
  command and the text is never re-parsed by an outer cmd.exe. The returned PID is the
  wrapper's; `taskkill /T` still kills the whole tree and the default-log rename watcher still
  waits for it. Before launching, both log files are opened for append; if that fails the launch
  returns "Task NOT launched: can't create log file …" (same contract as Linux/macOS since
  `e55bed6`) instead of a PID for a task that never ran.
- **Tests (Windows):** `test_tool__task_launch_logs.py::test_windows_task_compound_command_logs`
  (tester's repro with stdout-only, distinct logs with `&&` + pipe + quoted `&`, the command's own
  `1>nul`, default logs) and `::test_windows_task_unwritable_log_fails_launch`. Both fail on the
  old code. Existing Windows task/kill/status tests pass (11 passed, 6 skipped).
