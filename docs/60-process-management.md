# Process Management

## Key Identifiers

The SSH MCP Server uses two types of identifiers for tracking processes:

### Handle ID
- **Purpose**: Tool-level command tracking
- **Scope**: Per-connection, sequential
- **Lifetime**: Valid until connection drops
- **Used by**: `ssh_cmd_*` tools

**Characteristics:**
- Unique per command execution
- Sequential within a connection
- Persistent in command history
- Invalidated on connection drop

**Use cases:**
- Retrieving command output
- Checking execution status
- Accessing historical commands

### Process ID (PID)
- **Purpose**: OS-level process identification
- **Scope**: System-wide on remote host
- **Lifetime**: Exists while process runs
- **Used by**: `ssh_task_*` tools

**Characteristics:**
- Assigned by remote OS kernel
- Ephemeral (exists only while process runs)
- Unique system-wide at any moment
- Recycled by OS after process ends

**Use cases:**
- Background task management
- Process monitoring and signals
- System-level debugging

---

## Identifier Relationships

```
┌─────────────────────────────────────────────────────────────┐
│                     SSH Connection                           │
│                                                              │
│   ┌────────────────────────────────────────────────────┐    │
│   │              Command History Store                  │    │
│   │  ┌─────────┐  ┌─────────┐  ┌─────────┐            │    │
│   │  │ HID:1001│  │ HID:1002│  │ HID:1003│    ...     │    │
│   │  │ PID:5001│  │ PID:5002│  │ PID:5003│            │    │
│   │  └─────────┘  └─────────┘  └─────────┘            │    │
│   └────────────────────────────────────────────────────┘    │
│                                                              │
│   ┌────────────────────────────────────────────────────┐    │
│   │              Execution Lock (Busy Lock)             │    │
│   │         Only one command runs at a time            │    │
│   └────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
                              │
                              │ SSH Channel
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                     Remote Linux Host                        │
│                                                              │
│   Process Table:                                            │
│   PID 5001 → /bin/bash (exited)                            │
│   PID 5002 → find /var -name *.log (running)               │
│   PID 5003 → sleep 300 (running)                           │
└─────────────────────────────────────────────────────────────┘
```

---

## Execution Scenarios

### Sequential Execution
```bash
# Local terminal equivalent
$ command1 ; command2 ; command3
```

```
# SSH tool equivalent
ssh_cmd_run("command1")  # Handle-ID: 1001, PID: 5001
ssh_cmd_run("command2")  # Handle-ID: 1002, PID: 5002
ssh_cmd_run("command3")  # Handle-ID: 1003, PID: 5003
```

- New Handle-ID **and** PID for each command
- Commands execute sequentially
- BusyError prevents overlap

### Pipeline Execution
```bash
# Local terminal equivalent
$ command1 | command2 | command3
```

```
# SSH tool equivalent (single command)
ssh_cmd_run("command1 | command2 | command3")  # Handle-ID: 1004, PID: 5004
```

- Single Handle-ID/PID for entire pipeline
- All commands share same execution context
- Output captured as combined stream

### Background Execution
```bash
# Local terminal equivalent
$ long_command &
```

```
# SSH tool equivalent
ssh_task_launch("long_command")  # PID: 5005
# Returns immediately, no Handle-ID
```

- Only PID returned (no Handle-ID)
- Does not block connection
- Output goes to log files

---

## Background Tasks

### Launching Tasks
```
result = ssh_task_launch(
    command="./process_data.sh",
    use_sudo=False,
    log_output=True
)
# Returns: {"pid": 12345, "stdout_log": "/tmp/task-12345.log", ...}
```

### Checking Task Status
```
status = ssh_task_status(pid=12345)
# Returns: {"pid": 12345, "status": "running", "running": true, ...}
```

### Terminating Tasks
```
result = ssh_task_kill(
    pid=12345,
    signal=15,        # SIGTERM first
    force=True,       # Then SIGKILL if needed
    wait_seconds=2.0
)
# Returns: {"pid": 12345, "result": "killed", ...}
```

### Task vs Command Comparison

| Aspect | ssh_cmd_run | ssh_task_launch |
|--------|-------------|-----------------|
| Blocking | Yes | No |
| Output capture | Memory buffer | Log files |
| Identifier | Handle-ID | PID only |
| History tracking | Yes | No |
| Timeout support | Yes | No |
| Long-running | Limited | Ideal |

---

## Orphaned Processes

**Scenario:** Network disconnect during command execution

**What happens:**
- Handle-ID invalidated (connection-specific)
- PID continues running on remote OS
- Output buffer lost

**Recovery:**

1. **Reconnect** to the host:
   ```
   ssh_conn_connect(host_name="myserver")
   ```

2. **Check last known commands:**
   ```
   ssh_cmd_history(limit=5)
   ```

3. **If process still running, kill by PID:**
   ```
   ssh_task_kill(pid=5001, use_sudo=True)
   ```

4. **Or check remotely:**
   ```
   ssh_cmd_run("ps aux | grep process_name")
   ```

---

## Frequently Asked Questions

**Q: Can multiple Handle-IDs reference the same PID?**

A: Only in rare cases:
- Process survives connection drop/reconnect
- Manual PID reuse by OS (unlikely during same session)

**Q: How are PIDs assigned?**

A: By the remote OS kernel, completely independent of Handle-IDs. The MCP server has no control over PID assignment.

**Q: What's the maximum Handle-ID value?**

A: Depends on connection duration - sequential integers per session. Resets on reconnection.

**Q: How to track processes across connections?**

A: Use PID with `ssh_task_status(pid=...)`. PIDs persist on the remote system regardless of your connection state.

**Q: What happens to background tasks if I disconnect?**

A: They continue running. The server launched them with `nohup` or equivalent, so they're immune to hangups.

---

## Process Signals

Common signals for process control:

| Signal | Number | Description |
|--------|--------|-------------|
| SIGTERM | 15 | Graceful termination request |
| SIGKILL | 9 | Immediate termination (cannot be caught) |
| SIGINT | 2 | Interrupt (like Ctrl+C) |
| SIGHUP | 1 | Hangup (terminal closed) |

### Signal Usage
```
# Graceful stop
ssh_task_kill(pid=12345, signal=15)

# Force stop
ssh_task_kill(pid=12345, signal=9)

# Graceful with force fallback
ssh_task_kill(pid=12345, signal=15, force=True, wait_seconds=5.0)
```
