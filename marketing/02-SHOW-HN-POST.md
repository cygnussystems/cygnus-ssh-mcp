# Show HN Post

## Title Options (pick one)

**Option A (feature-focused):**
> Show HN: SSH MCP Server – 43 tools to give Claude full control of remote servers

**Option B (cross-platform hook):**
> Show HN: The only SSH MCP that works Windows ↔ Linux ↔ macOS in any direction

**Option C (problem-focused):**
> Show HN: I built an SSH MCP because existing ones couldn't edit files or manage background tasks

---

## Post Body

Most SSH MCP servers let you run commands. I wanted to *manage* servers.

**cygnus-ssh-mcp** gives Claude (or any MCP client) 43 specialized tools:

- **Line-level file editing** - replace a single line in nginx.conf without downloading the file
- **Background task management** - launch a backup, check status later, kill if needed
- **Pre-configured hosts** - "connect to prod" instead of typing credentials every time
- **Full sudo support** - with automatic password handling
- **Cross-platform** - works from Windows/Linux/Mac to Windows/Linux/Mac (yes, even Windows → Windows)

The cross-platform part was the hardest. Getting Unicode to work properly when SSH-ing from Windows to Linux (or vice versa) through PowerShell's encoding mess required using SFTP for all file operations instead of shell commands.

Quick start:

```bash
pip install cygnus-ssh-mcp
```

Then add to Claude Desktop and say: "Connect to myserver and show me disk usage"

GitHub: https://github.com/cygnussystems/cygnus-ssh-mcp

I built this because I manage trading infrastructure across different OSes and got tired of the limitations of basic SSH MCPs. Happy to answer questions about the implementation.

---

## Posting Tips

1. **Best time to post**: Tuesday-Thursday, 6-9 AM Pacific (HN is US-heavy)
2. **Don't ask for upvotes** - against HN rules
3. **Respond to every comment** - engagement helps ranking
4. **Be humble** - HN hates self-promotion vibes
5. **Technical depth wins** - be ready to discuss implementation details

## Expected Questions (prep answers)

- "Why not just use Ansible/SSH directly?"
- "How does this compare to [other MCP]?"
- "What about security of storing credentials?"
- "Why Python instead of Go/Rust?"
