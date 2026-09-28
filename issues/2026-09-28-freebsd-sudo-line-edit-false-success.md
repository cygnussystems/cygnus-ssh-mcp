# FreeBSD privileged `ssh_file_replace_line(force=true)` reports success without changing root-only file

**Date/harness:** 2026-09-28, OpenCode `openai/gpt-6-sol`, `freebsd-test` (FreeBSD 15.1, os_type `flex`). **Priority:** P1 config correctness. Black-box observation. No server source read.

## Exact repro

`ssh_conn_verify_sudo({})` → `{"available":true,"passwordless":true,"requires_password":false}`. Under tester-owned `/tmp/llm_test`, create a root-owned, mode-0600 test config with content `private=before\n` using a short `ssh_cmd_run(use_sudo:true)`; `ssh_file_stat({"path":"/tmp/llm_test/root.conf"})` → `type:"file", mode:"0o100600", uid:0, gid:0, size:15`.

```json
ssh_file_replace_line({"file_path":"/tmp/llm_test/root.conf","match_line":"private=before","new_line":"private=after","use_sudo":true})
```

Response: `{"success":false,"error":"Cannot read file to check for duplicate lines: [Errno 13] Permission denied",...}`. The tool schema describes `force` as “Force operation even if file can't be read (sudo only)”, so the model followed that guidance:

```json
ssh_file_replace_line({"file_path":"/tmp/llm_test/root.conf","match_line":"private=before","new_line":"private=after","use_sudo":true,"force":true})
```

**Actual:** `{"success":true,"message":"No changes needed (match line not found or content identical).",...}`. Privileged `ssh_cmd_run({"command":"cat /tmp/llm_test/root.conf; ls -ln /tmp/llm_test/root.conf","use_sudo":true,"wait_timeout":10})` → **`private=before`** and `-rw------- 1 0 0 ... root.conf`. The exact match exists and the replacement differs; no edit occurred. A user relying on `success:true` would incorrectly think the configuration was changed.

## Expected/fix direction

Use a privileged read/unique-match check on unreadable files before the write, or return an explicit error if the current host cannot safely perform it. Never return “success/no change” solely because the pre-edit check could not access the content. Preserve permission/ownership metadata and verify the resulting bytes. This is a disposable root-only fixture; it was subsequently removed with sudo and absence verified.

## Fix (2026-09-28, branch `feature/operation-progress`)

- **Root cause (not FreeBSD-specific: every Linux/macOS host):** `ssh_file_replace_line`,
  `ssh_file_insert_lines_after_match` and `ssh_file_delete_line_by_content` (and
  `ssh_file_replace_line_multi`) read the file over SFTP **as the connected user**, even with
  `use_sudo`. For a root-only file that read fails; with `force=True` the shared sudo writer then
  treated the original content as `""`, the edit found nothing to change, and the tool returned
  `success: true, "No changes needed"`.
- **Fix:** one shared read helper for all three: read over SFTP as the user, and if that's denied
  and `use_sudo` is set, read with `sudo cat`. If the content can't be read either way, the edit
  fails with a clear error ("... Nothing was changed."); the "assume empty content" path is gone,
  so it can never report success without having seen the file. `force` is kept for compatibility
  but is no longer needed; its description says so.
- **Verified live** on FreeBSD, Debian and macOS (root:0 file, mode 600): replace (with and
  without `force`), insert and delete really change the file and keep owner `0:0` / mode 600; a
  missing match returns `success: false, "Match line not found"`.
- **Test:** `testing_mcp/test_tool__sudo_file_edits.py::test_sudo_line_edits_on_root_only_file`
  (Linux/macOS); fails on the old code.
