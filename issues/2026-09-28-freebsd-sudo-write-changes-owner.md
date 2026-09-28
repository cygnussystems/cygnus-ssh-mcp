# FreeBSD `ssh_file_write(use_sudo=true)` changes root-owned mode-0600 file to the SSH user

**Date/harness:** 2026-09-28, OpenCode `openai/gpt-6-sol`, `freebsd-test` (FreeBSD 15.1, os_type `flex`). **Priority:** P1 file ownership/access semantics. Black-box observation; no server source read.

## Exact repro

Inside tester scratch `/tmp/llm_test`, `ssh_cmd_run(use_sudo:true)` created `/tmp/llm_test/root.conf` with `private=before\n` owned by `root:wheel` and mode 0600. `ssh_file_stat({"path":"/tmp/llm_test/root.conf"})` → `uid:0, gid:0, mode:"0o100600"`; the connected `claude` account could not SFTP-read it. Sudo was verified working/passwordless.

```json
ssh_file_write({"file_path":"/tmp/llm_test/root.conf","content":"private=after\n","use_sudo":true,"mode":384})
```

Tool result: `{"success":true,"file_path":"/tmp/llm_test/root.conf","bytes_written":14,"mode":"600","append":false,...}`. Privileged `ssh_cmd_run({"command":"cat /tmp/llm_test/root.conf; ls -ln /tmp/llm_test/root.conf","use_sudo":true,"wait_timeout":10})` →

```text
private=after
-rw-------  1 1002 0 14 ... /tmp/llm_test/root.conf
```

Owner changed from root uid0 to `claude` uid1002 even though the requested mode remained 0600. `ssh_file_read({"file_path":"/tmp/llm_test/root.conf"})` **without sudo** now returned `success:true, content:"private=after\n"`. For a real root-only config this is a privilege boundary change, not merely formatting metadata. The connection's capability warnings mention GNU `stat -c` missing and possible *permission* restoration uncertainty, but they do not warn that a sudo file write can give ownership to the SSH user.

## Expected/fix direction

On sudo overwrite, preserve prior owner/group/mode unless explicitly changed, or reject the operation before changing content when this host cannot safely preserve them. Return explicit owner/permission change metadata if requested; never silently make a private root-owned file readable by the unprivileged account. The tester restored root:wheel/0600 via a privileged command and removed the entire scratch tree with sudo; absence verified.
