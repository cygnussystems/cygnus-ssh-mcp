# Content-Aware File Editing Tools for MCP SSH Agent

This document defines a robust set of tools for line-based configuration file editing using **line content as anchors** instead of relying on fragile line numbers. These tools are designed for safe, LLM-driven remote file management via SSH, using only standard Linux utilities.

---

## Tool List

### 1. `ssh_file_find_lines_with_pattern`

**Purpose:** Search for a keyword or regex pattern in a file.

**Input:**

```json
{
  "file_path": "/etc/ssh/sshd_config",
  "pattern": "PermitRootLogin"
}
```

**Output:**

```json
{
  "total_matches": 2,
  "matches": [
    { "line_number": 14, "content": "PermitRootLogin yes" },
    { "line_number": 42, "content": "#PermitRootLogin no" }
  ]
}
```

---

### 2. `ssh_file_get_context_around_line`

**Purpose:** Return lines before and after a line that matches exactly.

**Input:**

```json
{
  "file_path": "/etc/ssh/sshd_config",
  "match_line": "PermitRootLogin yes",
  "context": 5
}
```

**Output:**

```json
{
  "match_line_number": 14,
  "context_block": [
    { "line_number": 9, "content": "..." },
    ...
    { "line_number": 14, "content": "PermitRootLogin yes" },
    ...
  ]
}
```

---

### 3. `ssh_file_replace_line_by_content`

**Purpose:** Replace a unique line (by exact content) with new lines (1 or more).

**Input:**

```json
{
  "file_path": "/etc/ssh/sshd_config",
  "match_line": "PermitRootLogin yes",
  "new_lines": [
    "# Set secure root login policy",
    "PermitRootLogin prohibit-password"
  ]
}
```

**Output:**

```json
{ "success": true, "lines_written": 2 }
```

---

### 4. `ssh_file_insert_lines_after_match`

**Purpose:** Insert lines *after* a unique line match.

**Input:**

```json
{
  "file_path": "/etc/ssh/sshd_config",
  "match_line": "# Custom SSH settings",
  "lines_to_insert": [
    "PasswordAuthentication no",
    "AuthorizedKeysFile .ssh/authorized_keys"
  ]
}
```

**Output:**

```json
{ "success": true, "lines_inserted": 2 }
```

---

### 5. `ssh_file_delete_line_by_content`

**Purpose:** Delete a line matching a unique content string.

**Input:**

```json
{
  "file_path": "/etc/ssh/sshd_config",
  "match_line": "PermitRootLogin yes"
}
```

**Output:**

```json
{ "success": true }
```

---

### 6. `ssh_file_copy`

**Purpose:** Copy a file (optionally timestamped backup).

**Input:**

```json
{
  "source_path": "/etc/ssh/sshd_config",
  "destination_path": "/etc/ssh/sshd_config.bak",
  "append_timestamp": true
}
```

**Output:**

```json
{
  "success": true,
  "copied_to": "/etc/ssh/sshd_config.bak.20240513T1443"
}
```

---

### 7. `apply_edit_plan` (Optional)

**Purpose:** Atomically apply multiple line-based operations in sequence, all validated by expected line content.

**Input:**

```json
{
  "file_path": "/etc/ssh/sshd_config",
  "operations": [
    {
      "type": "replace_line_by_content",
      "match_line": "PermitRootLogin yes",
      "new_lines": ["PermitRootLogin no"]
    },
    {
      "type": "insert_lines_after_match",
      "match_line": "Match User",
      "lines_to_insert": ["PasswordAuthentication no"]
    }
  ]
}
```

**Output:**

```json
{ "success": true, "operations_applied": 2 }
```

---


