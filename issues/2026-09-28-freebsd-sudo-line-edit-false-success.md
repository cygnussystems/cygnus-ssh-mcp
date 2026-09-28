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
