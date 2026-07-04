# Command Execution Guide

## Overview

The SSH MCP Server provides robust command execution capabilities with timeout handling, output management, and process control. This guide covers the command execution tools and their usage patterns.

## Core Tools

### ssh_cmd_run (Primary Tool)

The main tool for executing commands on the remote host.

**Features:**
- Executes commands with configurable timeouts
- Captures output with status metadata
- Supports sudo elevation
- Tracks execution in command history

**Example:**
```
ssh_cmd_run(
    command="ls -la /var/log",
    io_timeout=30.0,
    runtime_timeout=60.0,
    use_sudo=False
)
```

**Three independent timeout knobs** (see [Timeout Management](#timeout-management)
below): `io_timeout` (silence-based), `wait_timeout` (elapsed-based, regardless of
activity), and `runtime_timeout` (the only one that kills). All three are checked
concurrently - whichever of `io_timeout`/`wait_timeout` fires first hands back
control without touching the remote command; `runtime_timeout` always wins outright
if it fires, and kills.

### Supporting Tools

| Tool | Purpose |
|------|---------|
| `ssh_cmd_check_status` | Monitor running command status |
| `ssh_cmd_kill` | Terminate running commands |
| `ssh_cmd_output` | Retrieve command output |
| `ssh_cmd_history` | Access execution records |
| `ssh_cmd_clear_history` | Clear command history |

---

## Timeout Management

Three independent knobs, all checked concurrently on every poll iteration. Two are
"soft" (never kill the remote command, just hand back control); one is "hard" (the
only one that ever kills).

### I/O Timeout (`io_timeout`) - soft, silence-based
- An **inactivity** timeout, not a total command timeout - it only measures silence
  since the last output
- Triggers when no output is received within the timeout period. A command that
  keeps producing output, however slowly, never triggers this - see `wait_timeout`
  below if you want a cap that applies regardless of activity
- Does **NOT** kill the remote command. Monitoring is handed off to a background
  thread that keeps collecting output and watching for the real exit code, so the
  command genuinely keeps running and finishing normally is still fully observable
  later. The response has `status='io_timeout'`, `still_running=True`, and an
  `id`/`pid` to check back with via `ssh_cmd_check_status`/`ssh_cmd_output` - or to
  end it early with `ssh_cmd_kill` if you decide you don't want to wait for it
- Default: 60 seconds
- Use for: Commands that should produce regular output

### Wait Timeout (`wait_timeout`) - soft, elapsed-based

- A **total elapsed wait** cap, regardless of output activity - unlike `io_timeout`,
  this fires even while the command is actively producing output
- Same non-killing handoff behavior as `io_timeout`: the command keeps running in
  the background, and `ssh_cmd_check_status`/`ssh_cmd_output`/`ssh_cmd_kill` all work
  on it exactly the same way afterward
- Default: `None` (disabled) - only `io_timeout`/`runtime_timeout` apply unless you
  set this explicitly
- Use for: Checking in periodically on a command that's chatty but long-running
  (e.g. a `docker pull` with a constantly-updating progress bar, which would never
  go quiet enough to trigger `io_timeout`) - lets you decide whether to keep waiting,
  do something else, or kill it, rather than being blocked until it actually finishes

### Runtime Timeout (`runtime_timeout`) - hard, the only one that kills
- Limits total execution time regardless of output activity
- Hard stop - firing this **DOES** attempt to kill the remote command, on Linux,
  macOS, and Windows (non-sudo commands; sudo'd commands are the one remaining gap -
  see `docs_internal/CMD-EXECUTION-MODEL.md` for current coverage details). This
  still applies even to a command that's already been handed off to background
  monitoring after an `io_timeout`/`wait_timeout` - it's enforced there too, with an
  internal safety ceiling applied automatically if you never set one, so a
  background-monitored command can never run completely unbounded
- Default: None (no limit) - set this generously (much longer than the command
  should ever realistically take); it's a safety net, not a UX mechanism
- Use for: Preventing runaway processes

### Timeout Priority
1. All three are checked every poll iteration; whichever of `io_timeout`/
   `wait_timeout` triggers first ends the local wait and hands off to background
   monitoring - neither kills anything
2. `runtime_timeout` is the one to rely on for an actual cap, since it's the only
   one that kills the remote process - this holds true both before and after a
   handoff to background monitoring
3. A background thread continues monitoring wall-clock duration and output after a
   soft timeout fires, so `ssh_cmd_check_status`/`ssh_cmd_output` continue to reflect
   reality (including the real exit code, once the command actually finishes) rather
   than going stale the moment this tool call returns

---

## Execution Flow

```
                    ┌─────────────────┐
                    │  ssh_cmd_run()  │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │ Command starts  │
                    │ (Handle ID      │
                    │  assigned)      │
                    └────────┬────────┘
                             │
            ┌────────────────┼────────────────┐
            │                │                │
    ┌───────▼───────┐ ┌──────▼──────┐ ┌───────▼───────┐
    │   Success     │ │   Timeout   │ │    Error      │
    │ (exit code 0) │ │ (I/O, wait, │ │ (non-zero     │
    │               │ │  or runtime)│ │  exit code)   │
    └───────┬───────┘ └──────┬──────┘ └───────┬───────┘
            │                │                │
            └────────────────┼────────────────┘
                             │
                    ┌────────▼────────┐
                    │ Store in        │
                    │ command history │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │ Return result   │
                    │ dictionary      │
                    └─────────────────┘
```

---

## Status Codes

### `ssh_cmd_run` response `status` field

| Status | Description | Exit Code |
|--------|-------------|-----------|
| `success` | Command completed | 0 |
| `command_failed` | Command exited with error | Non-zero |
| `cwd_not_found` | The `cwd` parameter didn't exist on the remote host - the command was **not** executed | N/A |
| `io_timeout` | No output within `io_timeout` - remote command was **not** killed, genuinely still running (background monitoring keeps collecting it) | N/A |
| `wait_timeout` | `wait_timeout` elapsed regardless of activity - remote command was **not** killed, genuinely still running (same as `io_timeout`) | N/A |
| `runtime_timeout` | `runtime_timeout` exceeded - an attempt was made to kill the remote command | N/A |
| `sudo_required` | Sudo access needed but not available | N/A |
| `busy` | Another `ssh_cmd_run` is already in flight on this connection | N/A |
| `error` | Unexpected failure (e.g. connection dropped) | N/A |

### `ssh_cmd_check_status` response `status` field

| Status | Description |
|--------|-------------|
| `completed` | Confirmed finished, `exit_code` is populated - including for a command that survived an `io_timeout`/`wait_timeout`, since background monitoring keeps watching for the real exit code |
| `running` | Still being actively monitored, including a command that's past an `io_timeout`/`wait_timeout` but hasn't finished yet - background monitoring has definite knowledge it's still going, not just a guess |
| `killed` | The remote process was confirmed terminated (e.g. `runtime_timeout` killed it, or a prior `ssh_cmd_kill` call found it already gone) - `exit_code` is not known, but treat this as terminal, same as `completed` |
| `completed_exit_code_unknown` | Rare fallback: monitoring stopped without a confirmed exit code (e.g. an unexpected error/lost connection during background monitoring) and a live check now confirms the remote process is no longer running - terminal, but the real exit code was never observed and cannot be recovered |
| `unknown_still_running` | Rare fallback (same caveat as `completed_exit_code_unknown`) - a live check confirms the remote command is still actually running - not a failure, call this tool again to keep checking |
| `not_found` | The `handle_id` doesn't exist (handles don't survive reconnects) |

### `ssh_cmd_kill` response `result` field

Note this is a separate field (`result`, not `status`) on the `ssh_cmd_kill` response.

| Result | Description |
|--------|-------------|
| `killed` | Process confirmed exited after the signal (or force-kill fallback) |
| `not_running` | Command was already not running when kill was attempted |
| `already_exited` | Process had already exited before the signal was sent |
| `failed_to_kill` | Process still running after signal (and force-kill, if attempted) |
| `invalid_pid` | The tracked PID was not a real value |
| `error` | Unexpected failure while attempting the kill |

---

## Output Management

### Circular Buffer
- Output stored in memory with tail preservation
- Default: 100 lines retained
- Streaming capture with line normalization

### Retrieving Output
```
# Get last N lines
ssh_cmd_output(handle_id=1001, lines=50)

# Get all captured output
ssh_cmd_output(handle_id=1001)
```

### Output in History
```
ssh_cmd_history(
    include_output=True,
    output_lines=5  # Lines per entry
)
```

---

## Long-Running Commands

For commands that may run for extended periods:

### Strategy 1: Increase Timeouts
```
ssh_cmd_run(
    command="apt-get update",
    io_timeout=120.0,      # 2 minutes between outputs
    runtime_timeout=600.0,  # 10 minutes total
    use_sudo=True
)
```

### Strategy 2: Background Task
For very long operations, use background tasks instead:
```
ssh_task_launch(
    command="long-running-script.sh",
    use_sudo=True
)
# Returns immediately with PID
# Check status later with ssh_task_status(pid=...)
```

### Strategy 3: Check and Wait
```
# Start command
result = ssh_cmd_run(command="make", io_timeout=5.0)

if result['status'] == 'io_timeout':
    handle_id = result['id']  # ssh_cmd_run returns 'id'; check_status/kill/output take it as 'handle_id'

    # Check periodically
    while True:
        status = ssh_cmd_check_status(
            handle_id=handle_id,
            wait_seconds=5.0  # Wait then check
        )
        if status['status'] == 'completed':
            break
```

Note: `runtime_timeout` firing already attempts to kill the remote command (Linux, macOS,
and Windows; non-sudo), so there's nothing left to poll for in that case. Polling to wait
out a long command is the `io_timeout` pattern, shown above.

### Strategy 4: Periodic Check-Ins on a Chatty Command

`io_timeout` only fires on silence - a command that keeps producing output (a
verbose `docker pull`, a build with a constant progress bar) never triggers it, so
`ssh_cmd_run` would otherwise block for the command's entire duration. Use
`wait_timeout` to get control back periodically regardless of activity:

```
result = ssh_cmd_run(
    command="docker pull large/image:latest",
    io_timeout=120.0,     # still catch genuine hangs/silence
    wait_timeout=20.0,    # but also check in every 20s even while it's chatty
    runtime_timeout=1800.0  # hard safety cap
)

if result['status'] in ('io_timeout', 'wait_timeout'):
    handle_id = result['id']
    # Decide here: keep polling, do something else, or ssh_cmd_kill(handle_id) to
    # end it early - the command is still running either way.
```

---

## Process Termination

### Graceful Kill (SIGTERM)
```
ssh_cmd_kill(
    handle_id=1001,
    signal=15,      # SIGTERM
    force=True,     # Use SIGKILL if needed
    wait_seconds=2.0
)
```

### Immediate Kill (SIGKILL)
```
ssh_cmd_kill(
    handle_id=1001,
    signal=9,       # SIGKILL
    force=False     # Already using force signal
)
```

---

## Concurrency

### Single Connection Behavior
- Only one command can execute at a time per connection
- Attempting concurrent execution returns `busy` status
- Use the execution lock to prevent conflicts

### Parallel Execution Options
1. **Multiple connections** - Each handles one command
2. **Pipeline commands** - Single command with pipes
3. **Background tasks** - Non-blocking execution

### Pipeline Example
```
# Execute as single command
ssh_cmd_run(
    command="find /var/log -name '*.log' | xargs grep ERROR | head -20"
)
```

---

## Sudo Operations

### Basic Sudo Usage
```
ssh_cmd_run(
    command="cat /etc/shadow",
    use_sudo=True
)
```

### How Sudo Works
1. Server retrieves sudo password from host configuration
2. Command wrapped with `sudo -S -p ''` prefix
3. Password piped to sudo via stdin
4. Output captured normally

### Verify Sudo Access
```
# Check before running privileged commands
ssh_conn_verify_sudo()
# Returns: {"available": true, "passwordless": false, "requires_password": true}
```

---

## Command History

### View Recent Commands
```
ssh_cmd_history(
    limit=10,
    reverse=True  # Most recent first
)
```

### Search History
```
ssh_cmd_history(
    pattern="apt",  # Filter by command text
    include_output=True
)
```

### History Entry Structure
```json
{
    "id": 1001,
    "command": "ls -la",
    "exit_code": 0,
    "start_time": "2024-01-15T10:30:00Z",
    "end_time": "2024-01-15T10:30:01Z",
    "pid": 4821,
    "output": ["file1.txt", "file2.txt", "..."]
}
```

Note: there is no `status` field here - `exit_code` is `null` if the command hasn't
completed (e.g. it's still running or hit an `io_timeout`). `output` is only present
when `ssh_cmd_history(include_output=True)` is used.

---

## Best Practices

1. **Set appropriate timeouts** based on expected command duration
2. **Use runtime_timeout** for commands with unpredictable output timing - it's the
   only knob that actually kills the remote command, so it's your real safety net
3. **Use wait_timeout** instead of (or alongside) `io_timeout` for commands that are
   chatty but long-running, so you still get periodic control back even though the
   command never goes quiet
4. **Check status codes** before assuming success
5. **Use background tasks** (`ssh_task_launch`) for operations that should survive
   you disconnecting/reconnecting, not just this call returning
6. **Verify sudo access** before running privileged commands
7. **Clear history** periodically if storing sensitive commands
