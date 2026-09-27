# Windows recursive content search silently skips files with non-ASCII filenames

**Date/harness:** 2026-09-27, OpenCode `openai/gpt-6-sol`, `cygnus_ssh` target `TEST_MCP_SSH_WIN` (Windows Server 2016). **Priority:** P1 correctness: false negative with no warning. Black-box finding; no server source read.

## Minimal repro with exact calls and responses

Create an empty disposable directory `C:\Users\claude\llm_test\unicode-only-20260927` using `ssh_dir_mkdir`. Put a Unicode-named UTF-8 file in it:

```json
ssh_file_copy({"source_path":"C:\\Users\\claude\\llm_test\\search-20260926\\unicode-café.txt","destination_path":"C:\\Users\\claude\\llm_test\\unicode-only-20260927\\unicode-café.txt"})
```

Response: `{"success":true,"copied_to":"C:\\Users\\claude\\llm_test\\unicode-only-20260927\\unicode-café.txt",...}`. `ssh_file_read({"file_path":"C:\\Users\\claude\\llm_test\\search-20260926\\unicode-café.txt"})` had already returned `{"success":true,"content":"café 漢字 🌿\r\n","size":19,...}`. The destination is a copy of that file.

```json
ssh_dir_search_files_content({"dir_path":"C:\\Users\\claude\\llm_test\\unicode-only-20260927","pattern":"café 漢字 🌿"})
```

**Actual:** `{"result":[]}` — no error or warning. Even `pattern:"caf"` returned `{"result":[]}`.

Positive control: copy the *same source bytes* to an ASCII-named file alongside it:

```json
ssh_file_copy({"source_path":"C:\\Users\\claude\\llm_test\\search-20260926\\unicode-café.txt","destination_path":"C:\\Users\\claude\\llm_test\\unicode-only-20260927\\plain.txt"})
ssh_dir_search_files_content({"dir_path":"C:\\Users\\claude\\llm_test\\unicode-only-20260927","pattern":"café 漢字 🌿"})
```

**Actual after the copy:** `[{"file":"C:\\Users\\claude\\llm_test\\unicode-only-20260927\\plain.txt","line":1,"content":"café 漢字 🌿"}]` — only `plain.txt`, not `unicode-café.txt`. `ssh_dir_list_files_basic({"path":"C:\\Users\\claude\\llm_test\\unicode-only-20260927"})` → `["plain.txt","unicode-café.txt"]`; SFTP can enumerate the missing file by its correct name.

## Larger-task observation

On `C:\Users\claude\llm_test\search-20260926` (300 ASCII filler files plus `unicode-café.txt` and logs), `ssh_dir_search_files_content({"dir_path":"C:\\Users\\claude\\llm_test\\search-20260926","pattern":"café 漢字 🌿"})` returned `in_progress` at 50s with handle `1000091`, then `ssh_cmd_check_status({"handle_id":1000091,"wait_seconds":40})` completed after ~126s with `result:[]`. It was slow **and** incorrect. An ASCII marker in `file-217.txt` on the same tree was found correctly at line 2 in a separate ~129s call.

## Expected and suggested fix

Recursive content search must include files with Unicode filenames and return the same line/content as the ASCII-name copy; if any file cannot be enumerated or decoded, report a partial-search error instead of silently returning `[]`. The tool description explicitly promises Unicode-safe Windows matching via SFTP. Inspect only the path-enumeration/encoding boundary as a hypothesis; this result was obtained without reading implementation code.

**Related enumeration clue:** After the two directories were removed, `ssh_dir_delete({"path":"C:\\Users\\claude\\llm_test","dry_run":false})` returned `status:"success"`, but its `deleted_items` list rendered both Unicode names as `unicode-caf�.txt`, whereas `ssh_dir_list_files_basic` (SFTP) had returned `unicode-café.txt`. This supports, but does not prove, a Windows command-output filename encoding boundary; the erroneous search was observed independently of deletion. `ssh_file_stat({"path":"C:\\Users\\claude\\llm_test"})` → `exists:false`. Both scratch subdirectories and the parent were removed and verified absent.

## Fix (2026-09-27, branch `fix/round4-search-and-connection`)

- **Root cause:** the Windows search listed candidate files via PowerShell
  (`Get-ChildItem ... | ForEach-Object { $_.FullName }`) and read that list from PowerShell's
  stdout, which uses the OEM console code page. `unicode-café.txt` arrived as a garbled name,
  reading the garbled path over SFTP failed, and a bare `except: continue` skipped the file
  **silently**. Confirmed with the tester's exact control on the old code: only `plain.txt`
  was found.
- **Fix:** the whole Windows search now runs over **one SFTP session**: directory listings
  (`listdir_attr`, which returns proper UTF-8 names), raw file reads, local matching
  (UTF-8, BOM tolerated). Nothing is skipped silently any more: unreadable folders/files and
  files over 10 MB are collected, and `ssh_dir_search_files_content` then returns
  `{"status": "incomplete", "matches": [...], "skipped": [...], "skipped_count", "note"}`
  instead of an unqualified list. Linux/macOS do the same for entries grep couldn't read
  (e.g. systemd's private folders under `/tmp`, which previously were only logged).
- **Tests:** `testing_mcp/test_tool__search_and_errors.py`:
  `test_search_finds_files_with_non_ascii_names` (both files found for the full Unicode
  pattern and for `caf`, name intact; all platforms; fails on the old Windows code) and
  `test_search_reports_unreadable_files_as_incomplete` (Linux/macOS, `chmod 000` file).
