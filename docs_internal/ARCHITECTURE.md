# Architecture Overview

A map of how cygnus-ssh-mcp is actually built: the class hierarchy, the platform
abstraction pattern, and the hard-won implementation tricks that make Linux/macOS/
Windows behave consistently through one tool surface. Aimed at anyone extending or
debugging the codebase, not at end users of the MCP server (see `docs/` for that).

Complements (doesn't replace) [CMD-EXECUTION-MODEL.md](CMD-EXECUTION-MODEL.md), which
covers the `ssh_cmd_*` family - synchronous execution, timeout semantics, the
command-history model - in much more narrative depth. This document is broader
(every ops module, the host-config layer, cross-cutting platform tricks) but points
to that file rather than repeating it for anything already covered there.

---

## 1. High-level architecture

```
MCP tool layer (server.py, @mcp.tool() functions)
        │
        ▼
SshClient (client.py) - one instance per active connection
        │  holds paramiko.SSHClient at self._client
        │  detects os_type ONCE at connect time
        │  delegates to 5 platform-specific "ops" objects:
        │
        ├─ run_ops    (ops/run.py)       - ssh_cmd_* family: synchronous command execution
        ├─ task_ops   (ops/task.py)      - ssh_task_* family: background/detached tasks
        ├─ file_ops   (ops/file.py)      - ssh_file_* family: single-file operations
        ├─ dir_ops    (ops/directory.py) - ssh_dir_*/ssh_archive_* family: directories/archives
        └─ os_ops     (ops/os_ops.py)    - ssh_conn_host_info: system information

SshHostManager (host_manager.py) - separate from SshClient entirely; manages the
TOML host-config file(s), independent of any active connection. A module-level
mutable global in server.py (not per-connection), swappable at runtime via
ssh_host_use_config (see section 5).
```

Every ops module follows the same pattern: an abstract base class defining the
platform-agnostic *logic* (validation, dry-run semantics, unique-match requirements,
etc.), with `_Linux`/`_Mac`/`_Win` subclasses overriding only the methods that
generate the actual remote command/script text. The base class orchestrates; the
subclass just answers "what's the actual shell/PowerShell command for this
operation on this platform?" This is why fixes to shared logic (e.g. the
`_monitor_command` timeout-handoff mechanism in section 2.1) automatically apply to
every platform at once - they live in the base class, never overridden.

**OS detection happens exactly once, at connect time** (`client.py`, `_detect_os`):
1. Try `uname -s` (5s timeout - a hang here is treated the same as failure, since
   `uname` can hang instead of failing fast on some Windows hosts) → `Linux`/`Darwin`.
2. Fall back to `echo %OS%` (a cmd.exe built-in) → `Windows_NT`.
3. Final fallback: `$PSVersionTable.PSVersion.Major` via PowerShell → confirms Windows
   and gets the PS version in one shot (used later for the PS 5.0+ gate).

Once `os_type` is known, `_create_operations()` picks the concrete subclass for all
five ops objects in one place. **macOS reuses Linux's `run_ops`/`task_ops` wholesale**
(both are bash-compatible enough that no macOS-specific override was needed there) but
gets its own `file_ops`/`dir_ops`/`os_ops` (BSD command syntax differs enough from GNU
to need real overrides). This is why macOS has no entry in some of the tables below -
it's not missing, it's sharing Linux's implementation.

---

## 2. `ssh_cmd_run` vs. `ssh_task_launch`

Both let you run a remote command, but they give fundamentally different lifetime
guarantees - not just sync vs. async:

| | `ssh_cmd_run` (+ `ssh_cmd_check_status`/`_output`/`_kill`/`_history`) | `ssh_task_launch` (+ `ssh_task_status`/`_kill`) |
|---|---|---|
| Returns | After completion, `io_timeout`, `wait_timeout`, or `runtime_timeout` | Immediately, always |
| Output | In-memory circular buffer on this connection's `CommandHandle` | Redirected to a log file on the remote host |
| Survives `io_timeout`/`wait_timeout` | Yes - hands off to background monitoring (see 2.1) | N/A, was never waiting synchronously |
| Survives the *whole SSH session* ending (disconnect/reconnect) | **No** - tied to the connection; a new connection has no memory of it | **Yes** - detached from the SSH session entirely (see 2.2) |
| PID captured | Real remote PID (non-sudo, all platforms - see 3.1) | Real remote PID, always, all platforms |
| Kill mechanism | `ssh_cmd_kill(handle_id)` - looks up PID from this connection's history, kills by PID | `ssh_task_kill(pid)` - kills by bare PID directly |

The practical framing: `ssh_cmd_run` is "one command, tied to the current
connection's lifetime" - convenient because you get a handle immediately and can poll
without managing your own log file paths, but everything about it dies if the
connection drops. `ssh_task_launch` is "genuinely detached" - the right tool for
anything that should survive you disconnecting and reconnecting tomorrow (a Docker
pull, a long backup).

### 2.1 Timeout semantics (summary - see CMD-EXECUTION-MODEL.md for full detail)

Three independent knobs on `ssh_cmd_run`, all checked every poll iteration in
`_monitor_command` (`ops/run.py`):

- **`io_timeout`** (silence-based): stop waiting synchronously, but the remote
  command keeps running - come back and check on it, and you can still kill it if
  you decide to.
- **`wait_timeout`** (elapsed-based): fires after N seconds of *total* elapsed wait,
  regardless of output activity - unlike `io_timeout`, this fires even while the
  command is actively producing output (a chatty `docker pull` with a constant
  progress bar would never go quiet enough to trigger `io_timeout`). Added so a
  caller can check in periodically on a long-running-but-noisy command instead of
  being blocked until it finishes.
- **`runtime_timeout`** (hard wall-clock cap): the only knob that ever kills the
  remote process - a safety net, meant to be set generously, not a UX mechanism.

When `io_timeout`/`wait_timeout` fires, monitoring hands off to a background thread
(`_handoff_to_background`/`_continue_monitoring_in_background`) instead of closing
the channel, so the remote command genuinely keeps running and its real completion
stays observable via `ssh_cmd_check_status`/`ssh_cmd_output` - the thread still
enforces `runtime_timeout` itself (with an internal safety ceiling if the caller
never set one) and reuses the same platform-specific exit-code recovery the
synchronous path uses. `ssh_cmd_kill(handle_id)` works on a handed-off command too,
purely by PID, independent of channel state. Full narrative, including what this
looked like before the 2026-07-04 fix and why, is in
[CMD-EXECUTION-MODEL.md](CMD-EXECUTION-MODEL.md).

**Concurrency:** only one `ssh_cmd_run` is in flight at a time per connection - a
`_busy_lock` is held for the duration of `execute_command` and always released in a
`finally`, including when a command hands off to background monitoring. A second
call while one is running gets a `busy` status rather than queuing or running in
parallel. Use separate connections, or `ssh_task_launch`, for real concurrency.

### 2.2 Why `ssh_task_launch` survives disconnects and `ssh_cmd_run` doesn't

- **Linux**, sudo path: `nohup bash -c '...' > log 2>&1 &` (`ops/task.py:385,400`).
  `nohup` protects against SIGHUP specifically.
- **Linux**, non-sudo path (`ops/task.py:410-412`): **no `nohup`** - just
  `bash -c '...' > log 2>&1 &` inside a non-interactive script. Verified live
  2026-07-05 (full disconnect/reconnect against `linux-test`, Debian 12/OpenSSH
  9.2): the task survived and was still confirmed `running` well before its own
  runtime would have elapsed naturally. `nohup` on the sudo path turns out to be
  redundant rather than the non-sudo path being under-protected - backgrounding
  within a one-shot non-interactive script already detaches the child from the
  launching session's job control on this environment. Not verified against other
  OS/sshd combinations.
- **Windows**: spawned via `Invoke-CimMethod -ClassName Win32_Process -MethodName
  Create` (WMI), *not* `Start-Process`. This was a deliberately hard-won fix:
  `Start-Process`-launched children inherit membership in the SSH session's Windows
  Job Object, and get killed the instant that session ends regardless of
  "detached"/hidden-window flags - verified live, a `Start-Process`-launched `ping`
  died within 2 seconds every time, every Job-Object-inheriting child does. WMI
  process creation goes through a separate service process (the WMI provider host),
  so the result is never a member of the SSH session's job at all, and survives
  independently.

---

## 3. Platform-specific wrapper tricks (the hard-won stuff)

### 3.1 PID capture (real remote PID, not a local channel number)

`CommandHandle.pid` for `ssh_cmd_run` used to be `chan.get_id()` - paramiko's local
channel number, unrelated to anything on the remote host. This meant
`runtime_timeout`'s kill attempt sent `kill -9 <channel_number>` to the remote host,
which essentially never matched a real process, so `runtime_timeout` never actually
killed anything.

Fixed by reusing the exact pattern `ssh_task_launch` already used:
- **Linux/macOS**: `_wrap_for_pid_capture` prepends
  `printf '___SSH_MCP_PID___%s\n' "$$" 1>&2` as the very first thing the shell does
  (`$$` is the wrapper shell's own PID). `_capture_pid` reads stderr in a bounded
  3-second loop, parses the marker, strips it before it reaches the caller.
- **Windows**: spawns via `System.Diagnostics.Process` inside a
  `powershell -EncodedCommand` wrapper (base64-encoding the raw command rather than
  escaping it - nested-quote commands silently mis-parse under naive `"` → `\"`
  escaping, verified live), captures the real PID from a stderr marker, streams
  output live via `Register-ObjectEvent` + a poll loop (a blocking `WaitForExit()`
  would prevent PowerShell from ever running the event handlers).

**Sudo'd commands - fixed 2026-07-05, but only for `ssh_task_launch`/`ssh_task_kill`.**
Live-verified the actual shape of the problem differs by launch path: `ssh_task_launch`'s
sudo wrapper is a pipeline (`echo pw | sudo -S bash -c "$CMD"`), so bash can't
tail-exec-collapse it away - the captured PID is 3 processes above the real command
(bash → sudo → real command), and killing just that PID left sudo/the real command
running, orphaned. `ssh_cmd_run`'s sudo wrapper has no pipe and is the last statement
in its script, so bash *does* collapse it - the captured PID for `ssh_cmd_run` is
already `sudo` itself, and killing it directly already worked (sudo forwards the
signal to its child) - this path was never actually broken, despite earlier
assumptions here. Fixed `ops/task.py`'s `_cmd_kill_process` (Linux/macOS) to query
the real process-group ID live at kill-time (`ps -o pgid=` - never a fixed offset
from the pid) and kill the whole group when the target may be a sudo chain; also
gave `CommandHandle` a `sudo` flag so `runtime_timeout`'s automatic kill
(previously unable to elevate at all) can now do the same. See
`planning/2026-07-05-sudo-kill-scope.md` for the full live-verification writeup.

### 3.2 Windows exit code recovery

`chan.recv_exit_status()` cannot be trusted on Windows - a Win32-OpenSSH +
`cmd.exe` bug flattens any nested child process's real exit code to `1`, every
time, whenever a nested child is involved (which running through the wrapper
always requires). Fixed via a second stderr marker
(`EXIT_CODE_MARKER = '___SSH_MCP_EXITCODE___'`) printed by the wrapper right before
it exits, with the real exit code appended - `_handle_command_completion` recovers
the real value from that marker, falling back to the channel's (unreliable) value
with a warning if the marker never arrives.

### 3.3 Windows kill semantics

Every PID handed out on Windows is a `cmd.exe`/`powershell.exe` **wrapper**, never
the actual workload - there's no `exec()` on Windows, so the wrapper spawns the real
process as a child and waits on it rather than replacing itself. `Stop-Process` on
just the wrapper PID orphans the real process (verified live: a spawned `ping.exe`
kept running after its wrapper was confirmed killed). Fixed by using
`taskkill /F /T <pid>` everywhere (both `ssh_cmd_kill` and `ssh_task_kill`'s
underlying Windows implementation) - `/T` kills the whole process tree in one call.

### 3.4 The Windows background-task log rename race

`ssh_task_launch` writes to a placeholder-named log file (timestamp-based, since the
real PID isn't known until after the process is created), then renames it to the
final `task-<pid>.log` name once the PID is known. On Windows this rename used to
retry synchronously for a short, fixed window (~1.5s) and give up permanently if the
task was still running - Windows rejects renaming a file that's still open (unlike
POSIX rename, which succeeds regardless of open handles), and a task still writing to
its own log holds it open for its entire lifetime. Fixed by launching a small
detached watcher process via the same WMI trick from 2.2 (`Wait-Process -Id <pid>`,
then rename once it actually exits) - the rename now completes whenever the task
finishes, however long that takes, without blocking the launch call waiting on it.

### 3.5 PowerShell command encoding (base64, not shell escaping)

Used pervasively (`ps_encode.py`'s `powershell_encoded_command()`): any script text
destined for PowerShell is UTF-16LE-encoded and base64'd, then invoked as
`powershell -NoProfile -EncodedCommand <blob>`, rather than embedded as a quoted
string. Two independent reasons this matters, both verified live:
- Some Windows hosts configure PowerShell (not `cmd.exe`) as the SSH `DefaultShell` -
  a plain `powershell -Command "...$var..."` string gets parsed and interpolated by
  the *outer* shell before the inner `powershell.exe` ever sees it, corrupting
  `$variable` references.
- Nested quoting (e.g. a command containing its own escaped double quotes) silently
  mis-parses under naive escaping rather than erroring loudly - base64 sidesteps
  quoting entirely since its alphabet has no shell/PowerShell metacharacters.

### 3.6 `cwd` parameter: explicit, fails closed, not remembered (Linux/macOS only)

`ssh_cmd_run`'s `cwd` param is deliberately NOT a "remembered current directory" -
each call is an independent remote process with no shell state carried over (same
model as GitHub Actions steps / Ansible tasks). A prototype that *did* simulate `cd`
persistence across calls was built, tested, and deliberately reverted - it can't be
made fully foolproof (the remembered directory can vanish between calls, a TOCTOU
gap), no real usage evidence asked for it, and it fights the stateless-step idiom
callers already expect.

What shipped: `_wrap_for_explicit_cwd` prepends
```bash
cd -- '<cwd>' 2>/dev/null || { echo ___SSH_MCP_CWD_INVALID___ 1>&2; exit 77; }
```
before the user's command. If the directory doesn't exist, the command **never
runs at all** (verified live - zero side effects on the target). `77` is checked
together with the distinct stderr marker (not exit code alone, since a real command
could legitimately also exit 77) before raising `CwdNotFound`. Not implemented for
Windows (raises a clear error instead) - there's no single reliable shell to wrap
against there (`cmd.exe` vs. PowerShell `DefaultShell` ambiguity).

**Fixed side effect:** the cwd-validation wrapper is a real remote process with a
real PID and this sentinel exit code - its handle used to still end up in
`ssh_cmd_history` looking like the user's command actually ran and exited with code
`77`. Now removed from history entirely when this fires
(`CommandHistoryManager.remove_command`), since the caller was never even given the
handle's `id` to look it up by.

**Related (fixed 2026-07-06):** several other tools issue their own internal helper
commands through the same `SshClient.run()` path (`ssh_file_write`'s sudo mv/mkdir/
chmod/chown dance in `server.py`; `ops/file.py`'s `_replace_content_sudo` shared by
the line-editing tools; `client.py`'s OS-detection/status/sudo-verification probes;
`transfer_directory`'s temp-archive plumbing) - these used to be indistinguishable
from a directly user-issued `ssh_cmd_run` in `ssh_cmd_history`. `CommandHandle` now
carries `origin` (`'user'` default, or `'tool_internal'`/`'connection_probe'`/
`'sudo_probe'`) and `parent_tool` (the MCP tool name that triggered it), threaded
through `run()` → `execute_command()` → `_create_command_handle()` →
`CommandHistoryManager.add_command()`. `ssh_cmd_history`'s new `include_internal`
param (default `True`, preserving existing behavior) filters these out when `False`.
`ops/directory.py`'s ~35 call sites were deliberately left untagged (`origin='user'`)
- each is a `ssh_dir_*`/`ssh_archive_*` tool's own primary action, not hidden
plumbing.

### 3.7 Sudo handling

- **Linux/macOS**: tries passwordless sudo first (`sudo -n whoami`); if that fails,
  falls back to piping the cached password via a heredoc
  (`cat <<'EOF' | sudo -S command`) to avoid ever putting the password on the command
  line (which would be visible in `ps` output to any other user on the box).
- **Windows**: no per-command elevation exists at all. `_handle_sudo` just checks
  whether the *session itself* is already running elevated
  (`self.ssh_client._is_elevated`, detected once at connect time via a Security
  Principal check) - if not, it raises immediately rather than pretending to try.
  `use_sudo=True` is accepted but a no-op distinguishable only by this upfront check;
  the only way to get elevated operations on Windows is to connect as an
  Administrator account.

### 3.8 File/directory platform command tables

| Operation | Linux | macOS | Windows |
|---|---|---|---|
| Permission stat | `stat -c '%a %u %g'` | `stat -f '%Lp %u %g'` (BSD `-f` flag) | `Get-Acl` → owner name only, no octal bits, no group concept |
| Pattern search | `grep -F`/`-E` (POSIX ERE) | same as Linux | SFTP read + local Python `re` matching (not `Select-String` - avoids OEM code page corruption of matched content, see 3.8 note below; different regex flavor from POSIX ERE) |
| Recursive find w/ metadata | GNU `find -printf '%p\t%y\n'` | BSD `find -exec stat -f %HT` + manual type-letter translation (no `-printf` on BSD find) | `Get-ChildItem` + `PSIsContainer` check |
| Directory size | `du -sb` (GNU `-b` for raw bytes) | `find -type f -exec stat -f %z + \| awk` (no `-b` flag on BSD `du`) | `Get-ChildItem -Recurse -File \| Measure-Object -Sum Length` |
| Archive create | `tar -czf` (tar.gz) | `tar -czf` (tar.gz) | `Compress-Archive` (`.zip`, extension auto-corrected if a `.tar.gz` was requested) |
| Archive extract | `tar -xzf --strip-components=1` | same as Linux | Extract to temp dir, detect single top-level folder, move contents up, delete temp dir (emulates `--strip-components` since `Expand-Archive` has no equivalent) |
| Symlink handling in directory copy | `find`+`cp -a` for files/dirs, then re-created explicitly via `ln -sf` for symlinks | same as Linux | Not supported - `Copy-Item -Recurse` has no symlink concept to preserve, `preserve_symlinks` is effectively ignored |
| Unicode file reads | SFTP raw bytes + client-side decode (bypasses shell entirely) | same as Linux | same mechanism - specifically avoids PowerShell's OEM code page corrupting stdout |

Archives are **not cross-platform-portable**: a `.tar.gz` made on Linux can't be
extracted by the Windows extractor (only recognizes `.zip`), and vice versa. There is
currently no portable archive format offered by this tool across all three
platforms.

**Substring-matching bug class** (a lesson worth remembering when writing new
existence checks): a recurring mistake was checking `'exists' in output` (or
`not in`) against an `"exists"`/`"not_exists"` sentinel pair - `"not_exists"`
contains `"exists"` as a substring, so the check was always true regardless of which
branch actually happened. Fixed to exact `== 'exists'` comparisons wherever this
pattern was found (`safe_move_or_rename`, `copy_directory_recursive`,
`create_archive_from_directory`'s post-creation verification). Grep for this pattern
before assuming it's fully swept if you add a new existence check.

### 3.9 System info gathering (`ssh_conn_host_info`)

| Info | Linux | macOS | Windows |
|---|---|---|---|
| CPU | `/proc/cpuinfo` (count, model, MHz via grep) | `sysctl -n hw.ncpu` / `machdep.cpu.brand_string` / `hw.cpufrequency` | `Get-CimInstance Win32_Processor` |
| Memory | `free -m` | `sysctl -n hw.memsize` (total) + `vm_stat` (free/available pages) | `Get-CimInstance Win32_OperatingSystem` (Total/FreePhysicalMemory) |
| OS version | `/etc/os-release` (falls back to `/etc/redhat-release`) | `sw_vers` (productName/productVersion/buildVersion) | `Get-CimInstance Win32_OperatingSystem` (Caption/Version/BuildNumber) |
| Network | `hostname` + `ip -4 addr` per interface (from `/sys/class/net`) | `hostname` + `ifconfig` per interface (from `ifconfig -l`) | `$env:COMPUTERNAME` + `Get-NetIPAddress -AddressFamily IPv4` |
| Disk | `df -h /` + `df -T /` for filesystem type | `df -h /` + `mount \| grep " / "` for filesystem type | `Get-CimInstance Win32_LogicalDisk` (Size/FreeSpace/FileSystem for `C:`) |

All three normalize `os_type` to a fixed vocabulary (`"linux"`/`"macos"`/`"windows"`),
never the raw `uname -s`/`ver` output - this is deliberate so callers don't need to
special-case `"Darwin"` vs `"darwin"` vs whatever a given tool prints.

---

## 4. Host configuration architecture

`SshHostManager` (`host_manager.py`) is entirely separate from `SshClient` - it
manages a TOML file of host definitions and is not tied to any particular connection.

- **File resolution** (no `--config` flag given): `~/.mcp_ssh_hosts.toml` if it
  exists, else `./mcp_ssh_hosts.toml` in the server's working directory. Auto-created
  (with `0o600` permissions and a few commented-out examples) on first run if neither
  exists yet - this is not something a user needs to do manually first.
- **Storage format:** `tomlkit` (not the stdlib `tomllib`) specifically because it
  preserves comments/formatting on round-trip - `_save_hosts` loads the existing
  document, diffs which keys changed, and only rewrites what's different, rather than
  regenerating the whole file from scratch.
- **Every host field is plaintext** in this file - password, sudo_password,
  key_passphrase. This is why a whole cluster of tools/rules exists specifically to
  keep an LLM agent from ever needing to read the raw file: `ssh_host_list` (never
  returns credentials, only key/alias/description), `ssh_conn_add_host`,
  `ssh_host_update` (partial updates, `""` as an explicit clear-field sentinel),
  `ssh_host_remove` - and this project's own `.claude/CLAUDE.md` has a standing rule
  never to read the file directly, even for debugging.
- **Multi-config support (`ssh_host_use_config`):** originally a single global
  `host_manager` instance was created once at server startup and never changed. Now
  `server.py` keeps two module-level references: `host_manager` (the *currently
  active* one, mutable) and `_default_host_manager` (captured once, either at import
  time or in `main()` if `--config` was passed) - `ssh_host_use_config` reassigns the
  `host_manager` global to point at a different `SshHostManager` instance for the
  rest of the session, and calling it with no argument restores the saved default
  reference. Every other host tool just reads whatever `host_manager` currently is -
  Python resolves module globals at call time, so no other function needed to
  change. Deliberately does **not** auto-create a missing alternate file the way the
  true default file is auto-created on first run - an LLM-supplied path with a typo
  should fail loudly, not silently create a stray file.

---

## 5. Known limitations

No currently-known unfixed gaps as of 2026-07-05. Historical detailed write-ups
(repro steps, live-verification notes) live in the maintainer's local `planning/`
folder (gitignored, not part of this repo checkout) when one exists.

Since fixed (kept out of this list, see git history / the local `planning/` folder
for detail): the non-sudo `ssh_task_launch` `nohup` question from section 2.2
(verified 2026-07-05 to need no fix); `ssh_cmd_run` used to drop stderr entirely on
a successful command (`output`/`stderr` are now two separate fields, and
`ssh_cmd_output` gained a `stream` parameter); `cwd_not_found` used to pollute
`ssh_cmd_history` with the validation wrapper's own sentinel PID/exit code (the
handle is now removed from history entirely); `ssh_task_kill`'s `force_kill_used`
field used to be ambiguous (it now accurately reflects whether the SIGKILL fallback
was actually needed); `ssh_file_get_context_around_line`/`ssh_dir_search_files_content`
used to corrupt non-ASCII content on Windows by shelling out to PowerShell
(`Get-Content`/`Select-String`) and reading the result through the normal
command-execution stdout path, which decodes bytes as UTF-8 while the actual bytes
on the wire are in Windows' OEM console code page - fixed 2026-07-05 by rerouting
both through an SFTP read + local Python matching instead (same mechanism
`ssh_file_read` already used), verified live against `win-server-2016`;
sudo'd commands launched via `ssh_task_launch` couldn't be reliably killed
(section 3.1) - fixed 2026-07-05 via process-group killing with a live-queried
real PGID, plus giving `runtime_timeout`'s automatic kill a way to elevate
(`CommandHandle.sudo`) that it never had before; and
`ssh_cmd_check_status` used to misreport a `ssh_cmd_kill`'d background-monitored
command as `'completed'` instead of `'killed'` on Windows specifically - a
`taskkill`'d process's death is reported back over the SSH channel as a real
(if meaningless) numeric exit-status, unlike Linux where a signal-killed process
reports none at all, so the status-priority check picked the wrong branch when
both `exit_code` and `kill_confirmed` ended up set (fixed by checking
`kill_confirmed` first).
