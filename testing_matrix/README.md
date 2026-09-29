# Cross-Platform Test Matrix

Run the full test suite from all 3 test machines against all 3 targets (9 combinations total).

## Architecture

```
Local Windows PC (Orchestrator)
         |
         | SSH + MCP Tools
         v
+----------------------------------------------------+
|  Runner Machines (each runs tests against 3 targets) |
+------------------+-----------------+---------------+
| Linux VM (.27)   | Win Server (.9) | macOS (.53)   |
| test/testpwd     | claude/pwd      | claude/pwd    |
+------------------+-----------------+---------------+
```

## Test Matrix

| Runner / Target | Linux | Windows | macOS |
|-----------------|-------|---------|-------|
| Linux           | L->L  | L->W    | L->M  |
| Windows         | W->L  | W->W    | W->M  |
| macOS           | M->L  | M->W    | M->M  |

## Usage

### Run Full Matrix (9 combinations)
```bash
python testing_matrix/run_matrix.py
```

### Run from Single Runner
```bash
python testing_matrix/run_matrix.py --runner=linux
python testing_matrix/run_matrix.py --runner=windows
python testing_matrix/run_matrix.py --runner=macos
```

### Run Against Single Target
```bash
python testing_matrix/run_matrix.py --target=linux
python testing_matrix/run_matrix.py --target=windows
python testing_matrix/run_matrix.py --target=macos
```

### Single Combination
```bash
python testing_matrix/run_matrix.py --runner=macos --target=linux
```

### Skip Wheel Build
```bash
python testing_matrix/run_matrix.py --skip-build
```

## Slash Command

From Claude Code:
```
/test-matrix
/test-matrix --runner=linux
/test-matrix --runner=macos --target=windows
```

## What It Does

For each runner:

1. **Connect** - SSH to the runner machine via MCP tools
2. **Cleanup** - Remove any previous `~/mcp_matrix_test/` directory
3. **Upload** - Transfer wheel and test files using `ssh_dir_transfer`
4. **Install** - Create venv, install wheel and test dependencies
5. **Configure** - Write `.env` file with all target credentials
6. **Test** - Run pytest against each target platform
7. **Cleanup** - Remove the test workspace

## Prerequisites

Each runner machine needs:
- **Python 3.10+** installed and in PATH
  - Linux: Usually pre-installed or `apt install python3`
  - Windows: Install from python.org, ensure "Add to PATH" is checked
  - macOS: Install via `brew install python@3.12` or python.org
- Network access to all target machines (port 22)
- ~100MB disk space for venv

### Current Runner Status

| Runner | Python | Status |
|--------|--------|--------|
| Linux (192.168.1.27) | Python 3.11 (`python3`) | Verified 2026-09-29 |
| Windows (192.168.1.9, `win-server-2016`) | Python 3.12.10 at `C:\Program Files\Python312` (installed 2026-09-29, all users, on PATH) | Verified 2026-09-29 |
| macOS (192.168.1.109, `MACBOOK-2015`) | Python 3.13.15 (`/usr/local/bin/python3`; login shell is zsh) | Verified 2026-09-29. Its password comes from `MACOS_SSH_PASSWORD` in `testing_mcp/.env` |

**Same-machine combinations** (a runner testing itself, e.g. Linux -> Linux) put pytest, the
MCP server and every remote command on one small VM - timing-sensitive tests can then fail
from load alone. Prefer cross-machine combinations; a Linux client can also be simulated from
WSL on the orchestrator PC.

Useful options: `--tests <path>` runs a subset on the runners; each combination's full pytest
output is saved to `%TEMP%\mcp_ssh_matrix_results\<runner>_to_<target>.txt`.

## Files

- `run_matrix.py` - Main orchestration script
- `config.py` - Runner and target configuration
- `README.md` - This file

## Output

```
============================================================
                  CROSS-PLATFORM TEST MATRIX
============================================================

Runner: LINUX (test@192.168.1.27)
  -> LINUX    ... 135 passed in 200s
  -> WINDOWS  ... 135 passed in 199s
  -> MACOS    ... 135 passed in 201s

Runner: WINDOWS (claude@192.168.1.9)
  -> LINUX    ... 135 passed in 198s
  -> WINDOWS  ... 135 passed in 200s
  -> MACOS    ... 135 passed in 199s

Runner: MACOS (claude@192.168.1.109)
  -> LINUX    ... 135 passed in 201s
  -> WINDOWS  ... 135 passed in 200s
  -> MACOS    ... 135 passed in 199s

============================================================
                          SUMMARY
============================================================
Combinations: 9/9 passed
Total tests:  1215
Total time:   30.2m
============================================================
```
