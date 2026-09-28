# FreeBSD flex connection reports contradictory `system.os_type: macos`

**Date/harness:** 2026-09-28, OpenCode `openai/gpt-6-sol`, `freebsd-test`, FreeBSD 15.1. **Priority:** P2 metadata discoverability. Black-box observation; no server source read.

`ssh_conn_connect({"host_name":"freebsd-test"})` succeeded with top-level `os_type:"flex"`, `connection.os_type:"flex"`, `connection.os_version:"freebsd"`, `system.kernel:"15.1-RELEASE-p1"`, `system.hostname:"freebsd-test"`, and a `flex_note` correctly saying FreeBSD is not Linux/macOS/Windows. **But within the same response `system.os_type:"macos"`.** Memory fields also reported `mem_total_mb:"0"`, `mem_available_mb:"0"` despite the host being configured with 512 MB RAM; `system.os_name`/`system.os_version` were blank. A short sudo command printed `FreeBSD\nroot\n`, confirming kernel and privilege behavior.

**Expected:** report `system.os_type:"flex"` or `"freebsd"`, or clearly mark it unavailable. Do not present valid-looking `macos` and zero-RAM facts that could guide the model to wrong commands or capacity assumptions. Keep the useful `connection.os_type`/`flex_note` and capability warnings. No remote changes were required to reproduce.
