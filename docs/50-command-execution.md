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

### I/O Timeout (`io_timeout`)
- Monitors output activity
- Triggers when no output received within the timeout period
- Default: 60 seconds
- Use for: Commands that should produce regular output

### Runtime Timeout (`runtime_timeout`)
- Limits total execution time
- Hard stop regardless of output activity
- Default: None (no limit)
- Use for: Preventing runaway processes

### Timeout Priority
1. If both timeouts are set, whichever triggers first ends the command
2. Runtime timeout takes precedence for planning purposes
3. Background thread monitors wall-clock duration

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
    │ (exit code 0) │ │ (I/O or     │ │ (non-zero     │
    │               │ │  runtime)   │ │  exit code)   │
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

| Status | Description | Exit Code |
|--------|-------------|-----------|
| `success` | Command completed successfully | 0 |
| `command_failed` | Command exited with error | Non-zero |
| `io_timeout` | No output within timeout | N/A |
| `runtime_timeout` | Total time exceeded | N/A |
| `busy` | Another command is running | N/A |
| `killed` | Manually terminated | N/A |

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
result = ssh_cmd_run(command="make", runtime_timeout=5.0)

if result['status'] == 'runtime_timeout':
    handle_id = result['handle_id']

    # Check periodically
    while True:
        status = ssh_cmd_check_status(
            handle_id=handle_id,
            wait_seconds=5.0  # Wait then check
        )
        if not status['running']:
            break
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
    "handle_id": 1001,
    "command": "ls -la",
    "status": "success",
    "exit_code": 0,
    "start_time": "2024-01-15T10:30:00Z",
    "end_time": "2024-01-15T10:30:01Z",
    "output_snippet": ["file1.txt", "file2.txt", "..."]
}
```

---

## Best Practices

1. **Set appropriate timeouts** based on expected command duration
2. **Use runtime_timeout** for commands with unpredictable output timing
3. **Check status codes** before assuming success
4. **Use background tasks** for operations > 5 minutes
5. **Verify sudo access** before running privileged commands
6. **Clear history** periodically if storing sensitive commands
