# `ssh_cmd_check_status(wait_seconds=90)` hits OpenCode's 60s request timeout

| | |
|---|---|
| **Severity** | 🟡 Confusing / long polling fails with transport error |
| **Status** | **Fixed, 2026-09-26** (see "Fix"). Reproduced in OpenCode 2026-09-26 |
| **Target** | `TEST_MCP_SSH_LINUX` (Debian 12) |
| **Evidence** | `test_results/2026-09-26-opencode-timeout-fix-retest.md`, section 3a |

The 50s per-call cap on `ssh_cmd_run` does not apply to `ssh_cmd_check_status`. Its tool description says “Wait for the specified duration, then check the status,” but does not caution that a wait exceeding the client's request timeout can lose the tool response. The original `ssh_cmd_run` bug is fixed; this is a distinct path.

Exact calls and responses:

```text
ssh_cmd_run(command="for i in $(seq 1 90); do echo tick $i; sleep 1; done", io_timeout=300, wait_timeout=1)
→ {"status":"wait_timeout","id":19,"pid":1075,"timeout_seconds":1.0,"still_running":true,"error":"Command wait_timeout reached after 1.0 seconds (PID: 1075, ID: 19)"}  [output tick 1–2 omitted]

ssh_cmd_check_status(handle_id=19, wait_seconds=90)
→ MCP error -32001: Request timed out

ssh_cmd_check_status(handle_id=19, wait_seconds=1)
→ {"handle_id":19,"waited_seconds":1.0,"status":"running","exit_code":null,"pid":1075,"output_available":true,"output_lines":71,"next_step":"Not confirmed complete. Call ssh_cmd_check_status again to keep polling, or ssh_cmd_output(handle_id) to inspect output collected so far. Do not rerun this command."} [timestamp omitted]
```

The known ID made recovery possible without guessing, and the remote command eventually completed. Recommend capping `wait_seconds` below common MCP client limits, or explicitly documenting/recommending short waits (e.g., 1–10s). A cap would be more robust for a first-time consumer.

## Fix (2026-09-26)

- **Root cause:** `ssh_cmd_check_status` passed `wait_seconds` straight to `asyncio.sleep()`;
  the 50s cap added for `ssh_cmd_run` didn't cover it.
- **Fixed:** `wait_seconds` is clamped to the same per-call cap (`--max-wait` /
  `MCP_SSH_MAX_WAIT`, default 50s). Every response's `waited_seconds` reports the wait actually
  applied. The parameter description and `docs/40-tools-reference.md` now say so and recommend
  short (1–10s) polls.
- **Regression test:** `testing_mcp/test_tool__task_launch_logs.py::test_check_status_wait_is_capped`
  (cap patched to 3s; `wait_seconds=90` returns in ~3s with `waited_seconds: 3.0`, status `running`).
