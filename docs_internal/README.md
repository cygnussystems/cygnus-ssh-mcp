# Internal Documentation

This folder contains internal development documentation not intended for end users.

## Contents

| Document | Description |
|----------|-------------|
| [TEST-INFRASTRUCTURE.md](TEST-INFRASTRUCTURE.md) | Test VMs, platform matrix, and test procedures |
| [RELEASING.md](RELEASING.md) | Release process and PyPI publishing |
| [CMD-EXECUTION-MODEL.md](CMD-EXECUTION-MODEL.md) | How `ssh_cmd_*` actually works: no persistent shell/cwd, timeout semantics, the PID bug, cmd vs task tools |

## Quick Links

- **Running tests**: See [TEST-INFRASTRUCTURE.md](TEST-INFRASTRUCTURE.md)
- **Publishing a release**: See [RELEASING.md](RELEASING.md)
- **How cmd tools/timeouts actually work**: See [CMD-EXECUTION-MODEL.md](CMD-EXECUTION-MODEL.md)
- **AI assistant context**: See [../CLAUDE.md](../CLAUDE.md)

## User-Facing Documentation

User documentation is in the `docs/` folder:
- [docs/25-windows-support.md](../docs/25-windows-support.md) - Windows platform details
- [docs/20-platform-compatibility.md](../docs/20-platform-compatibility.md) - Supported platforms
- [docs/40-tools-reference.md](../docs/40-tools-reference.md) - All tools reference
