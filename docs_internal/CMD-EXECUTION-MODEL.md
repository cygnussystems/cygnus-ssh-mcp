# How `ssh_cmd_*` Actually Works

Internal reference for the `ssh_cmd_run` / `ssh_cmd_check_status` / `ssh_cmd_output` /
`ssh_cmd_kill` / `ssh_cmd_history` tool family (referred to here as "cmd tools", as
opposed to the separate `ssh_task_*` "task tools" family — see the comparison at the
bottom).

Written after fixing a cluster of timeout/status bugs on 2026-07-03 (see
`planning/2026-07-03-timeout-recovery-bugfix.md`, gitignored/local-only) where several
wrong assumptions about this model — including ones this assistant stated out loud
before checking — caused real confusion. This doc exists so that doesn't happen again,
and so `docs/50-command-execution.md` can eventually be corrected to match reality.

Updated 2026-07-04 after a black-box LLM usability test found that `io_timeout` was
likely violating its own documented promise (silently killing the remote command via
`chan.close()` instead of leaving it running) - see the "Timeout semantics" section
below for the fix, and the new `wait_timeout` parameter added alongside it. The public
docs (`docs/50-command-execution.md`, `docs/40-tools-reference.md`) have been updated
to match and shouldn't have further drift as of this date.

## The mental model: one SSH connection, many separate remote processes

It's tempting to think of `ssh_cmd_run` like typing into one continuous terminal
session, the way a human SSHing in would experience it. **That's not what happens.**

What *is* continuous: the underlying `paramiko.SSHClient` and its `Transport` — one TCP
connection, one authenticated SSH session, held open across every `ssh_cmd_run` call
until you disconnect. That's real, persistent state.

What is *not* continuous: each `ssh_cmd_run` call opens a **brand-new SSH channel** and
calls `channel.exec_command(cmd)` on it (`ops/run.py:_execute_command`). Per the SSH
protocol, `exec_command` asks the server to run one command in a fresh process and tear
it down when it's done — this is a different SSH channel type than `invoke_shell`
(interactive PTY-backed shell), which is what a human's terminal session actually uses
and which this codebase does **not** use for `ssh_cmd_run`. Every command call is its
own process on the remote end, with no memory of anything a previous call did.

Concretely, this means:

- **`cd` does not persist between calls, and this is intentional** — see "Working
  directory: explicit, not remembered" below for why an earlier prototype that
  *simulated* persistence (remembering the last directory and silently re-`cd`-ing
  into it) was built, tested, and then deliberately reverted the same day.
- **Environment variables/exports do not persist between calls**, on any platform.
- **The `cwd` field you see in `ssh_conn_status`/`ssh_conn_host_info` is a separate,
  live one-off query** (`server.py` runs `cd`/`pwd` fresh, right when you call that
  tool) — unrelated to `ssh_cmd_run`'s own `cwd` parameter/response field below.

What *does* survive across calls, on the same connection:
- The sudo password (cached in-memory on the `SshClient` instance, used automatically
  when `use_sudo=True`).
- Command history (`ssh_cmd_history`) — but only for this connection; it resets on
  reconnect.
- Nothing else. No shell variables, no aliases, no working directory.

## Working directory: explicit, not remembered (decided 2026-07-03)

A same-day prototype simulated `cd` persistence across calls (remember the last
directory, silently re-`cd` into it before every subsequent command, self-correcting
fallback to the login directory if that path had since been deleted/renamed). It
worked and was tested live, but was deliberately reverted before shipping, because:

1. **It cannot be made fully foolproof.** The remembered directory can vanish between
   call N and call N+1 (a TOCTOU gap); any recovery that still runs the command is a
   silent behavior change the caller has to notice by diffing a response field, not an
   explicit failure. "Remember + silently inject" can only ever fail *gracefully*, not
   foolproof.
2. **No real usage evidence asked for it.** Three independent real-world sessions used
   this MCP for genuine remote maintenance (including one on GPT-5.5) and never once
   flagged missing cwd persistence — they flagged timeout/status confusion instead
   (which is what got fixed first, same day).
3. **It fights the model callers already have.** Every mainstream stateless-step
   automation system (GitHub Actions `working-directory:`, Ansible `chdir:`, GitLab CI,
   Terraform provisioners) already uses "no implicit persistence, be explicit per
   step" — an agent that has internalized that idiom would find silent cwd carryover
   surprising, not helpful.
4. **It would have been Linux/macOS-only anyway**, since Windows has no single
   reliable shell to wrap against (cmd.exe vs PowerShell `DefaultShell` ambiguity —
   the same problem `ps_encode.py` had to solve for other Windows commands). A
   platform asymmetry in *default* behavior was explicitly ruled out as a requirement.

**What shipped instead: an optional, per-call `cwd` parameter on `ssh_cmd_run` that
fails closed.** No state is remembered anywhere. Implementation
(`ops/run.py:SshRunOperations_Linux._wrap_for_explicit_cwd`, applied in
`execute_command` after sudo handling, Linux/macOS only):

```bash
cd -- '<cwd>' 2>/dev/null || { echo ___SSH_MCP_CWD_INVALID___ 1>&2; exit 77; }
<the actual command>
```

If the directory doesn't exist, the command **never runs at all** — verified live by
checking the target host directly (not just catching the exception) that a command
passed a bad `cwd` had zero side effects. `_is_cwd_invalid` checks for exit code 77
*and* the distinct stderr marker together (not exit code alone, since a real command
could legitimately also exit 77) and raises `CwdNotFound`, which `server.py` surfaces
as `status: 'cwd_not_found'`. On success, `handle.cwd`/the response's `cwd` field
simply echo back the directory that was confirmed to run in — ground truth for that
one call, never a promise about the next one. Not implemented for Windows: passing
`cwd` on a Windows connection raises a clear `SshError` rather than silently no-op-ing
or attempting something half-tested.

**Sudo interaction:** the `cd` runs in the *outer*, non-privileged shell, before
`sudo -n bash -c '...'` (or the password-piped equivalent) — the sudo'd child inherits
the cwd via normal fork semantics, so `cwd` applies transparently to `use_sudo=True`
calls too. Verified live.

**Concurrency:** only one `ssh_cmd_run` can be in flight at a time per connection — a
`_busy_lock` is held for the duration of `execute_command`. A second call while one is
running returns a `busy`-type error rather than queuing or running in parallel. Use
separate connections (or `ssh_task_launch`) if you need concurrency.

## Timeout semantics (updated 2026-07-04 — background-monitoring handoff)

Three independent timeout knobs, checked every poll iteration in `_monitor_command`:

- **`io_timeout`** (default 60s): max seconds of *silence* — no stdout/stderr activity.
- **`wait_timeout`** (default: none): max seconds of *total elapsed wait*, regardless
  of output activity — unlike `io_timeout`, this fires even if the command is
  actively producing output. Added 2026-07-04 so a caller can check in periodically
  on a long-running-but-chatty command (a `docker pull` with a constant progress bar)
  instead of being blocked until it finishes or genuinely goes quiet.
- **`runtime_timeout`** (default: none): a hard wall-clock cap regardless of output
  activity — the *only* one of the three that ever kills the remote process.

**When `io_timeout` or `wait_timeout` fires** (`ops/run.py:_handoff_to_background`),
the channel is **not** closed. Monitoring is handed off to a daemon thread
(`_continue_monitoring_in_background`) that keeps draining stdout/stderr into the
handle and watching for real completion — it still enforces `runtime_timeout` itself
(with an internal `MAX_BACKGROUND_RUNTIME_SECONDS` ceiling, currently 24h, applied if
the caller never set one, so a background thread can never run unbounded), and
reuses the same platform-specific `_handle_command_completion` the foreground path
uses (including Windows's exit-code-marker recovery) once the command actually
finishes. `CommandTimeout` now also carries a `.reason` (`'io_timeout'` or
`'wait_timeout'`) alongside the `CommandHandle` (id + pid).

**Before 2026-07-04, this closed the channel instead** (`chan.close()` right after
`io_timeout` fired), which — since the command was never launched detached/`nohup`'d
— likely sent the remote process `SIGHUP`, silently killing exactly the commands the
tool's own docstring promised would keep running. This was found via a black-box
LLM usability test (see `planning/2026-07-04-llm-test-session1-linux-bugs.md`,
finding #1, gitignored/local-only) and fixed the same day. `runtime_timeout`'s kill
path is unaffected by this change — still the only thing in this whole flow allowed
to close the channel early (`_kill_on_runtime_timeout`, shared by both the
foreground loop and the background thread).

### Recovering after a timeout

`ssh_cmd_check_status(handle_id, wait_seconds)` polls the handle's recorded state
after waiting. Its `status` field means:

| status | meaning |
|---|---|
| `completed` | Confirmed — a real exit code was captured. This now includes commands that survived `io_timeout`/`wait_timeout`, since the background thread keeps watching for the real exit code, not just for `runtime_timeout`-killed or same-call completions. |
| `running` | Still actively being monitored — including a command that's past `io_timeout`/`wait_timeout` but hasn't finished yet, since the background thread has definite live knowledge of it, not a guess. |
| `killed` | Confirmed terminated — `runtime_timeout` killed it, or a prior `ssh_cmd_kill` call found it already gone. `exit_code` is not known but this is terminal. |
| `completed_exit_code_unknown` | Rare fallback now (previously the common `io_timeout` outcome) — monitoring stopped without a confirmed exit code (e.g. an unexpected error/lost connection during background monitoring) and a live `task_status(pid)` check confirms the process is gone. |
| `unknown_still_running` | Rare fallback, same caveat as above, but the live check confirms it's still alive. **Not** an error state — call `check_status` again. |
| `not_found` | The handle doesn't exist — often because the connection was reconnected (handles/history are connection-scoped and do not survive reconnect; `ssh_task_launch` PIDs do). |

The old bug (2026-07-03): completion was inferred from `end_ts is not None`, but
`end_ts` gets set on *every* exit path (real completion, `io_timeout`,
`runtime_timeout`, errors) — so a command that merely went quiet was reported as
`completed` with a stale/`None` exit code. The fix checks `exit_code is not None`
instead, since `exit_code` is set *exclusively* by real command completion
(`ops/run.py:_handle_command_completion`) — this remains true after the 2026-07-04
change too, since `_handle_execution_error`/`_cleanup_command` now both skip setting
`end_ts` at all for a handle that's been handed off to background monitoring
(`handle._background_monitored`), so it stays `None` until the background thread
itself confirms real completion.

## The PID problem (fixed for Linux/macOS, non-sudo, 2026-07-03)

`CommandHandle.pid` for a command launched via `ssh_cmd_run` **used to not be a real
remote OS process ID at all** - it was `chan.get_id()`, paramiko's local channel number
(0, 1, 2, ... on this side of the `Transport`), unrelated to anything on the remote
host. Consequence: `runtime_timeout`'s kill attempt
(`task_ops._kill_remote_process(handle.pid)`) sent `kill -15/-9 <channel_number>` to
the remote host, which essentially never matched a real process and silently failed -
`runtime_timeout` never actually killed anything despite being designed as the hard cap.

**Fixed by reusing the exact pattern `ssh_task_launch` already used for background
tasks** (`echo "PID:$!"`) - no new mechanism invented. `SshRunOperations_Linux`:

- `_wrap_for_pid_capture` prepends `printf '___SSH_MCP_PID___%s\n' "$$" 1>&2` as the
  very first thing the remote shell does, before any cwd-guard or the user's command
  (`$$` is the wrapper shell's own PID and doesn't change based on what runs after it).
- `_capture_pid` (overridden, replacing the base channel-id fallback) reads stderr in a
  bounded loop (`PID_CAPTURE_TIMEOUT = 3s`, though the marker arrives near-instantly
  since it's printed before the command even starts), parses the real PID, and strips
  the marker line before any of it reaches the caller's stderr output. Falls back to
  the old channel-id behavior (with a warning) if the marker doesn't arrive in time -
  degraded, not broken.

Verified live: `runtime_timeout` on `sleep 30` now genuinely kills the remote process
(confirmed via an independent `kill -0 <pid>` check against the actual host, not just
by trusting our own code's success claim) - previously this silently failed 100% of
the time. Regression-checked against `cwd`, sudo, and `io_timeout` together; all still
work, and `io_timeout`'s returned `pid` is now also a real, independently-usable PID
as a side benefit.

**Fixed for Windows too (2026-07-03, later the same day):** `SshRunOperations_Win`
now overrides `_wrap_for_pid_capture`/`_capture_pid` as well - it spawns the command
via `System.Diagnostics.Process` inside a `powershell -EncodedCommand` wrapper
(base64-encoding the raw command rather than escaping it, since nested-quote commands
silently mis-parse under naive escaping), captures the real PID from a stderr marker,
and streams output live via `Register-ObjectEvent` + a poll loop (a blocking
`WaitForExit()` would prevent PS from ever running the event handlers). `runtime_timeout`
kill now uses `taskkill /F /T` (not `Stop-Process`, which only killed the wrapper and
orphaned the real workload - every PID handed out is a `cmd.exe`/`powershell.exe`
wrapper, not the actual process, since there's no `exec()` on Windows). Windows exit
codes are *also* now reliable - `chan.recv_exit_status()` can't be trusted there (a
Win32-OpenSSH + `cmd.exe` bug flattens any nested child's real exit code to `1`), so
`_handle_command_completion` recovers the real value from a second stderr marker
printed right before the wrapper exits.

**Not fixed (deliberately out of scope):**
- **Sudo'd commands.** The PID marker is printed by the *outer*, non-privileged wrapper
  shell - for `use_sudo=True`, the actual command runs as a *child* of that shell (via
  `sudo -n bash -c '...'`), so killing the captured PID kills the wrapper, not
  necessarily the privileged child doing the real work (killing a parent doesn't
  cascade to children in Unix). This is the same class of issue as the already-flagged,
  not-yet-scoped `ssh_cmd_kill`-after-`io_timeout`/orphaned-children problem - process-
  group killing (`kill -{signal} -{pid}` instead of `kill -{signal} {pid}`) would likely
  fix both at once, but changes the shared kill path also used by the already-tested
  `ssh_task_kill`, so it was deliberately not bundled into this fix.

## Streaming stdout/stderr without corrupting partial lines (fixed 2026-07-06)

Every `chan.recv(4096)`/`chan.recv_stderr(4096)` call site in `ops/run.py` used to
treat each raw chunk as if it only ever contained complete lines:
`line if line.endswith('\n') else line + '\n'`. If a chunk arrived with no `\n` in it
at all - plausible whenever the remote flushes output byte-by-byte or in small
fragments (verified live with `curl`'s unbuffered stderr, e.g. its progress-meter
header) - this synthesized a **fake** newline onto what was actually just an
in-progress fragment, not a completed line. Over many tiny fragments this produced
one synthetic newline per fragment (`"curl: (7) Failed..."` arriving as
`"c\nu\nr\nl\n:\n..."`).

Fixed by adding two shared helpers to the base `SshRunOperations` class:
- `_feed_output_chunk(handle, chunk, is_stderr)` - accumulates a per-handle pending
  partial-line buffer (`CommandHandle._pending_stdout`/`_pending_stderr`) and only
  emits genuinely newline-terminated lines; a trailing fragment with no `\n` yet
  stays buffered instead of getting a synthetic newline.
- `_flush_pending_output(handle)` - once a command is confirmed fully done, emits
  whatever's left in either buffer exactly as-is (no fake newline - correctly
  handles output that just doesn't end in a newline, e.g. a shell prompt).

All 4 base-class chunk-processing sites now route through `_feed_output_chunk`;
`_drain_remaining_output` calls the flush at the end (covering both completion
paths that call it - `_monitor_command`'s sync loop and
`_continue_monitoring_in_background`'s daemon-thread loop); `_kill_on_runtime_timeout`
got its own explicit flush too, since it never called `_drain_remaining_output` and
would otherwise silently drop whatever was pending at kill time. Both platform-
specific `_capture_pid` overrides (Linux, Windows) were reworked to feed
incrementally rather than accumulating raw bytes and splitting once at the end -
Windows's marker-detection loop in particular now runs against the pending-buffer-
aware accumulated text, since it must still find a marker that could itself be split
across small reads while forwarding non-marker lines only once genuinely complete.

## `cmd` vs `task` tools — why `task` doesn't have these bugs

| | `ssh_cmd_run` family | `ssh_task_launch` family |
|---|---|---|
| Execution model | Synchronous, blocks until done/timeout | Fire-and-forget, returns immediately |
| Output | Captured in-memory (circular buffer, tail-preserving) | Redirected to log files on the remote host |
| PID captured | Yes, on all platforms non-sudo (Linux/macOS fixed 2026-07-03 via `$$`, Windows fixed later the same day via a stderr marker - see "The PID problem" above and the Windows note below it) — real remote OS PID. Still `chan.get_id()` (a local channel number) for sudo'd commands | Yes — real remote OS PID, captured explicitly at launch, all platforms |
| Status check | `ssh_cmd_check_status` reads handle state that a background thread keeps updating in real time after `io_timeout`/`wait_timeout` (2026-07-04) - the live `task_status(pid)` fallback to `'completed_exit_code_unknown'`/`'unknown_still_running'` is now a rare path (unexpected errors / lost connections during background monitoring), not the common outcome it used to be | `ssh_task_status` does a *live* liveness check every call (`kill -0 <pid>` / `Get-Process`) — no caching, so no staleness bug |
| Survives reconnect | No — handles/history are connection-scoped (the background monitoring thread dies with the connection too) | Yes — PID is an OS-level identifier |
| Known gap | `runtime_timeout` kill still doesn't reliably work for sudo'd commands (kills the wrapper, not the privileged child) - this is now the only remaining gap, Windows is fixed | No exit-code or log-path recall after the task exits — `ssh_task_status` only ever reports liveness, not a documented bug, more of a missing feature (tracked separately in the consolidated feature plan, Theme D) |

Because `ssh_task_status` re-queries the remote host live on every call instead of
trusting cached local state, it was never exposed to the `end_ts`-vs-`exit_code`
staleness bug that `ssh_cmd_check_status` had. Task launch always captures a real PID
on all platforms; the `cmd` path now does too for the common (non-sudo, Linux/macOS)
case, closing most of the gap between the two tool families.

## Open items for `docs/50-command-execution.md`

Most of the drift originally noted here has since been fixed (the "Strategy 3" example
now correctly uses `result['id']`/`status['status']`, and `busy` is confirmed a real
`ssh_cmd_run` status from `BusyError` - `killed` was never actually listed under
`ssh_cmd_run`'s own status table, only `ssh_cmd_check_status`'s, so there was nothing
to fix there). The public docs were also updated 2026-07-03 with the new
`ssh_cmd_check_status` terminal statuses and Windows `runtime_timeout` coverage
described above, and again 2026-07-04 with the `wait_timeout` parameter and the
background-monitoring fix (both `docs/50-command-execution.md` and
`docs/40-tools-reference.md` now reflect the current behavior described in this file).

Still open:
- No mention anywhere that working directory and environment do not persist between
  calls, or of the `cwd` parameter's fail-closed behavior. Suggested doc line: "Each
  ssh_cmd_run is an independent process with no shell state carried between calls
  (same model as GitHub Actions steps or Ansible tasks). Use absolute paths, chain
  with &&, or pass cwd for a single call (Linux/macOS only)."
