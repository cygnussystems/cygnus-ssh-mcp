# Logging Guide

## Overview

When deploying the SSH MCP server (e.g., with Claude Desktop), proper logging ensures observability and debugging capability without overwhelming your system or leaking sensitive information.

## Default Behavior

Claude Desktop and other MCP clients:
- Do **not** manage logs for your MCP server
- Do **not** rotate or store logs automatically
- Simply run the server and pass `stdout`/`stderr` through

This means logs are **ephemeral** unless you configure file-based logging.

---

## Recommended Setup

### Basic File Logging

```python
import logging
from logging.handlers import RotatingFileHandler

# Log file path
log_file = "mcp_server.log"

# Create rotating file handler
handler = RotatingFileHandler(
    log_file,
    maxBytes=5 * 1024 * 1024,  # 5 MB
    backupCount=3
)

# Set format
formatter = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
handler.setFormatter(formatter)

# Configure root logger
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(handler)

# Silence noisy libraries
logging.getLogger('paramiko').setLevel(logging.WARNING)
```

### File Rotation Behavior
- Rotates when file exceeds 5 MB
- Keeps 3 backup files: `mcp_server.log.1`, `.log.2`, `.log.3`
- Oldest backups automatically deleted

---

## Log Levels

| Level | Use For |
|-------|---------|
| `DEBUG` | Detailed internal information (dev only) |
| `INFO` | Routine operations, successful tool calls |
| `WARNING` | Deprecated usage, non-critical issues |
| `ERROR` | Tool failures, exceptions |
| `CRITICAL` | System crashes, corrupt state |

### Setting Log Level

**Via environment variable:**
```bash
export LOG_LEVEL=DEBUG
python mcp_ssh_server.py
```

**Via command line (if implemented):**
```bash
python mcp_ssh_server.py --log-level DEBUG
```

---

## Log Locations

### Recommended Paths

**Linux/macOS:**
```
~/.claude/logs/mcp_ssh_server.log
/var/log/mcp/ssh_server.log  # System-wide
```

**Windows:**
```
%USERPROFILE%\.claude\logs\mcp_ssh_server.log
%APPDATA%\MCP\logs\ssh_server.log
```

### Claude-Friendly Location
```python
import os
log_dir = os.path.expanduser("~/.claude/logs")
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, "mcp_ssh_server.log")
```

---

## Security Considerations

### Never Log
- Passwords (SSH or sudo)
- Private keys
- Session tokens
- Full command outputs (may contain secrets)
- Host configuration contents

### Safe to Log
- Connection events (without credentials)
- Tool names and parameters (sanitized)
- Timestamps and durations
- Error messages (without sensitive context)
- Handle IDs and PIDs

### Sanitization Example
```python
def sanitize_command(cmd: str) -> str:
    """Remove potential secrets from command logging."""
    # Replace password arguments
    cmd = re.sub(r'(-p\s*)\S+', r'\1****', cmd)
    cmd = re.sub(r'(password=)\S+', r'\1****', cmd)
    return cmd
```

---

## JSON Logging

For integration with log aggregation tools:

```python
import json
import logging

class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_data = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_data)

handler = RotatingFileHandler("mcp_server.json.log", maxBytes=5*1024*1024)
handler.setFormatter(JsonFormatter())
```

---

## Best Practices Summary

| Area | Recommendation |
|------|----------------|
| Enable logging | Yes |
| Destination | File with rotation |
| Log level control | Via env var or config |
| Sensitive data | Never log |
| Format | Structured text or JSON |
| Rotation | 5 MB with 3 backups |

---

## Troubleshooting

### Logs Not Appearing
1. Check file permissions
2. Verify log path exists
3. Ensure handler is attached to root logger
4. Check log level isn't filtering messages

### Logs Too Verbose
```python
# Silence specific loggers
logging.getLogger('paramiko').setLevel(logging.WARNING)
logging.getLogger('paramiko.transport').setLevel(logging.WARNING)
```

### Log File Growing Too Large
- Reduce `maxBytes` in RotatingFileHandler
- Increase `backupCount` for more history
- Lower log level to `WARNING` in production

---

## Production Checklist

- [ ] File-based logging configured
- [ ] Log rotation enabled
- [ ] Sensitive data never logged
- [ ] Log level configurable
- [ ] Noisy libraries silenced
- [ ] Log directory has proper permissions
- [ ] Log path added to .gitignore
