# OpenCode retest round 2: long operations, truncation, Windows task logs, search

| | |
|---|---|
| **Found** | 2026-09-26, OpenCode (`openai/gpt-6-sol`, default request timeout) against Linux, macOS and Windows |
| **Source** | `PR_MCP_SSH__LLM_TEST/findings/2026-09-26-opencode-developer-handoff.md` (+ per-issue files there) |
| **Plan** | `planning/2026-09-26-retest-fix-plan.md` (local) |
| **Status** | In progress on `fix/opencode-retest-issues`: F2 + small items, W1 and F1 fixed (plus a Windows output-loss bug found on the way); W2/W3 pending |

## Summary

| ID | Finding | Status |
|---|---|---|
| W2/W3 (P1) | `ssh_archive_create` / `ssh_archive_extract` on 18,000 files: client timeout, result lost; during extract **every** tool timed out until OpenCode was restarted | Pending (WS4) |
| W1 (P1) | Windows `ssh_task_launch("echo a & echo b 1>&2", stdout_log=…)`: stdout lost, stderr in the stdout log | **Fixed** |
| F1 (P1) | `ssh_cmd_run("seq 1 105")` returns lines 6–105, no truncation indicator, first lines unrecoverable | **Fixed** |
| F1b (P1, found while fixing F1) | **Windows `ssh_cmd_run` lost the end of fast output**: a 1000-line command returned `success` with only lines 1–505 | **Fixed** |
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

## F1: output silently cut to the last 100 lines

- **Root cause:** `CommandHandle` kept output in `deque(maxlen=tail_keep)` with `tail_keep=100`
  (the history manager meant to keep full output for recent commands, but `run_ops` forced every
  handle back to 100). Earlier lines were dropped with no indication, and `ssh_cmd_output` couldn't
  recover them. The same buffer limited every tool that parses command output (content search,
  listings, glob search) to 100 lines.
- **Fix:**
  - Size-based retention: up to **2 MB per stream per command** is kept (`--max-output` /
    `MCP_SSH_MAX_OUTPUT`); beyond it the earliest lines are dropped **and counted**
    (`dropped_lines`). A single line bigger than the budget keeps its end with a marker.
  - **Inline limit:** `ssh_cmd_run` returns at most the last **32 KB** of each stream
    (`--inline-output` / `MCP_SSH_INLINE_OUTPUT`), in all four response paths (success,
    io/wait handoff, runtime timeout, command_failed).
  - Every response has `output_truncated` / `stderr_truncated`; when true it adds
    `*_lines_total`, `*_lines_returned`, `*_lines_dropped` and an `output_note` saying which
    lines can be paged and which are gone. `command_failed` now also includes the handle `id`.
  - `ssh_cmd_output(start_line=N, lines=M)` pages over everything retained (stdout or stderr);
    asking for a dropped line raises an error naming the first available line.
  - **Total memory ceiling** of 50 MB across history (`--output-memory` /
    `MCP_SSH_OUTPUT_MEMORY`): when a new command starts over it, the oldest *finished*
    commands' output is released (reads then say it was dropped).
- **Tests:** `testing_mcp/test_tool__output_limits.py` (105 lines complete; truncation flags,
  paging and dropped-line error with patched small limits; stderr separately; memory ceiling).
  Pass on Linux, macOS and Windows.

## F1b: Windows `ssh_cmd_run` lost the end of fast output

- **Found** while testing F1 on Windows: a 1000-line PowerShell loop returned `success` with
  `total_lines` 505 (447 on another run) and the last line `Line 505`. The old 100-line buffer
  hid it: you'd have seen lines 406–505 and not known 506–1000 were missing.
- **Root cause:** the Windows wrapper (`ops/run.py` `_PID_CAPTURE_SCRIPT_TEMPLATE`) relayed the
  child's output through `Register-ObjectEvent -Action` handlers, which PowerShell only runs
  when the engine is idle. A command that writes a lot and exits quickly left events queued;
  the wrapper then printed its exit-code marker and exited, and the queued output was lost.
- **Fix:** the wrapper reads both pipes directly with `ReadLineAsync`, polled until EOF, and
  relays each line immediately (still streaming live). If the command has exited but a process
  it started still holds the pipes open (e.g. `start /b …`), it stops after ~1s of silence
  instead of waiting for that process.
- **Also fixed:** removing the exit-code marker line from stderr didn't update the line/size
  counts, so every Windows command showed one phantom stderr line (and would have been flagged
  `stderr_truncated`). New `CommandHandle.remove_stderr_line()` keeps them consistent.
- **Tests (Windows):** `test_windows_fast_bulk_output_is_not_lost` (3,000 lines, all present),
  `test_windows_child_holding_pipes_does_not_hang` (`start /b ping …` returns promptly).
