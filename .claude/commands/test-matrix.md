---
description: Run cross-platform test matrix
---

Run the cross-platform test matrix to test the SSH MCP tools from all runner machines against all targets.

Arguments:
- $ARGUMENTS: Optional. Format: `--runner=<linux|windows|macos|all> --target=<linux|windows|macos|all>`

Examples:
- `/test-matrix` - Run all 9 combinations
- `/test-matrix --runner=linux` - Run from Linux runner against all targets
- `/test-matrix --target=windows` - Run from all runners against Windows only
- `/test-matrix --runner=macos --target=linux` - Single combination

Execute this command:

```bash
cd $PROJECT_ROOT && python testing_matrix/run_matrix.py $ARGUMENTS
```
