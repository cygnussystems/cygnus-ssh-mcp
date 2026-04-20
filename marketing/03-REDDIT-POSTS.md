# Reddit Posts

## Subreddit Targets

| Subreddit | Subscribers | Fit | Post Type |
|-----------|-------------|-----|-----------|
| r/ClaudeAI | ~50k | Perfect | Show off tool |
| r/LocalLLaMA | ~200k | Good | MCP ecosystem |
| r/selfhosted | ~400k | Great | Homelab angle |
| r/devops | ~200k | Good | Automation angle |
| r/sysadmin | ~500k | Moderate | Server management |
| r/homelab | ~600k | Great | Multi-server control |

---

## r/ClaudeAI Post

**Title:**
> I built an SSH MCP with 43 tools - manage any server from Claude (cross-platform)

**Body:**

Hey everyone,

I got frustrated with basic SSH MCPs that only let you run commands, so I built one that actually lets you *manage* servers.

**What it does:**

- 43 specialized tools (not just "run command")
- Line-level file editing (change one line in a config without downloading the whole file)
- Background task management (launch a backup, check on it later)
- Pre-configured hosts with aliases ("connect to prod")
- Works cross-platform: Windows ↔ Linux ↔ macOS in any direction
- Full Unicode support (yes, even Windows)

**Quick demo:**

"Connect to my-server and find all log files over 100MB"
"Edit /etc/nginx/nginx.conf and change worker_connections to 4096"
"Start a backup in the background and let me know when it's done"

**Install:**

```bash
pip install cygnus-ssh-mcp
```

GitHub: https://github.com/cygnussystems/cygnus-ssh-mcp

I use this to manage my trading infrastructure across different OSes. Would love feedback!

---

## r/selfhosted Post

**Title:**
> Give Claude control of your homelab servers with this SSH MCP (43 tools, cross-platform)

**Body:**

For those using Claude Desktop or Claude Code - I built an SSH MCP that goes beyond basic command execution.

**Why I built it:**

I have servers on different OSes (Linux boxes, Windows server, Mac mini) and wanted Claude to manage them all without me typing SSH commands constantly.

**Features homelabbers will like:**

- **Host aliases** - configure once, then just say "connect to nas" or "connect to pihole"
- **Background tasks** - start a long rsync, check status later
- **Line-level file editing** - edit docker-compose.yml without vim
- **Cross-platform** - Windows, Linux, macOS as both client and server
- **Sudo support** - with automatic password handling

**Example uses:**

- "Connect to my NAS and show me disk usage on all drives"
- "Check if Plex is running on media-server, restart it if not"
- "Find all files over 1GB in my downloads folder"

GitHub: https://github.com/cygnussystems/cygnus-ssh-mcp

Free, GPL-3.0 licensed. Would love to hear how you'd use this!

---

## r/devops Post

**Title:**
> SSH MCP for Claude with 43 tools - line-level editing, background tasks, cross-platform

**Body:**

Built this because I needed more than "run command and pray" from SSH MCPs.

**Key differentiators:**

| Feature | Basic SSH MCPs | This one |
|---------|----------------|----------|
| Run commands | ✅ | ✅ |
| Line-level file editing | ❌ | ✅ |
| Background task management | ❌ | ✅ |
| Host aliases | ❌ | ✅ |
| Windows server support | ❌ | ✅ |
| Proper sudo handling | Limited | ✅ |

**Use case:** I manage trading infrastructure and got tired of downloading configs, editing locally, uploading. Now I just tell Claude "change worker_connections to 4096 in nginx.conf on prod" and it edits the exact line.

```bash
pip install cygnus-ssh-mcp
```

GitHub: https://github.com/cygnussystems/cygnus-ssh-mcp

Open to feedback, especially on security concerns around credential storage.

---

## Posting Tips

1. **Don't post to all subreddits on the same day** - looks spammy
2. **Engage with comments** - Reddit rewards engagement
3. **Cross-post carefully** - some subs have rules against it
4. **Flair appropriately** - use [Tool] or [Project] flair where available
5. **Don't be defensive** - thank critics, they're giving you free feedback
