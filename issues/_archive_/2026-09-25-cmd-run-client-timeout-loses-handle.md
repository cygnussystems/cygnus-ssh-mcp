# `ssh_cmd_run` longer than the MCP client's request timeout (60s) loses the command handle

| | |
|---|---|
| **Severity** | 🔴 Blocker (in the field: forced the agent to guess command IDs) |
| **Status** | **Fixed, 2026-09-25** (branch `fix/cmd-run-timeout-handoff`). Both problems (A and B) reproduced in Claude Code against `TEST_MCP_SSH_LINUX` (see "Repro results"). Root cause and fix below |
| **Reported** | 2026-09-19 (field), written up here 2026-09-25 |
| **Client** | OpenCode, models `gpt-5.5` / `gpt-5.6-terra` |
| **Target** | Ubuntu 26.04.1 LTS (MacBook Pro 2014), sudo available |
| **Original report** | `ADMIN_LIVE/MACBOOK_PRO_2014/planning/ssh-mcp-timeout-bug-report.md` |
| **Evidence** | `~/.local/share/opencode/opencode.db`, sessions `ses_f473ead7affe5tPiHzTtbG0aM9` ("SSH MCP alias and project directories") and `ses_f45d9476cffe24eVl91yQr8k1L` ("MacBook idle power consumption check") |

## Summary

When an `ssh_cmd_run` call runs longer than the **MCP client's own request timeout**, the
client stops the call with `MCP error -32001: Request timed out`. The agent never sees the
documented `io_timeout`/`wait_timeout` handoff response, so it also never gets the command
`id` or PID. The remote command keeps running (and in this case finished fine), but the
agent has no supported way to find it again.

The server's `io_timeout`/`wait_timeout` are meant to hand control back before anything else
times out, but they can't work if they're set higher than the client's limit. Nothing in the
tool descriptions or public docs mentions that limit.

## Evidence: every failure took exactly 60 seconds

These are all the `Request timed out` tool calls in the OpenCode DB, with durations taken from
the part's `state.time`:

| Session | Tool | Duration | Key inputs |
|---|---|---|---|
| …bG0aM9 | `ssh_cmd_run` | **60.01s** | `apt-get update && apt-get install -y xfce4 xfce4-goodies`, `use_sudo: true`, `io_timeout: 300`, `wait_timeout: 600`, `runtime_timeout: 1800` |
| …bG0aM9 | `ssh_cmd_history` | **60.01s** | `limit: 5, include_output: true, output_lines: 30, reverse: true, include_internal: false` |
| …bG0aM9 | `ssh_cmd_run` | **60.018s** | `bun install; docker version …; docker compose version`, `io_timeout: 300`, `wait_timeout: …` |
| …Qr8k1L | `ssh_cmd_run` | **60.008s** | `powertop --time=60 --html=… ` (runs >60s by design) |

A consistent 60.0s points to the **MCP TypeScript SDK's default request timeout (60,000 ms)**,
which OpenCode appears to use for tool calls. The failure isn't specific to apt or large
downloads. Any `ssh_cmd_run` longer than 60s on this client will fail the same way.

Exact error text:

```text
MCP error -32001: Request timed out
```

## Two separate problems

### A. The handle is lost when the client times out (main issue)

- Expected (per `docs/40-tools-reference.md` and `docs/50-command-execution.md`): when the
  caller stops waiting, the command keeps running and the response carries `status:
  io_timeout|wait_timeout`, the `id` and the `pid`.
- Actual: the client drops the request at 60s. The server is still inside its 300s/600s wait
  loop, so it never returns a response. Whatever it returns later is thrown away by the client.
- The server can't fix this directly, because the client ends the request. What the server
  *can* do is make sure the agent can always get the handle back another way (see
  suggestions).

### B. `ssh_cmd_history` also blocked for 60s while the long `ssh_cmd_run` was in flight

- `ssh_cmd_history` is a read-only lookup that should return instantly. It timed out at 60s
  too, right after the `ssh_cmd_run` failure, while the apt install was still running.
- Hypothesis (unverified): the server handles tool calls one at a time, or `ssh_cmd_history`
  waits on a lock that the in-flight `ssh_cmd_run` wait loop holds. So the tool the agent
  would use to recover is blocked by the very call it's trying to recover from.
- Later calls (`ssh_cmd_check_status(handle_id=11)` → `completed`, `exit_code: 0`, `pid:
  29218`) worked once the install finished.

## How the agent recovered (unsafe)

The previous command's ID was `10`, so the agent guessed `11` and called
`ssh_cmd_check_status(handle_id=11, wait_seconds=1)` and then `ssh_cmd_output(handle_id=11,
stream="stdout")`. This only worked because nothing else ran in between. A wrong guess could
lead the agent to re-run a non-idempotent command (for example a second `apt-get install`).

## Discoverability angle (why this belongs in this harness)

- The tool description and docs encourage generous timeouts (`wait_timeout: 600`) with
  nothing warning that the client may cut the call off at 60s. An LLM following the docs will
  hit this.
- The docs never say "keep `wait_timeout` below your client's request timeout", and the
  server doesn't report its own recommended ceiling.
- The error the agent sees comes from the transport layer and says nothing about the server,
  so the agent can't tell whether the command started, failed or is still running.

## Suggested fixes (for `PR_MCP_SSH/planning/`)

1. **Cap or default the wait below common client limits.** For example, clamp the effective
   foreground wait to ~45–50s (configurable) no matter what the caller asks for, and return
   the normal `wait_timeout` handoff. Background monitoring already covers the rest, so
   nothing is lost. Tell the caller in the response when a wait was clamped.
2. **Make history and status tools responsive while a call is in flight.** `ssh_cmd_history`,
   `ssh_cmd_check_status` and `ssh_cmd_output` shouldn't be blocked by an in-flight
   `ssh_cmd_run` wait loop (fixes problem B).
3. **Supported way to find a lost command without guessing IDs.** Options: let `ssh_cmd_history`
   show in-flight commands clearly (`status: running`, `started_at`, command text), and/or
   accept an optional caller-supplied `tag`/`client_ref` on `ssh_cmd_run` that can be looked
   up later.
4. **Document it.** In `ssh_cmd_run`'s description and `docs/50-command-execution.md`, warn
   that MCP clients often time requests out at 60s, and recommend `ssh_task_launch` (or a
   short `wait_timeout`) for anything that may take longer.

## Repro plan (for when `cygnus_ssh` is connected in this harness)

Target: `TEST_MCP_SSH_LINUX`. Scratch dir: `/tmp/llm_test/`. Clean up afterwards.

**Step 0: match the client timeout.** Claude Code's default MCP tool timeout is much longer
than 60s, so the bug won't show up by default. Start the session with `MCP_TOOL_TIMEOUT=60000`
(or run the same steps from OpenCode, which reproduced it in the field). Record which client
and timeout were used.

1. **Baseline, under the limit:**
   `ssh_cmd_run(command="sleep 20; echo done", wait_timeout=10)`
   → expect `status: wait_timeout` with `id` and `pid`. Confirms the handoff works when it
   fires first.
2. **Trigger A:**
   `ssh_cmd_run(command="for i in $(seq 1 90); do echo tick $i; sleep 1; done; echo FINISHED > /tmp/llm_test/marker", io_timeout=300, wait_timeout=600, runtime_timeout=1800)`
   → expect `MCP error -32001: Request timed out` at ~60s and no handle. (It prints steadily,
   so `io_timeout` never fires. This mirrors apt's chatty output.)
3. **Trigger B (right away, while step 2 is still running remotely):**
   `ssh_cmd_history(limit=5, reverse=true)`
   → record whether it returns instantly, returns with a delay, or also times out at 60s.
   Also try `ssh_conn_is_connected()` to see if *every* tool is blocked or just history.
4. **Recovery without guessing:** after ~90s, call `ssh_cmd_history` again. Does it list the
   step-2 command, with what status, and can you tell which entry it is from the listed fields
   alone?
5. **Confirm the command survived:** `ssh_cmd_run(command="cat /tmp/llm_test/marker")`
   → expect `FINISHED`.
6. **Sudo variant:** repeat step 2 with `use_sudo=true` (the field case used sudo) and a
   harmless command, to check that the sudo path behaves the same.
7. **Control, recommended pattern:** run the same 90s loop with `ssh_task_launch` and poll it.
   → expect no timeout. Record whether the docs would have steered an LLM to this tool
   *before* it hit the problem.
8. **Cleanup:** `rm -rf /tmp/llm_test`, and confirm no leftover `sleep`/loop processes
   (`pgrep -af 'tick'`).

For each step, record the exact call, the exact response or error, and the wall-clock
duration.

## Repro results (2026-09-25)

Client: Claude Code launched with `MCP_TOOL_TIMEOUT=60000`. Target: `TEST_MCP_SSH_LINUX`
(Debian 12). Local times are UTC+1; remote/server timestamps are UTC.

| Step | Call | Result |
|---|---|---|
| 1 Baseline | `ssh_cmd_run("mkdir -p /tmp/llm_test && date +%T && sleep 20 && echo done && date +%T", wait_timeout=10)` | ✅ `status: wait_timeout`, `id: 8`, `pid: 772`, `still_running: true`, and a clear `next_step` telling the agent to poll rather than rerun. Handoff works when it fires before the client limit. |
| 2 Trigger A | `ssh_cmd_run("date +%T; for i in $(seq 1 90); do echo tick $i; sleep 1; done; echo FINISHED > /tmp/llm_test/marker; date +%T", io_timeout=300, wait_timeout=600, runtime_timeout=1800)` sent 12:40:57 | 🔴 `MCP server "cygnus_ssh" tool "ssh_cmd_run" timed out after 60s`. No id, no pid. **A reproduced.** |
| 3 Trigger B | `ssh_cmd_history(limit=5, reverse=true, include_internal=false)` sent 12:42:00 | 🔴 Returned at **12:42:28**, exactly when the step-2 command ended remotely (`end_time 11:42:28.517 UTC`). Blocked about 28s, and only returned because under 60s of the loop was left. **B reproduced.** |
| 3b Is every tool blocked? | Second 90s trigger (timed out at 60s again, as expected), then `ssh_conn_is_connected()` sent 12:43:40 | 🔴 Returned `true` at **12:44:11**, again exactly when the command ended (`end_time 11:44:11.06`). **Every tool, even a trivial status check, waits behind an in-flight `ssh_cmd_run`.** So B is a server-wide one-request-at-a-time problem, not a history-specific one. |
| 4 Recovery | `ssh_cmd_history` output from step 3 | 🟡 After the command finished it was listed (`id: 9`, `pid: 776`, full command text, `exit_code: 0`), so it was identifiable. But you can only see it **after** it finishes, because the history call itself is blocked while it runs. A command that runs longer than the client timeout can't be looked up at all while it's running. |
| 5 Survived? | `ssh_cmd_run("cat /tmp/llm_test/marker")` | ✅ `FINISHED`. The remote command was not killed. |
| 6 Sudo variant | 75s loop writing `whoami` to a marker, `use_sudo=true`, `io_timeout=300`, `wait_timeout=600` | 🔴 Same 60s timeout. The command completed (`marker_sudo` contained `root`, owned by root), listed as `id: 12`. Same behavior as without sudo. |
| 7 Control | `ssh_task_launch(90s loop, stdout_log="/tmp/llm_test/task.log")` | ✅ Returned instantly with `pid: 967`. The next tool call (`ssh_conn_is_connected`) was instant. `ssh_task_status(967)` showed `running`, then `exited`; the marker was written. The `ssh_task_launch` description does steer toward it ("Prefer this over ssh_cmd_run for … package installs … large downloads"), but the `ssh_cmd_run` description *also* suggests `io_timeout` 300+ for exactly those cases, which leads straight into this bug. |
| 8 Cleanup | `sudo rm -rf /tmp/llm_test`; `ps` check | ✅ Directory gone, no leftover processes. |

**Refined conclusions**

- **A** is fully explained by the client timeout. The server works as designed, but any
  `wait_timeout`/`io_timeout` longer than the client's limit (60s in OpenCode and many other
  MCP clients) can't take effect. Suggested fix #1 (clamp the foreground wait under ~50s)
  would have prevented every failure seen in the field and here.
- **B** is more serious than first thought: while an `ssh_cmd_run` is blocking, the server
  answers **no** other tool calls. Recovery tools (`ssh_cmd_history`, `ssh_cmd_check_status`,
  `ssh_cmd_output`) are useless until the command ends. Suggested fix #2 should cover the
  whole server, not just history.
- Handle IDs start from wherever internal probes left off (the first user command after
  connecting was `id: 8`, because the connection probes used 1–7). This is another reason
  guessing IDs, as the field agent did, is unreliable.

## Root cause (code analysis, 2026-09-25)

- **B: the event loop is blocked.** `ssh_cmd_run` is declared `async def`
  (`src/cygnus_ssh_mcp/server.py`), but it calls the synchronous, blocking
  `mcp.ssh_client.run(...)` directly. The wait loop (`select.select` in `ops/run.py`) runs
  on the event-loop thread, so FastMCP can't serve any other request until it returns. That
  explains why every tool (even `ssh_conn_is_connected`) waited for the command to end.
  Fix: run the blocking call in a worker thread (`await asyncio.to_thread(...)`). Other
  `async` tools that call paramiko synchronously have the same flaw, but they're usually short.
- **A: the server can't outlast the client.** Nothing limits a single call's foreground wait,
  so `io_timeout=300` or `wait_timeout=600` goes past a 60s client limit. The
  `io_timeout` field description even recommends "300+" for package installs.
  Fix: cap the foreground wait per call (default ~50s, configurable), return the normal
  `wait_timeout` handoff when the cap fires, and tell the caller the wait was capped. Reword
  the `io_timeout` guidance.

## Fix (2026-09-25)

- **B:** `ssh_cmd_run` now calls `client.run()` via `asyncio.to_thread`. Verified live on
  `linux-test`: during a 70s run, `ssh_conn_is_connected` and `ssh_cmd_history` answered in
  0.00s (down from about 28s, i.e. until the command ended), and history showed the running
  command (`id`, `pid`, `end_time: null`).
- **A:** a single `ssh_cmd_run` call blocks for at most 50s by default (`--max-wait` /
  `MCP_SSH_MAX_WAIT`, `0` = off). Verified live: a 70s loop with `wait_timeout=600`
  returned at 50.1s with `status: wait_timeout`, `id`, `pid`, `wait_capped: true`,
  `requested_wait_timeout: 600`, and later `ssh_cmd_check_status` reported
  `completed`/`exit_code: 0`.
- **Discoverability:** the `io_timeout`/`wait_timeout` descriptions and the `ssh_cmd_run`
  docstring now explain the cap and point to `ssh_task_launch` for long work (no more "set
  this high (300+)"). `docs/50-command-execution.md`, `docs/40-tools-reference.md` and
  `docs_internal/CMD-EXECUTION-MODEL.md` are updated.
- **Recovery without guessing IDs (suggestion #3):** with B fixed, `ssh_cmd_history` lists
  the in-flight command, so no ID guessing is needed. A caller-supplied `tag` was not added.
- Regression tests: `testing_mcp/test_tool__responsiveness.py`.
- Verified through the live MCP connection (Claude Code) on `MACBOOK-2015`: a 70s loop with
  `wait_timeout=600` returned at 50s with `wait_capped: true`; `ssh_conn_is_connected` and
  `ssh_cmd_history` sent in parallel answered while it ran (history showed `id: 8`,
  `end_time: null`); `ssh_cmd_check_status` then reported `completed`, `exit_code: 0`.
- Not yet run against the Windows target.

## Open questions

- Does OpenCode have a setting to raise the MCP tool-call timeout? (Its `mcp.<name>.timeout`
  setting only covers *fetching tools*, default 5000 ms, per our OpenCode notes.) Even if it
  does, the server shouldn't depend on it being raised.
- ~~Is problem B a general single-request-at-a-time limit in the server, or specific to
  `ssh_cmd_history`?~~ Answered: it applies to every tool (step 3b).
