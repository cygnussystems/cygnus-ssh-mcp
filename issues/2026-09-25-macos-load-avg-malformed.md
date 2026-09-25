# macOS `load_avg` is malformed (`LOAD:{ … } LOAD:{ … }`)

| | |
|---|---|
| **Severity** | 🟢 Minor |
| **Status** | **Fixed, 2026-09-25** (branch `fix/cmd-run-timeout-handoff`). Regression test `test_ssh_conn_connect_load_avg_is_three_numbers` in `testing_mcp/test_tool__responsiveness.py` |
| **Found** | 2026-09-25, `ssh_conn_connect` against `MACBOOK-2015` (macOS 14.8.9, Intel MacBook Pro 2015) |

## Symptom

`ssh_conn_connect(host_name="MACBOOK-2015")` →
`system.load_avg: "LOAD:{ 3.20 5.54 6.14 } LOAD:{ 3.20 5.54 6.14 }"`. The value appears
twice and still has the key prefix and braces. Expected: `"3.20 5.54 6.14"`, the format
Linux returns.

## Root cause

`ops/os_ops.py`, macOS `_cmd_system_info`:

```sh
echo "LOAD:$(sysctl -n vm.loadavg | awk "{print \$2, \$3, \$4}")"
```

macOS's `/bin/sh` brace-expands `{print \$2, \$3, \$4}` because of the commas, even
inside the double quotes of this nested `$(...)`. So awk receives a broken program and fails
(`awk: syntax error … >>> print <<< $2`), and the brace-expanded words turn into the doubled
`LOAD:{ … }` text. The `CPU_MHZ` line uses the same quoting but works, because its awk
program (`{print \$1/1000000}`) has no comma. This was the only awk program with a comma in
the codebase.

## Fix

Strip the braces with `sed` instead of awk:

```sh
echo "LOAD:$(sysctl -n vm.loadavg | sed "s/[{}]//g; s/^ *//; s/ *\$//")"
```

Verified live: `MACBOOK-2015` → `"4.93 5.67 6.14"`; `linux-test` is unchanged (`"0.00 0.00 0.00"`).
