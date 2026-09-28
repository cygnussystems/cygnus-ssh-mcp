# OpenWrt connection metadata reports empty root user and KiB-scale values labeled MB

**Date/harness:** 2026-09-28, OpenCode `openai/gpt-6-sol`, `openwrt-test` (OpenWrt 25.12.5/BusyBox, ~192 MB VM). **Priority:** P2 truthful capacity and identity metadata. Black-box result; no server source read.

`ssh_conn_connect({"host_name":"openwrt-test"})` succeeded with `connected_to:"root@192.168.1.39"`, top-level `user:"root"`, `system.os_name:"OpenWrt"`, `os_version:"25.12.5"`, but `connection.user:""`, `system.user:""`, `system.hostname:""`, `system.mem_total_mb:"171240"`, `system.mem_free_mb:"122792"`, `system.mem_available_mb:"106892"`. `ssh_conn_status({})` also returned `user:""`. A short `ssh_cmd_run({"command":"id -un; hostname; free -m; df -h /tmp; uname -s","wait_timeout":10})` printed `root` and `ash: hostname: not found`. BusyBox `free -m` printed `Mem: 171240 ... available 106828`, while `/tmp` was an 83.6 MB tmpfs. **Independent unit check** after reconnect: `ssh_cmd_run({"command":"grep -E '^(MemTotal|MemFree|MemAvailable):' /proc/meminfo; id -un; test ! -e /tmp/llm_test && echo SCRATCH_ABSENT","wait_timeout":5})` → `MemTotal: 171240 kB`, `MemAvailable: 106780 kB`, `root`, `SCRATCH_ABSENT`. The server copied *KiB* numbers into fields named *_mb* without division (171240 kB ≈ 167 MiB, not 171240 MB). The `hostname` utility is absent, explaining blank hostname, but not making it a meaningful reported host identity.

`ssh_host_disconnect({})` later returned `"Successfully disconnected from @192.168.1.39"`, propagating the empty username into user-visible guidance. **Expected/fix direction:** use the known authenticated SSH user (`root`) for connection/status identity, obtain memory from a unit-explicit source such as `/proc/meminfo` and convert KiB → MiB, and return `unknown`/null rather than a misleading empty hostname if no supported query works. A model choosing workloads from a claimed 171 GB RAM figure could overload this constrained router VM.

## Fix (2026-09-28, branch `feature/operation-progress`)

- **Memory:** BusyBox `free` ignores `-m` and prints KiB. Linux-class hosts now read
  `/proc/meminfo` (always kB) and divide by 1024. OpenWrt now reports `mem_total_mb: 167`
  (was 171240). The values are rounded down, so GNU hosts can differ by 1 MB from `free -m`.
- **User:** OpenWrt has no `whoami`. The probes now fall back to `id -un`, and then to the
  authenticated SSH user, so `connection.user`, `system.user`, `ssh_conn_status` and the
  disconnect message ("disconnected from root@192.168.1.39") are all `root`.
- **Hostname:** there's no `hostname` binary, so it's read from `/proc/sys/kernel/hostname`,
  falling back to `uname -n`. OpenWrt now reports `openwrt-test`.
- **Unknown instead of empty:** any status probe that prints nothing now reports `n/a` rather
  than `""`.
- **Verified live:** on OpenWrt, Alpine, FreeBSD, Debian and macOS, the values match `id -un`,
  `uname -n` and the host's own memory source. Debian and macOS are otherwise unchanged.
- **Tests:**
  - `testing_mcp/test_unit_status_metadata.py` (offline, 3 tests);
  - `testing_limited/test_limited_platforms.py::test_connect_metadata_is_truthful`.

  Both fail on the old code.
