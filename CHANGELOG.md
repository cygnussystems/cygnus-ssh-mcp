# Changelog

## 1.6.1 - 2026-09-29

Two fixes found by running the test suite from every client platform (Windows, macOS, Linux)
against every target, rather than from a Windows client only.

### Fixed

- **A result is no longer lost when the client gives up on a request.** If a tool finished
  after the client had already timed out on the request (but within the server's wait
  cap), the result went nowhere, and `ssh_cmd_check_status` could no longer find the
  operation. Finished operations now stay collectable (the last 50). The ones that were
  returned normally don't clutter `ssh_cmd_history`.
- **Windows targets when the server runs on macOS or Linux.** Remote paths were split by
  the local machine's rules, which don't know `\` as a separator. As a result,
  `ssh_file_write` with `create_dirs` failed ("SFTP put failed: No such file"). Remote paths
  are now always handled by the target's rules. This covers parent directories, temporary
  file names, `append_timestamp` copies and archive sources.

### Testing

- The cross-platform test matrix (`testing_matrix/`) works again with 1.6.0's long-operation
  hand-off, and all three runner machines are verified.

## 1.6.0 - 2026-09-28

The theme of this release: any MCP client, with any model, should get correct answers
without having to work around the server. Long operations no longer freeze the server or
lose their result. Output is never silently cut off. Every tool either gives the right
answer or a clear error that says what to do instead, including on routers, NAS boxes and
BusyBox systems.

### New

- **Long-running tools hand off instead of freezing.** Every tool now runs in its own worker
  thread. A call that outlasts the per-call wait cap (default 50 s, `--max-wait` /
  `MCP_SSH_MAX_WAIT`) returns `in_progress` with a handle; poll it with
  `ssh_cmd_check_status`. Status tools keep answering in the meantime. Before, an
  archive, transfer or search that took longer than the client's ~60 s limit lost its
  result and blocked every other call.
- **Progress reporting** for long operations: transfers, archives, searches and copies.
- **Large output is kept and pageable.** Up to 2 MB per stream is retained (`--max-output` /
  `MCP_SSH_MAX_OUTPUT`) and the last ~32 KB is returned inline, with
  `output_truncated`/`stderr_truncated` flags. `ssh_cmd_output(start_line=...)` pages the
  rest. Output used to be silently cut to 100 lines.
- **Dead connections are detected** (SSH keepalive). The error names the host and says how to
  reconnect, instead of a generic failure.
- **`capabilities.sftp`.** Hosts without usable SFTP are detected at connect: either no SFTP
  subsystem (OpenWrt/Dropbear), or an SFTP that shows a different filesystem than the shell
  (Synology DSM's share-only SFTP). SFTP-based file tools then return one clear "use
  `ssh_cmd_run` instead" error rather than wrong answers.
- **`handle_id` in `ssh_cmd_run` and `ssh_cmd_history` results.** This is the name
  `ssh_cmd_check_status`, `ssh_cmd_output` and `ssh_cmd_kill` take. `id` is still returned.

### Changed

These are small changes to result shapes. No tool or parameter was removed or renamed.

- `ssh_archive_extract` returns `files_extracted`, `directories` and the first 50 file paths
  (relative to the destination), plus `extracted_files_truncated`, instead of every file
  name. An 18,000-file archive used to produce ~300 KB of JSON.
- Long operations can return `status: in_progress` (see above) where they used to block.
- Directory size (`ssh_dir_calc_size`, `ssh_dir_copy`'s `bytes_copied`) now counts regular
  files only on every platform. On Linux it also counted each directory's own size.
- `ssh_file_stat` returns `exists: null` (with the reason) when it couldn't check a path.
  `exists: false` now always means "not found".
- The `force` parameter of the line-edit tools is no longer needed and is kept only for
  compatibility.

### Fixed

**Commands and tasks**
- `ssh_cmd_run` no longer blocks the server while it waits, and never loses its handle.
- `ssh_cmd_check_status` counted at most 50 output lines.
- Sudo tasks with logs in root-owned directories silently never started, but a PID was
  still returned.
- Windows task logs lost or misrouted output for compound (`&`) commands.
- Windows commands that wrote output quickly could lose part of it.
- A failed task launch left its launcher script, which can contain the sudo password, in
  `/tmp`.
- `ssh_cmd_clear_history` now also clears finished long operations.

**Files**
- Sudo line edits on root-only files could report success without changing anything. They
  now read the file with sudo, or fail with "Nothing was changed".
- Sudo writes changed an existing file's owner to the connected user. They now keep its
  owner, group and mode.
- Line edits turned CRLF files into LF. They now keep the file's own line endings.

**Directories and archives**
- Linux and macOS `ssh_dir_copy` flattened nested files.
- `ssh_dir_transfer` now reports where files land and exact counters.
- `ssh_dir_search_files_content` failed on Linux when nothing matched.
- On Windows, content search skipped files with non-ASCII names and was slow.
- Windows path lists (delete previews, batch delete, glob search, advanced listing,
  archive extraction) garbled non-ASCII file names. The server's PowerShell scripts now
  write UTF-8.
- `ssh_archive_create` reported an error after creating the archive on hosts without
  `stat`. The size query is now best-effort.

**Platforms**
- BusyBox hosts (Alpine, OpenWrt):
  - directory size and copy now report exact bytes;
  - `ssh_dir_search_glob` now works (portable fallback for `find -printf`).
- OpenWrt: correct user, hostname and memory (was KiB labelled as MB).
- FreeBSD: `system.os_type` is `freebsd` (was `macos`), with real memory and OS
  name/version.
- Synology DSM: reports `Synology DSM` and its version, and estimates available memory on
  its older kernel.
- Windows version detection when PowerShell is the SSH default shell.
- Clean `load_avg` on macOS.
- Longer OS probes at connect, for slow hosts.

### Testing

- New `testing_limited/` suite for Alpine, FreeBSD, OpenWrt and a Synology NAS. It walks an
  everyday administration workflow and checks every tool's answer against the shell.
- Each fix above has a regression test that fails on the previous code.
