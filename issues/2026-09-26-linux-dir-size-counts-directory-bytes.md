# Linux directory size/copy bytes include directory metadata despite file-size contract

**Status:** new black-box consumer finding (2026-09-26, OpenCode). **Priority:** P2 for truthful metadata; not a timeout blocker. **Target:** `TEST_MCP_SSH_LINUX` Debian 12. No server source read.

## Exact repro

Create a scratch tree with **2,000 regular files of 524,288 bytes each** and one 23-byte UTF-8 text file. The local 2,001-file manifest totals exactly `1048576023` bytes. Upload it to `/tmp/llm_test/session2-20260926/uploaded` using `ssh_dir_transfer`. Verify the same regular-file total on the remote host:

```json
ssh_cmd_run({"command":"find /tmp/llm_test/session2-20260926/uploaded -type f -printf '%s\\n' | awk '{sum += $1} END {printf \"%.0f\\n\", sum}'","wait_timeout":15})
```

Output: `1048576023\n`.

```json
ssh_dir_calc_size({"path":"/tmp/llm_test/session2-20260926/uploaded"})
```

Actual response: `{"path":"/tmp/llm_test/session2-20260926/uploaded","size_bytes":1048645655,"size_human":"1000.07 MB"}`. **Difference: 69,632 bytes.** `du -sb /tmp/llm_test/session2-20260926/uploaded` returned the same inflated value, consistent with counting directory metadata as well as regular files. The tool description says `sum of all file sizes under it`, so the returned `size_bytes` does not match that promise.

Likewise `ssh_dir_copy({"source_path":"/tmp/llm_test/session2-20260926/uploaded","destination_path":"/tmp/llm_test/session2-20260926/copied"})` returned `{"status":"success","files_copied":2001,"bytes_copied":1048645655,"destination_path":"/tmp/llm_test/session2-20260926/copied",...}` (connection object omitted here). The copied tree had 2001 regular files with matching sample hashes; `bytes_copied` again reports the inflated `du -sb` value.

**Control:** `ssh_dir_calc_size` on the same uploaded tree on macOS returned exactly `1048576023`. No data was lost; Linux result semantics differ.

## Expected / suggested resolution

Either report the sum of regular-file sizes (matching the documented contract and macOS) or explicitly document that Linux includes directory entries and name the returned metric accordingly. Keep `ssh_dir_calc_size` and `ssh_dir_copy.bytes_copied` consistent. The scratch trees were removed and their parent path verified absent after the test.
