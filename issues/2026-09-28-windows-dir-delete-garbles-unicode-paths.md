# Windows recursive delete preview/result garbles Unicode filenames

**Date/harness:** 2026-09-28, OpenCode `openai/gpt-6-sol`, `TEST_MCP_SSH_WIN` on `feature/operation-progress` at `58d8776`. **Priority:** P2 truthful preview/audit output. Black-box observation; no server source read. This is *separate* from the fixed Windows Unicode **content search** and SFTP file read/write behavior.

Inside an initially absent scratch directory `C:\Users\claude\llm_test`, `ssh_file_write({"file_path":"C:\\Users\\claude\\llm_test\\unicode-café.txt","content":"mode=baseline\r\nmarker=café 漢字 🌿\r\n"})` succeeded. `ssh_dir_list_files_basic({"path":"C:\\Users\\claude\\llm_test"})` → `["unicode-café.txt"]`; `ssh_file_stat` and `ssh_file_read` using that exact filename worked. The same file was downloaded successfully with `ssh_file_transfer` and its Unicode content read correctly locally.

```json
ssh_dir_delete({"path":"C:\\Users\\claude\\llm_test"})
```

**Actual preview:** `{"status":"success","dry_run":true,"deleted_items":["C:\\Users\\claude\\llm_test\\unicode-caf�.txt","C:\\Users\\claude\\llm_test"],...}`. Repeating with `dry_run:false` returned the same garbled `deleted_items`; `ssh_file_stat` afterward confirmed the directory was actually removed. The deletion succeeded, but its preflight/audit path no longer identifies the original filename correctly. A model comparing the preview to expected paths could not rely on it for Unicode names.

**Expected:** the dry-run and real results should return `unicode-café.txt` exactly, like SFTP listing/stat and Windows content search. Preserve Unicode through command-output enumeration, or use a Unicode-safe listing when forming `deleted_items`. The test scratch and local downloaded file were removed and absence verified.

## Fix (2026-09-28, branch `feature/operation-progress`)

- **Root cause (not specific to delete):** the server's own PowerShell scripts wrote their output
  in the console's OEM code page. Any non-ASCII character in a path they printed was lost:
  `é` became `�`, and CJK and emoji became `??`. This affected every Windows tool that builds a
  path list with PowerShell:
  - `ssh_dir_delete`
  - `ssh_dir_batch_delete_files`
  - `ssh_dir_search_glob`
  - `ssh_dir_list_advanced`
  - the symlink listing
  - `ssh_archive_extract`'s file list
- **Fix:** `powershell_encoded_command()` now switches each script's stdout to UTF-8 (no BOM).
  `ssh_cmd_run`'s own wrapper is exempt, because it relays the user's program's output, whose
  encoding it doesn't control. The documented `Get-Content` caveat for `ssh_cmd_run` still
  applies.
- **Verified live on Server 2016** with `unicode-café 漢字 🌿.txt`: delete (dry run and real),
  batch delete, glob search and advanced listing all return the exact name, and `ssh_cmd_run` is
  unchanged.
- **Test:** `testing_mcp/test_tool__windows_unicode_paths.py` (Windows); fails on the old code.

## Related fix: line edits no longer convert CRLF to LF

The retest also noted that `ssh_file_replace_line` turned a CRLF file into LF (41 → 39 bytes).
This happened on every platform, not just Windows:
- **Cause:** replace, insert and delete edited LF-normalized text and wrote it back as LF.
- **Fix:** an edited file now keeps its own line endings.
- **Test:** `testing_mcp/test_tool__file_line_endings.py` (all platforms, checked by exact byte
  size); fails on the old code.
