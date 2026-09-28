# OpenWrt archive create reports failure after producing the archive (no `stat` executable)

**Date/harness:** 2026-09-28, OpenCode `openai/gpt-6-sol`, `openwrt-test` (OpenWrt 25.12.5/BusyBox). **Priority:** P1 truthful side-effect result. Black-box result, no server source read.

With tester-owned `/tmp/llm_test/openwrt/config.txt` (26 bytes) created and no archive at destination:

```json
ssh_archive_create({"source_path":"/tmp/llm_test/openwrt","archive_path":"/tmp/llm_test/openwrt.tar.gz","format":"tar.gz"})
```

**Actual response:** `{"status":"error","message":"Command failed with exit code 127. Stderr: ash: stat: not found\n",...}`. A follow-up `ssh_cmd_run({"command":"if [ -f /tmp/llm_test/openwrt.tar.gz ]; then echo ARCHIVE_EXISTS; wc -c /tmp/llm_test/openwrt.tar.gz; else echo NO_ARCHIVE; fi","wait_timeout":5})` → `ARCHIVE_EXISTS\n165 /tmp/llm_test/openwrt.tar.gz\n`. The tar.gz side effect occurred, but the tool did not return `archive_created` or a usable result because its later size query called nonexistent `stat`. A model might retry the reported failed operation and overwrite a valid archive. `capabilities.stat_c:false` warned only about GNU `stat -c`, not that **all** `stat` binaries were absent.

`ssh_archive_extract` correctly rejected this host's missing tar `--strip-components` before extraction with a clear manual fallback; that is expected and separate. The test archive and scratch were removed and absence verified.

**Expected/fix direction:** probe whether `stat` exists at all; use a supported alternative such as `wc -c < file` to report archive size, or return a truthful partial-success response with the actual archive path and an explicit size-metadata error. On a post-create failure, clearly tell the caller that remote output may exist and must be checked before retrying. Never report an unqualified operation failure after leaving a created archive without naming it.
