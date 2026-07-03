# How `ssh_cmd_*` Actually Works

Internal reference for the `ssh_cmd_run` / `ssh_cmd_check_status` / `ssh_cmd_output` /
`ssh_cmd_kill` / `ssh_cmd_history` tool family (referred to here as "cmd tools", as
opposed to the separate `ssh_task_*` "task tools" family — see the comparison at the
bottom).

Written after fixing a cluster of timeout/status bugs on 2026-07-03 (see
`planning/2026-07-03-timeout-recovery-bugfix.md`, gitignored/local-only) where several
wrong assumptions about this model — including ones this assistant stated out loud
before checking — caused real confusion. This doc exists so that doesn't happen again,
and so `docs/50-command-execution.md` can eventually be corrected to match reality
(it currently has some drift — noted inline below where relevant).

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

## Timeout semantics (as of the 2026-07-03 fix)

Two independent timeout knobs, checked every poll iteration in `_monitor_command`:

- **`io_timeout`** (default 60s): max seconds of *silence* — no stdout/stderr activity.
  When hit, **only the local SSH channel is closed** (`chan.close()`); the remote
  process is deliberately *not* killed. This is intentional — quiet periods (package
  installs, downloads, compilation) are normal and shouldn't be treated as failures.
  Raises `CommandTimeout`, which now carries the `CommandHandle` (id + pid), so the
  caller always gets a real, checkable reference back — this used to be lost via a
  fragile history-lookup-by-command-text.
- **`runtime_timeout`** (default: none): a hard wall-clock cap regardless of output
  activity. When hit, the code *attempts* to kill the remote process and now always
  closes the local channel afterward (previously that close was dead code and silently
  never ran). **The kill attempt itself is currently unreliable** — see "The PID
  problem" below. This is a known, tracked gap, not yet fully fixed.

### Recovering after a timeout

`ssh_cmd_check_status(handle_id, wait_seconds)` polls the handle's recorded state
after waiting. As of the fix, its `status` field means:

| status | meaning |
|---|---|
| `completed` | Confirmed — a real exit code was captured. |
| `running` | Still actively being monitored (rare from `check_status`'s perspective — usually you're calling it *because* the original call already returned/timed out). |
| `unknown_still_running` | The original call's monitoring stopped (e.g. `io_timeout`) without ever capturing an exit code. The remote command was not killed and is very likely still running. Call `check_status` again, or use `ssh_cmd_output` to see output collected so far. **Not** an error state. |
| `not_found` | The handle doesn't exist — often because the connection was reconnected (handles/history are connection-scoped and do not survive reconnect; `ssh_task_launch` PIDs do). |

The old bug: completion was inferred from `end_ts is not None`, but `end_ts` gets set
on *every* exit path (real completion, `io_timeout`, `runtime_timeout`, errors) — so a
command that merely went quiet was reported as `completed` with a stale/`None` exit
code. The fix checks `exit_code is not None` instead, since `exit_code` is set
*exclusively* by real command completion (`ops/run.py:_handle_command_completion`).

## The PID problem (known gap, not yet fixed)

`CommandHandle.pid` for a command launched via `ssh_cmd_run` is **not a real remote OS
process ID.** It's set here (`ops/run.py:_capture_pid`):

```python
handle.pid = chan.get_id()
```

`paramiko.Channel.get_id()` returns a small sequential integer local to this side of
the SSH `Transport` (0, 1, 2, ...) — internal channel bookkeeping, unrelated to any
process ID on the remote host. The log message even says so ("used as PID reference").

Consequence: `runtime_timeout`'s kill attempt (`task_ops._kill_remote_process(handle.pid)`)
sends `kill -15/-9 <channel_number>` to the remote host, which essentially never matches
a real process and fails (logged as a warning, not surfaced to the caller). Same root
cause likely explains why `ssh_cmd_kill` doesn't reliably kill after an `io_timeout`
either — noted as a related, not-yet-scoped follow-up in the timeout planning doc.

**This does not need inventing a new mechanism** — `ssh_task_launch` already solves the
identical problem correctly for background tasks, by capturing a real PID at launch
time (`echo "PID:$!"` on Linux, `$proc.Id` from `Start-Process -PassThru` on Windows —
see `ops/task.py:_build_launch_script`). The fix for `ssh_cmd_run` is to reuse that
pattern for synchronous execution, which is more involved because the caller is
blocking and streaming output at the same time, not just firing-and-forgetting.

## `cmd` vs `task` tools — why `task` doesn't have these bugs

| | `ssh_cmd_run` family | `ssh_task_launch` family |
|---|---|---|
| Execution model | Synchronous, blocks until done/timeout | Fire-and-forget, returns immediately |
| Output | Captured in-memory (circular buffer, tail-preserving) | Redirected to log files on the remote host |
| PID captured | No — `chan.get_id()`, a local channel number (bug, see above) | Yes — real remote OS PID, captured explicitly at launch |
| Status check | `ssh_cmd_check_status` reads *cached* handle state from this connection's history | `ssh_task_status` does a *live* liveness check every call (`kill -0 <pid>` / `Get-Process`) — no caching, so no staleness bug |
| Survives reconnect | No — handles/history are connection-scoped | Yes — PID is an OS-level identifier |
| Known gap | Kill after timeout doesn't work (fake PID) | No exit-code or log-path recall after the task exits — `ssh_task_status` only ever reports liveness, not a documented bug, more of a missing feature (tracked separately in the consolidated feature plan, Theme D) |

Because `ssh_task_status` re-queries the remote host live on every call instead of
trusting cached local state, it was never exposed to the `end_ts`-vs-`exit_code`
staleness bug that `ssh_cmd_check_status` had. And because task launch always captures
a real PID, its kill path actually works, unlike the `cmd` path's `runtime_timeout`.

## Open items for `docs/50-command-execution.md` once this is fully stable

The public docs currently have some drift worth fixing in the same pass as documenting
the behavior above:
- Example under "Strategy 3" reads `result['handle_id']` and `status['running']` (a
  boolean) — the actual fields are `result['id']` and `status['status']` (a string enum:
  `completed`/`running`/`unknown_still_running`/`not_found`/...).
- The status table lists `busy` and `killed` as `ssh_cmd_run` status values; worth
  double-checking these are real return values from that specific tool vs. from
  `BusyError`/`ssh_cmd_kill` separately, and correcting/removing if not.
- No mention anywhere that working directory and environment do not persist between
  calls, or of the `cwd` parameter's fail-closed behavior. Suggested doc line: "Each
  ssh_cmd_run is an independent process with no shell state carried between calls
  (same model as GitHub Actions steps or Ansible tasks). Use absolute paths, chain
  with &&, or pass cwd for a single call (Linux/macOS only)."
