# SSH Command Execution Tools Documentation

## Core Tool Overview

### ssh_cmd_run (Primary Tool)
- Executes commands on remote host
- Handles both immediate and long-running operations
- Manages timeouts (I/O timeout and runtime timeout)
- Returns structured response with:
  - Command output
  - Execution metadata (PID, timestamps)
  - Status indicators (success/timeout/failure)

### Supporting Tools
- `ssh_cmd_check`: Monitors long-running command status
- `ssh_cmd_kill`: Terminates running commands
- `ssh_cmd_history`: Retrieves command execution records

---



---

## Key Interdependencies

### Execution Flow
```mermaid
graph TD
  A[ssh_cmd_run] --> B[Command execution]
  B --> C{Success?}
  C -->|Yes| D[Store in history]
  C -->|No| E[Error handling]
  D --> F[ssh_cmd_check]
  E --> G[ssh_cmd_kill]
```

### Data Flow
- All tools share the command history store
- PID tracking links execution->monitoring->termination
- Status codes propagate through response chain

---

## Error Conditions & Handling

| Scenario               | Error Type            | Handling Mechanism             | Status Code           |
|------------------------|-----------------------|---------------------------------|-----------------------|
| Command failure        | Non-zero exit code    | CommandFailed wrapper          | 'command_failed'      |
| I/O timeout            | No output activity    | Background thread monitoring   | 'io_timeout'          |
| Runtime timeout        | Process over duration | SIGTERM/SIGKILL sequence        | 'runtime_timeout'     |
| Concurrent execution   | Lock contention       | Threading.Lock primitive        | 'busy'                |
| Sudo requirements      | Privilege escalation  | Pre-execution sudo check        | 'sudo_required'       |
| Process termination    | Manual intervention   | Signal-based termination       | 'killed'/'terminated' |

---

## Implementation Details

1. **Timeout Prioritisation**
   - Runtime timeout supersedes I/O timeout if both set
   - Background thread monitors wall-clock duration

2. **Output Management**
   - Circular buffer with tail preservation (default 100 lines)
   - Streaming output capture with line normalisation

3. **State Tracking**
   - Atomic status updates to prevent race conditions
   - PID-based process lifecycle management

4. **Cleanup Guarantees**
   - Context managers ensure resource release
   - SIGKILL fallback for unresponsive processes
