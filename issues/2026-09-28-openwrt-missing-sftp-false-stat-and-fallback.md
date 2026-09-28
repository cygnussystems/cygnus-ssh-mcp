# OpenWrt has no SFTP but file tools report misleading absence and fallbacks

**Date/harness:** 2026-09-28, OpenCode `openai/gpt-6-sol`, `openwrt-test` (OpenWrt 25.12.5, Dropbear, root SSH). **Priority:** P1 truthful file existence/tool discoverability. Black-box result; no server source read.

## Exact repro

`ssh_conn_connect({"host_name":"openwrt-test"})` succeeds and capability warnings enumerate missing GNU features, but do **not** mention SFTP unavailability. `ssh_file_stat({"path":"/tmp/llm_test"})` before creation returned `{"exists":false,"path":"/tmp/llm_test","error":"Unexpected error: EOF during negotiation"}`. `ssh_dir_mkdir({"path":"/tmp/llm_test"})` failed with `Error calling tool 'ssh_dir_mkdir': EOF during negotiation`.

After `ssh_cmd_run({"command":"if [ -e /tmp/llm_test ]; then echo EXISTS; else echo ABSENT; fi"})` independently confirmed the scratch path was absent, a short shell command created `/tmp/llm_test/openwrt/config.txt` containing `mode=baseline\nmarker=cafe\n` (26 bytes). **Now on a known-existing path:**

- `ssh_file_stat({"path":"/tmp/llm_test/openwrt/config.txt"})` → `{"exists":false,"path":"/tmp/llm_test/openwrt/config.txt","error":"Unexpected error: EOF during negotiation"}`. This is a false absence; the file was later found and copied by shell-backed tools.
- `ssh_file_read({"file_path":"/tmp/llm_test/openwrt/config.txt"})` → `{"success":false,"file_path":"/tmp/llm_test/openwrt/config.txt","error":"SFTP read failed: EOF during negotiation"}`.
- `ssh_file_write({"file_path":"/tmp/llm_test/openwrt/new.txt","content":"should-not-exist\n"})` → `{"success":false,"file_path":"/tmp/llm_test/openwrt/new.txt","error":"EOF during negotiation"}`.
- `ssh_dir_list_files_basic({"path":"/tmp/llm_test/openwrt"})` → MCP error `EOF during negotiation`.
- `ssh_dir_search_glob({"path":"/tmp/llm_test/openwrt","pattern":"*.txt"})` and `ssh_dir_calc_size({"path":"/tmp/llm_test/openwrt"})` correctly rejected unsupported `find -printf`, **but both suggested** `ssh_dir_list_files_basic` + `ssh_file_stat` as an SFTP fallback “unaffected” by the missing feature. That fallback is unusable on this host.

`ssh_dir_search_files_content` (shell-backed) correctly found `marker=cafe` at line 2; `ssh_dir_delete` and batch-delete shell paths worked. `ssh_task_launch` returned a PID/log path and task exited, but `ssh_file_read` of the promised log also failed EOF; a shell `cat` was required to see output. No files were left on the target; the exact scratch directory was deleted and absence verified by command.

## Expected/fix direction

Probe SFTP negotiation as a **separate host capability** at connect time, or fail the connection clearly for SFTP-dependent tools. Do not use `exists:false` for a transport/subsystem failure; return a distinct `sftp_unavailable`/`status:error` without a false existence field and an actionable explanation. Capability-gate tools whose only implementation requires SFTP, and recommend only fallbacks that actually work on this target. If POSIX-command fallback is intentionally supported, document its limitations (encoding and permissions) rather than forcing the model to discover it by trial and error. A recent command-only success must not be treated as proof SFTP works.
