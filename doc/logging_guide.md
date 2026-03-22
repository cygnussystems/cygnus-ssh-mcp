# Logging Best Practices for MCP Servers in Production (Python + Claude Desktop)

## Overview

When deploying an MCP server in production (e.g., for use with Claude Desktop), it's essential to implement reliable and controlled logging. This ensures proper observability, debugging, and security — without overwhelming your system or leaking sensitive information.

This guide outlines best practices for logging in production, tailored for Python servers running under Claude Desktop.

---

## Should Production MCP Servers Log?

✅ **Yes** — but logging must be:

* Structured
* Limited to useful information
* Secure (no sensitive data)
* Configurable

---

## Default Behavior in Claude Desktop

Claude Desktop:

* **Does not manage logs** for your MCP server.
* **Does not rotate or store logs** on its own.
* Simply runs the server and passes `stdout`/`stderr` through to the UI (and optionally the terminal).

This means:

> 🚩 If your server logs to stdout and you're not capturing or rotating those logs, they are **ephemeral** and **not stored persistently** unless redirected manually.

---

## Recommended Logging Setup (Python)

Use Python’s `logging` module with a rotating file handler:

```python
import logging
from logging.handlers import RotatingFileHandler

# File path for logs
log_file = "mcp_server.log"

# Create a rotating file handler
handler = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)

# Configure root logger
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(handler)

# Silence noisy libraries
logging.getLogger('paramiko').setLevel(logging.WARNING)
logging.getLogger('PIL').setLevel(logging.WARNING)
```

### 🔀 This setup will:

* Write to `mcp_server.log`
* Rotate the log file once it exceeds **5 MB**
* Keep **3 backups** (`mcp_server.log.1`, `.2`, etc.)

---

## Log Levels

Use appropriate log levels to filter output:

| Level      | Use for                                       |
| ---------- | --------------------------------------------- |
| `DEBUG`    | Detailed internal information, dev-only       |
| `INFO`     | Routine operations, successful tool calls     |
| `WARNING`  | Deprecated usage, suspicious but non-critical |
| `ERROR`    | Tool failures, exceptions                     |
| `CRITICAL` | System crashes, corrupt state                 |

---

## Best Practices Summary

| Area                | Recommendation                                |
| ------------------- | --------------------------------------------- |
| Enable logging      | ✅ Yes                                         |
| Logging destination | File with rotation                            |
| Log level control   | Via env var or config                         |
| Sensitive data      | ❌ Never log                                   |
| Format              | Use structured (e.g., JSON) or formatted text |

---

## Optional Enhancements

* Support `--log-level` or `LOG_LEVEL` env var to toggle verbosity.
* Use JSON formatting if integrating with log aggregation tools.
* Write logs to a Claude-friendly path, e.g., `~/.claude/logs/mcp_server.log`.

---

With these practices, your MCP server will be production-ready and safely observable without risking uncontrolled log growth or exposure of sensitive information.
