# Synology DSM: SFTP sees a different filesystem than the shell

**Date:** 2026-09-28. **Found by:** a connectivity check against the production Synology NAS
(`OTL9-NAS`, 192.168.1.3; DS216play, DSM 7.1.1, armv7, kernel 3.10). **Priority:** P1: wrong
answers, and writes could land on the wrong file.

DSM's SFTP server doesn't show the real filesystem. SFTP `/` is the list of shared folders
(`home`, `music`, `photo`, `web`, ...), and SFTP `/home` is the user's own home. The shell sees the
real root (`/etc`, `/tmp`, `/volume1`, ...). Every SFTP-based tool therefore resolved paths
differently from `ssh_cmd_run`:
- `ssh_file_stat /tmp` returned `exists: false`.
- `ssh_file_stat` on the user's own home directory (`/volume1/homes/claude`, where the shell
  starts) returned `exists: false`.
- An SFTP write to `/home/x` would really write `/volume1/homes/claude/x`.

Two smaller problems:
- `os_name` and `os_version` were `Unknown`, because DSM has no `/etc/os-release`.
- `mem_available_mb` was `n/a`, because kernel 3.10 has no `MemAvailable`.

## Fix (2026-09-28, branch `feature/operation-progress`)

- **SFTP check at connect (Linux and flex hosts):** once SFTP opens, it must be able to `stat` the
  shell's home directory.
  - If it can't, `capabilities.sftp` is `false`, with the warning "a usable SFTP subsystem -
    missing, or showing a different filesystem than the shell, e.g. Synology DSM's share-only
    SFTP".
  - Every SFTP-based tool then returns "SFTP is not available on this host for normal paths: its
    SFTP server shows a different filesystem than the shell ... Use ssh_cmd_run instead". This
    is the same path as OpenWrt, which has no SFTP at all.
  - `ssh_file_stat` returns `exists: null` with that reason.
  - If the check itself fails (for example, `pwd` can't be read), SFTP stays enabled, so a
    hiccup can't disable it on a normal host.
- **OS info:** read from `/etc/VERSION` when there's no `/etc/os-release`. It now reports
  `Synology DSM`, `7.1.1`, and the build number.
- **Available memory:** estimated as free + buffers + page cache when the kernel has no
  `MemAvailable`. It now reports 216 MB.
- **Verified live, read-only:** the NAS reports all of the above. Alpine and FreeBSD still have
  `sftp: true`.
- **Tests:**
  - `testing_mcp/test_unit_no_sftp.py` (two new tests; both fail on the old code).
  - The NAS now runs in `testing_limited/` as a production host: scratch only in its home
    directory, no sudo tests.
  - A new `testing_limited/test_limited_admin.py` walks the everyday admin workflow on all
    limited hosts.
