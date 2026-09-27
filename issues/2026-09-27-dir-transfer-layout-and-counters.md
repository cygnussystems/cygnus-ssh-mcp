# Directory transfer root layout and counters are surprising despite correct data

**Harness/date:** OpenCode `openai/gpt-6-sol`, 2026-09-26, Linux `TEST_MCP_SSH_LINUX`; macOS upload had the same 2002 count. **Priority:** P3 usability/response clarity. Upload/download completed safely (no client timeout), and a full local manifest verified all 2,001 regular files intact. No server source read.

## Exact repro and observed responses

The source `C:\Users\ritte\AppData\Local\Temp\opencode\session2-20260926-source` contained 2,000 × 524,288-byte random files plus one 23-byte Unicode text file: **2,001 regular files / 1,048,576,023 content bytes**. Its SHA-256 manifest was recorded before transfer.

```json
ssh_dir_transfer({"direction":"upload","local_path":"C:\\Users\\ritte\\AppData\\Local\\Temp\\opencode\\session2-20260926-source","remote_path":"/tmp/llm_test/session2-20260926/uploaded"})
```

Response after `in_progress`/poll: `{"success":true,"operation":"upload","local_path":"C:\\Users\\ritte\\AppData\\Local\\Temp\\opencode\\session2-20260926-source","remote_path":"/tmp/llm_test/session2-20260926/uploaded","archive_format":"tar.gz","files_transferred":2002,"bytes_transferred":1049323272,...}`. The remote destination contained the **files directly** under `uploaded/`; `find ... -type f | wc -l` returned `2001`.

```json
ssh_dir_transfer({"direction":"download","local_path":"C:\\Users\\ritte\\AppData\\Local\\Temp\\opencode\\session2-20260926-downloaded","remote_path":"/tmp/llm_test/session2-20260926/uploaded"})
```

Response after `in_progress`/poll: `{"success":true,"operation":"download","files_transferred":2002,"bytes_transferred":1049033842,"archive_format":"tar.gz",...}`. The local destination was **not** populated with files directly: it contained one nested `uploaded/` directory. A manifest comparison of `downloaded\uploaded` reported `expected 2001, actual 2001, mismatched 0, missing 0, bytes 1048576023`.

**What was surprising:** `files_transferred:2002` apparently includes a directory entry even though only 2001 regular files existed. `bytes_transferred` is different in each direction and from the actual file-content total; it appears to measure archive/staging bytes, but the return shape does not label that unit. A user looking for `downloaded\file-00000.bin` would not find it, although transfer succeeded.

## Suggested resolution

Document download's top-level-directory behavior and whether it is guaranteed or filename-dependent. Clarify whether `files_transferred` counts directory entries and whether `bytes_transferred` is the compressed archive size, wire bytes, or payload. Prefer explicit `regular_files`, `directories`, `archive_bytes`, `payload_bytes` where those are available rather than a misleading single count. This is not a corruption/timeout bug: all payload files and sampled hashes matched and all source/downloaded/remote fixtures were cleaned and verified absent.

## Fix (2026-09-27, branch `fix/round4-search-and-connection`)

- **Root cause, layout:** both directions build an archive that contains the folder itself.
  The *remote* extraction strips that top level (`tar --strip-components=1`, and the same
  emulated on Windows), so an upload places the contents directly in `remote_path`. The
  *local* extraction for downloads doesn't, so the files land in
  `local_path/<remote folder name>/`.
- **Root cause, counters:** `files_transferred` counted archive *entries* (files **and** folders,
  hence 2002 for 2001 files), and `bytes_transferred` was the compressed archive size, which
  differs per direction and from the file total. Neither was labelled.
- **Fix (non-breaking):**
  - Every result now has **`files_location`**, the exact folder where the transferred files
    are (`remote_path` for upload, `local_path/<name>` for download), and the tool description
    and docs state the layout of each direction explicitly.
  - `files_transferred` = **regular files only**; new `directories` (subfolders, not counting the
    top folder), `payload_bytes` (sum of the files' sizes) and `archive_bytes` (compressed size
    sent). `bytes_transferred` is kept, equal to `archive_bytes`, and documented as that.
- **Design choice to confirm:** I kept the download layout (`local_path/<name>/`) rather than
  making downloads place the contents directly in `local_path` like uploads. The existing test
  `test_ssh_dir_transfer_download` asserts the nested layout, so it's current, tested behavior,
  and changing it could break existing users. If you'd prefer symmetric behavior, it's a small
  change in `extract_local_archive` (strip the top-level folder), but a behavior change worth a
  release note.
- **Also fixed:** `ssh_dir_transfer`'s description had its layout/result text after an `Args:`
  section, which FastMCP drops from published tool descriptions; the text is now placed so models
  see it.
- **Test:** `test_tool__dir_transfer.py::test_dir_transfer_layout_and_counters`: a 4-file tree in
  2 subfolders (incl. a hidden file) is uploaded and downloaded, checking `files_location`, the
  actual placement and exact counters. Passes on Linux, macOS and Windows; fails on the old code.
