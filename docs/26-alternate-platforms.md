# Alternate Platforms (`flex`)

!!! warning "Work in progress"
    Support for non-standard SSH targets is newer and less battle-tested than the
    Linux/macOS/Windows support described elsewhere in these docs. It's been
    verified against several real devices (see [Tested Devices](#tested-devices)
    below), but the space of routers, NAS boxes, and embedded systems is huge -
    expect rough edges on hardware that hasn't been tried yet, and please open an
    issue if you hit one.

## What This Is

Beyond the three fully-supported platforms, cygnus-ssh-mcp can also connect to
**any other SSH target that responds to a basic shell command** - routers, NAS
boxes, BSD-kernel appliances, and other embedded Linux devices that aren't
Linux, macOS, or Windows in the way this project normally means those words.
These connect with `os_type` reported as **`flex`**.

Two genuinely different situations both fall under "alternate platform," and
it helps to keep them separate:

1. **A different kernel entirely** - `uname -s` succeeds but reports something
   that isn't `Linux` or `Darwin` (e.g. `FreeBSD`). This is `os_type='flex'`.
2. **A "Linux" kernel with a minimal, non-GNU userland** - BusyBox-based
   devices (most consumer routers, Alpine Linux, many NAS boxes) report
   `Linux` via `uname -s` just like a full Debian server does, so they detect
   as `os_type='linux'` - but their `find`/`stat`/`du`/`tar`/`ps`/`xargs`
   often only support a smaller flag set than GNU coreutils. Both cases are
   handled the same way: a one-time **capability probe**, run once at connect
   time.

## Capability Probing

For `os_type` `linux` or `flex`, connecting runs one batched, read-only shell
script that checks exactly the shell/coreutils features this project's tools
actually depend on:

| Capability | What it gates |
|---|---|
| `bash` | Whether `bash` is used for sudo/background-task command wrapping, or a portable `sh` fallback |
| `find_printf` | Fast recursive directory listing (`ssh_dir_list_advanced`, `ssh_dir_search_glob`) |
| `find_depth` | Depth-limited recursive `find` |
| `stat_c` | GNU `stat -c` format strings (permission restoration after a sudo'd file edit) |
| `du_sb` | Combined `du -s -b` (directory size in exact bytes) |
| `tar_strip_components` | Archive extraction with path stripping |
| `tar_keep_old_files` | Archive extraction without overwriting existing files |
| `ps_pgid` | Killing a sudo'd command's whole process group, not just its outer wrapper PID |
| `xargs_0` | Null-delimited batch file operations |
| `sudo` | Whether `sudo` is present at all |
| `tmp_writable` | Whether `/tmp` can be written to |

The results are returned by `ssh_conn_connect` (and `ssh_conn_host_info`) as
`capabilities` (raw per-key `true`/`false`) and `capability_warnings`
(plain-English notes for anything confirmed missing).

**A capability's absence never breaks something that would otherwise work.**
Only a *confirmed* `false` blocks a tool - a probe hiccup, or simply not
probing at all (macOS/Windows connections skip this entirely, since they're
already fully supported), is treated as "not confirmed missing" and never
gates anything.

## What Happens When a Capability Is Missing

Tools that depend on a missing capability raise a clear error naming exactly
what's unavailable and, where one exists, a concrete fallback - for example:

```
This operation needs GNU find's -printf, which this host's find doesn't
support (looks like a BusyBox-style find). Fallback: ssh_dir_list_files_basic
(non-recursive filenames) plus ssh_file_stat per entry for metadata - both are
SFTP-based and unaffected by this, at the cost of one call per directory level
instead of one call for the whole tree.
```

Nothing silently degrades or produces a cryptic remote command failure - a
missing capability either has a documented workaround or an honest "no clean
fallback exists" explanation, both in the error and in the affected tool's
own docstring.

## Tested Devices

| Device | `os_type` | `os_subtype` | Notes |
|---|---|---|---|
| Alpine Linux (BusyBox) | `linux` | - | No `bash`, no GNU `find -printf`/`ps -o pgid=`; `tar --strip-components` works but `--keep-old-files` doesn't - two independently-gated capabilities, not one |
| OpenWrt | `linux` | - | Root-only image, dropbear SSH, most GNU extensions absent |
| FreeBSD | `flex` | `freebsd` | No `bash` by default; login shell is plain `/bin/sh` |
| Synology DSM (NAS) | `linux` | - | Full GNU coreutils, working `sudo` - behaves like a normal Linux server despite the ARM/embedded hardware |

## Known Limitations

- **Some devices reject shell access entirely, and no capability probe can
  fix that.** A Synology **SRM** router (as opposed to a DSM NAS - the two
  are different OSes despite both being Synology products) was tested and
  rejected every single command - even a bare `echo` - immediately after a
  successful SSH login, for an account with admin-level web UI permissions.
  This is a device/account permission restriction (a PAM account-phase
  rejection, not a wrong password), not a shell-syntax gap - `ssh_conn_connect`
  fails with a diagnostic explaining this distinction when it happens, rather
  than a generic "connection failed."
- **`doas`** (the `sudo` alternative used on Alpine/BSD by default) isn't
  supported yet - only real `sudo` is currently handled for elevation.
- Devices without `uname` at all (some highly restrictive vendor CLIs) can't
  be identified and won't connect.
- A read-only root filesystem, or a missing/read-only `/tmp`, will likely
  break more tools than the capability gate currently catches - this hasn't
  been tested against such a device yet.

## See Also

- [Platform Compatibility](20-platform-compatibility.md) - the three fully-supported platforms
- [Windows Support](25-windows-support.md)
