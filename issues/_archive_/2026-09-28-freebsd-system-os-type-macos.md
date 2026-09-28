# FreeBSD flex connection reports contradictory `system.os_type: macos`

**Date/harness:** 2026-09-28, OpenCode `openai/gpt-6-sol`, `freebsd-test`, FreeBSD 15.1. **Priority:** P2 metadata discoverability. Black-box observation; no server source read.

`ssh_conn_connect({"host_name":"freebsd-test"})` succeeded with top-level `os_type:"flex"`, `connection.os_type:"flex"`, `connection.os_version:"freebsd"`, `system.kernel:"15.1-RELEASE-p1"`, `system.hostname:"freebsd-test"`, and a `flex_note` correctly saying FreeBSD is not Linux/macOS/Windows. **But within the same response `system.os_type:"macos"`.** Memory fields also reported `mem_total_mb:"0"`, `mem_available_mb:"0"` despite the host being configured with 512 MB RAM; `system.os_name`/`system.os_version` were blank. A short sudo command printed `FreeBSD\nroot\n`, confirming kernel and privilege behavior.

**Expected:** report `system.os_type:"flex"` or `"freebsd"`, or clearly mark it unavailable. Do not present valid-looking `macos` and zero-RAM facts that could guide the model to wrong commands or capacity assumptions. Keep the useful `connection.os_type`/`flex_note` and capability warnings. No remote changes were required to reproduce.

## Fix (2026-09-28, branch `feature/operation-progress`)

- **Root cause:** flex hosts reuse the macOS OS-info probes, which hard-coded
  `OS_TYPE:macos` and used macOS-only commands (`hw.memsize`, `vm_stat`, `sw_vers`,
  `machdep.cpu.brand_string`). On FreeBSD these gave 0 MB of memory and a blank OS name and
  version.
- **Fix:**
  - `system.os_type` is `macos` only when `uname -s` is Darwin; otherwise it's the real kernel
    name, lowercased (`freebsd`). The top-level and connection `os_type` stay `flex`.
  - Memory falls back to `hw.physmem`, and free/available memory to
    `vm.stats.vm.v_free_count` / `v_inactive_count` × `hw.pagesize`.
  - OS name and version fall back to `uname -s` and `freebsd-version`, and the CPU model to
    `hw.model`.
- **Verified live on FreeBSD 15.1:** `os_type: freebsd`, `os_name: FreeBSD`,
  `os_version: 15.1-RELEASE-p1`, `mem_total_mb: 473` (= `hw.physmem`), free 382 MB,
  available 392 MB. macOS is unchanged (`os_type: macos`, 16384 MB).
- **Tests:** same as the OpenWrt metadata issue. Both fail on the old code.
