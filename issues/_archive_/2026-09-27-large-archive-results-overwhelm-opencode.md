# Large archive-extract results exceed OpenCode inline tool output

**Harness/date:** OpenCode `openai/gpt-6-sol`, default settings, 2026-09-26. **Priority:** P3 payload ergonomics; not a timeout or extraction-corruption failure. Black-box observation, no server source read.

## Repro and result

`ssh_archive_extract` on a 2,001-file Linux/macOS tar.gz succeeded, returning `status:"success"` and `extracted_files` with 2,002 strings (top-level directory plus regular files). The response was approximately **52 KB** and OpenCode did not show it inline: it saved the JSON to a local `tool-output` path and told the model where to read it. A Windows 18,001-file ZIP result in the accepted W3 retest was approximately **306 KB**, likewise saved externally. The saved JSON was one long line; the dedicated local `read` tool truncated that line at 2,000 characters. The model had to parse the saved artifact locally to see `success`, the actual destination and entry count, then verify file count on the remote host. The underlying archive operations and extracted contents were correct.

## Why it matters / suggested contract

The model should see a concise terminal summary **without** having to parse a many-hundred-KB tool artifact: operation status, actual destination/format, extracted file and directory counts, conflicts/skipped/error counts, and a deterministic way to page names when needed. Keep a full manifest accessible separately for detailed audits. This can be addressed at the MCP response shape level and should preserve the current successful `in_progress`/poll lifecycle. Exact archive calls and timings are in `findings/2026-09-26-opencode-session2-directory-ops.md` and the archived round-3 retest.

## Fix (2026-09-27, branch `fix/round4-search-and-connection`)

- **Root cause:** `ssh_archive_extract` returned every extracted name inline. On Linux/macOS
  that was tar's raw listing (the archive's top folder plus every folder and file entry, with the
  top folder in each path); on Windows every file path. 2,001 files gave ~52 KB of JSON and
  18,001 files ~306 KB, too much for OpenCode to show inline.
- **Fix:** the tool now returns a concise summary on every platform: `files_extracted`,
  `directories`, `destination_path`, `extracted_files` = the **first 50 file paths, relative to
  the destination** (Linux/macOS paths no longer include the stripped top folder, matching
  Windows), `extracted_files_truncated`, and a `note` when there are more, naming
  `ssh_dir_search_glob(path=<destination>, pattern='*')` to list them. The files are on disk, so
  that's the paged/retrievable manifest. When `overwrite=False` kept existing files (tar
  `--keep-old-files`), the note says so (previously only logged).
  `in_progress`/terminal-result behavior is unchanged.
- **Tests:** `testing_mcp/test_tool__archive_extract_summary.py`: 120 files in 2 folders gives
  exact counts, a 50-path relative sample, the truncation flag and note, and a response under
  8 KB; 4 files gives the complete list with no truncation. Pass on Linux, macOS and Windows;
  fail on the old code.
