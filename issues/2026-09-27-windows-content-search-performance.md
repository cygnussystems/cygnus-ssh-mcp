# Windows recursive content search takes ~2 minutes for 300 tiny files

**Date/harness:** 2026-09-26/27, OpenCode `openai/gpt-6-sol`, default MCP settings, target `TEST_MCP_SSH_WIN`. **Priority:** P2 performance/usability, separate from the Unicode-filename correctness issue. No source read. Long-operation handoff itself **works**: all slow calls returned `in_progress` at 50s and their terminal results were retrievable without `-32001`.

## Controlled workload and measurements

`C:\Users\claude\llm_test\search-20260926` contained 300 small `file-NNN.txt` text files, with one ASCII marker on line 2 of `file-217.txt`, one Unicode-named UTF-8 file and two tiny task logs. Representative files contained only `plain filler`; this is not a huge-directory or huge-byte test.

| Exact call | Handoff / terminal result | Server-recorded elapsed |
|---|---|---|
| `ssh_dir_search_files_content({"dir_path":"C:\\Users\\claude\\llm_test\\search-20260926","pattern":"unique-search-needle-20260926"})` | `in_progress, handle_id:1000085`; completed with one correct match at `file-217.txt:2` | start `2026-09-26T21:11:42.158421`, end `21:13:51.732038` = **129.6s** |
| Same tool/path, `pattern:"SESSION3-ABSENT-NEEDLE-20260926"` | `in_progress, handle_id:1000090`; completed with `result:[]` | start `2026-09-27T07:22:28.750584`, end `07:24:37.921613` = **129.2s** |
| Same tool/path, `pattern:"café 漢字 🌿"` | `in_progress, handle_id:1000091`; completed with `result:[]` (incorrectly skipped Unicode filename; see separate correctness issue) | start `2026-09-27T07:24:47.031439`, end `07:26:53.004204` = **126.0s** |

Average approximately 0.43 seconds **per tiny file** on this host. A model can poll and recover the result; it cannot reasonably infer that a 300-file search should take over two minutes from the name/description, and status has no scanned-file progress. The Unicode false negative is detailed in `issues/2026-09-27-windows-content-search-skips-unicode-filenames.md` and must be fixed independently.

## Suggested investigation / acceptance

Measure per-file SFTP open/read overhead versus a batched traversal on Windows while preserving Unicode file *paths and contents*. Do not improve speed by silently ignoring unreadable/non-ASCII files. Benchmark on the exact ~302-file scratch tree before/after; a result in seconds rather than minutes is a reasonable usability target, but do not declare a strict SLA from one VM. Keep `in_progress`/status handoff for genuinely large trees. All scratch files from this test were removed and absence verified.

## Fix (2026-09-27, branch `fix/round4-search-and-connection`)

- **Root cause:** the search read each file with `read_file`, which opened a **new SFTP
  session per file**. Measured on `win-server-2016`: opening a session costs ~0.37s, while
  20 reads on one existing session take 0.06s in total, so the session setup was the whole
  ~0.43s per file.
- **Fix:** the Windows search walks and reads everything on one SFTP session (same change as
  the Unicode-filename fix).
- **Test:** `test_windows_search_of_300_files_is_fast`: 300 files, all 300 matches returned
  directly (no handoff) in under 30s. It fails on the old code (the search overran the 50s cap).
