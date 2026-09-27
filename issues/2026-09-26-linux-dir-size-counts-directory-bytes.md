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

## Fix (2026-09-27, branch `fix/round4-search-and-connection`)

- **Root cause:** Linux computed directory size with `du -sb`, which counts each directory's
  own size (4,096 bytes per directory; 17 directories = the 69,632 extra bytes).
  `ssh_dir_copy`'s `bytes_copied` uses the same helper.
- **Fix:** Linux now sums regular-file sizes (`find <path> -type f -printf '%s\n' | awk`),
  like macOS and Windows and as the tool description says. The capability guard for this
  method is now `find_printf` instead of `du_sb`.
- **Bigger bug found while testing this:** on **Linux and macOS**, `ssh_dir_copy` ran
  `cd src && find . -type f -o -type d | xargs -I{} cp -a {} dest/`, which copied every
  subdirectory *and* every file inside it into the destination root: nested files were
  **duplicated, flattened, at the top level** (e.g. `copy/c.txt` next to `copy/d1/c.txt`),
  inflating `files_copied`/`bytes_copied` and leaving a wrong tree. The `preserve_symlinks=False`
  branch used `src/*`, which skipped hidden files. Both are replaced by one
  `cp -R -P|-L [-p] 'src/.' 'dest/'` (GNU, BSD and BusyBox compatible): exact tree, hidden
  files included, symlinks kept as symlinks by default.
- **Tests:** `testing_mcp/test_tool__dir_size.py`: a 12-file tree in 8 folders, including hidden
  files. `ssh_dir_calc_size` must equal the exact file-size sum, and `ssh_dir_copy` must produce
  exactly the same file list with the exact `files_copied`/`bytes_copied` (all platforms;
  fails on the old Linux code: 45,135 vs 12,367 bytes). `test_copy_keeps_symlinks_as_symlinks`
  (Linux/macOS). Existing sudo copy tests pass.
