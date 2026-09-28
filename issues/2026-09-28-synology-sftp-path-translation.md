# Synology DSM: make SFTP usable again by translating shell paths

| | |
|---|---|
| **Severity** | 🟡 Lost capability: no file transfers to/from a Synology NAS since 1.6.0 |
| **Status** | Open, proposed for 1.6.1 |
| **Found** | 2026-09-28, while reviewing the 1.6.0 release |
| **Host** | `OTL9-NAS` 192.168.1.3, DS216play, DSM 7.1.1, armv7, kernel 3.10. **PRODUCTION**: work only in a scratch folder inside claude's home directory, no sudo writes, don't retry failed logins (DSM auto-block) |
| **Background** | [`_archive_/2026-09-28-synology-sftp-sees-different-filesystem.md`](_archive_/2026-09-28-synology-sftp-sees-different-filesystem.md) |

## The situation

A Synology NAS gives two views of the same files, with different paths:

| | Shell (`ssh_cmd_run`, tasks, search, copy, archives) | SFTP (read, write, stat, transfer, line edits) |
|---|---|---|
| Root `/` | The real filesystem: `/etc`, `/tmp`, `/volume1`, ... | Only the shared folders: `home`, `music`, `photo`, `web`, ... |
| Your home | `/volume1/homes/claude` | `/home` |
| A share | `/volume1/photo` | `/photo` |
| `/tmp`, `/etc` | Exist | Not reachable |

**Before 1.6.0:** the SFTP tools worked, but only with SFTP-style paths (`/home/notes.txt`,
`/photo/x.jpg`). The real paths the shell reports failed: `ssh_file_stat` called the user's own
home directory and `/tmp` "not found". A model has no way to know the two sets of tools use
different paths, so it got confident wrong answers, and could write somewhere other than where
it believed.

**Since 1.6.0 (stopgap):** at connect the server sees that SFTP can't reach the shell's home
directory and switches SFTP off for the host (`capabilities.sftp: false`). Every SFTP tool then
says "use `ssh_cmd_run` instead". That removed the wrong answers, but it also removed what used
to work:
- Text files still work through `ssh_cmd_run` (`cat`, `printf > file`).
- **Binary files can't be copied between the local machine and the NAS through the MCP at
  all**: `ssh_file_transfer` and `ssh_dir_transfer` are off, and `ssh_cmd_run` can't carry
  binary data.
- The same applies to any server whose SFTP is jailed to a subtree, e.g. OpenSSH
  `ChrootDirectory` for SFTP-only accounts.

## Options

### 1. Translate shell paths to SFTP paths (recommended)

When SFTP's view differs, keep SFTP on and let every SFTP-based tool accept the **real shell
paths**, translating them behind the scenes. The mapping isn't guessed; it's learned at connect:

1. The SFTP check already knows the shell's home directory (`pwd`, e.g. `/volume1/homes/claude`)
   and SFTP's own home (`sftp.normalize('.')`, here `/`).
2. List SFTP's `/` (the share names) and resolve each share's real location with the shell,
   read-only, e.g. `readlink -f` or the `/volume*/<share>` path that exists. That gives
   `{"/volume1/photo": "/photo", "/volume1/homes/claude": "/home", ...}`.
3. Check the mapping before relying on it: for one known entry, `stat` it both ways (same size
   and mtime). If that fails, fall back to today's behavior (SFTP off, clear error).

Then:
- A tool path under a mapped prefix is rewritten (longest prefix first) before the SFTP call.
  Results report the **shell path**, so what tools return still matches `ssh_cmd_run`.
- A path outside every share (`/tmp`, `/etc`, `/volume1/@appstore`) gives a clear, specific
  error: "SFTP on this host can only reach the shared folders (`/volume1/homes/claude`,
  `/volume1/photo`, ...); `/tmp/x` isn't in one - use `ssh_cmd_run`, or a path inside a share".
- `capabilities.sftp` becomes `true` again, plus something like
  `sftp_paths: "shares_only"` and the mapping, reported in `ssh_conn_connect` /
  `ssh_conn_host_info` with a plain-English warning.
- `ssh_file_stat` keeps its 1.6.0 promise: `exists: false` only for a real "not found" (inside
  a share), `exists: null` plus the reason for an unreachable path.

**Pros:**
- Every file tool works with the paths a model already sees, and uploads/downloads work again.
- The mapping is discovered rather than hard-coded to DSM, so it also covers other jailed SFTP
  setups where shares map to real directories.

**Cons:**
- The most work of the three options.
- Every SFTP call site needs to go through the translation. They already share one entry point
  (`SshClient.open_sftp()`), but the path arguments are spread across ~13 call sites. The
  cleanest route is a small wrapper object returned by `open_sftp()` that translates paths
  itself.

### 2. Keep SFTP on, with a warning

Don't disable SFTP; instead warn at connect that its paths differ from the shell's, and have
tools include which view a path was resolved in.

- **Pros:** little work; transfers work for anyone who uses SFTP-style paths.
- **Cons:** brings back the trap 1.6.0 closed. A model must understand two sets of paths, and
  `ssh_file_stat` on a real path still says "not found". That goes against the project's
  "no acrobatics" goal.

### 3. Per-host setting to force SFTP on

A host-config field (e.g. `sftp: force`) that skips the check.

- **Pros:** trivial; useful as an escape hatch in any case.
- **Cons:** no help to a model that isn't told about it, and it brings back the wrong answers
  for that host.

## Recommendation

Do **option 1** for 1.6.1, with option 3 as a small escape hatch (`sftp: auto | force | off`,
default `auto`).

## Testing

- **Unit (offline):** mapping discovery from fake `pwd` / SFTP listing / `readlink` results;
  longest-prefix translation; unreachable paths produce the specific error; a failed
  verification falls back to SFTP off.
- **Live, on the NAS, read-only plus the scratch folder in claude's home only:**
  - connect reports the mapping;
  - `ssh_file_stat`/`read`/`write` on real paths inside the scratch folder;
  - `ssh_file_transfer` upload and download of a binary file into the scratch folder,
    compared by checksum with `ssh_cmd_run`;
  - line edits;
  - `/tmp/x` gives the specific error;
  - clean up and verify nothing is left.
- `testing_limited/`: flip the Synology entry to `has_sftp=True` (mapped). The existing
  "right answer or clear refusal" checks then cover it, and add a binary round-trip test.
- **Regression checks:** Linux, macOS, FreeBSD and Alpine must still show plain `sftp: true`
  with no mapping, and OpenWrt `sftp: false`.
