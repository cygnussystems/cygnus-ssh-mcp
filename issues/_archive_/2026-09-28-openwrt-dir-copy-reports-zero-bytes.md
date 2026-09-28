# OpenWrt `ssh_dir_copy` reports zero bytes for a successful nonempty copy

**Date/harness:** 2026-09-28, OpenCode `openai/gpt-6-sol`, `openwrt-test` (OpenWrt 25.12.5/BusyBox). **Priority:** P2 result correctness. Black-box result; no server source read.

Source `/tmp/llm_test/openwrt/config.txt` contained 26 bytes (verified by `wc -c`).

```json
ssh_dir_copy({"source_path":"/tmp/llm_test/openwrt","destination_path":"/tmp/llm_test/openwrt-copy"})
```

**Actual:** `{"status":"success","files_copied":1,"bytes_copied":0,"destination_path":"/tmp/llm_test/openwrt-copy",...}`. Independent `ssh_cmd_run({"command":"if [ -f /tmp/llm_test/openwrt-copy/config.txt ]; then echo COPY_PRESENT; wc -c /tmp/llm_test/openwrt-copy/config.txt; else echo COPY_MISSING; fi; du -sb /tmp/llm_test/openwrt-copy","wait_timeout":5})` → `COPY_PRESENT\n26 /tmp/llm_test/openwrt-copy/config.txt\n86\t/tmp/llm_test/openwrt-copy\n`. The copied data exists; `bytes_copied:0` is not a valid file-byte total. This host's capability probe claimed `du_sb:true` (though it lacks GNU `find -printf` and `stat`), so the tool did not reject the request. No user data was affected; scratch removed and absence verified.

**Expected/fix direction:** compute regular-file byte total using portable supported primitives or report an explicit unknown/null size with a reason. Do not return a numeric zero when files were copied. Keep copied-file count and byte total aligned with actual destination data.

## Fix (2026-09-28, branch `feature/operation-progress`)

- **Root cause (a regression from the round-4 issue-4 fix, `d327949`):** Linux directory size
  was switched from `du -sb` to `find -type f -printf '%s'`. BusyBox `find` (OpenWrt, Alpine) has
  no `-printf`; the pipeline's `awk` still printed `0` and exited 0, so `ssh_dir_copy` reported
  `bytes_copied: 0` silently. The same change also made `ssh_dir_calc_size`'s capability guard
  require `find_printf`, so on Alpine it refused to run (it had worked with `du -sb`, just with
  directory overhead).
- **Fix:** the size command is chosen by capability: `find -printf` where available, otherwise
  `find <path> -type f -exec ls -ln {} +` summing the size column (POSIX, BusyBox-compatible,
  regular files only). The capability guard on `calculate_directory_size` is removed, since a
  fallback always exists.
- **Verified live:** 3 files / 1,033 bytes: `ssh_dir_calc_size` and `ssh_dir_copy` report exactly
  1,033 on OpenWrt, Alpine, FreeBSD and Debian, and the copied tree is exact.
- **Tests:** `testing_mcp/test_unit_dir_size_command.py` (fallback chosen without `find_printf`,
  no guard). Note: the automated suite doesn't target BusyBox hosts yet.
