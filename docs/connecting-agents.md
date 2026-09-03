# Connecting your AI tools to contextd

contextd is two processes:

- **the daemon** — long-running, watches this machine, owns the database
- **`contextd mcp`** — short-lived, spawned by your editor, forwards questions to the daemon

Your editor only ever launches the second one. If the daemon is not running, tools still respond,
but they say so instead of returning a blank briefing.

```bash
contextd run                              # terminal 1: the watcher, in the foreground
systemctl --user enable --now contextd    # or leave it to systemd
```

`contextd --mcp` still works as a spelling of `contextd mcp`, so existing editor
configuration does not need changing.

---

## What agents can call

| Tool | Use it when |
|---|---|
| `context_now` | Before asking the user what they are working on. Returns recent activity, their declared intent, and related history. |
| `search_context` | Looking for a specific past moment. Combines meaning-based and literal matching. |
| `recall_similar` | The current problem feels familiar and you want to know how it went last time. |
| `set_intent` | The user states a goal ("I am fixing the login bug") so later briefings stay on track. |

There is also one resource, `context://snapshot/current`, which is the same briefing as
`context_now` with no arguments. Clients that support resources can attach it without spending a
tool-call turn. Its `ttlMs` is `0` — a briefing is stale the moment it is read.

---

## Cursor

`~/.cursor/mcp.json` for every project, or `.cursor/mcp.json` inside one repo:

```json
{
  "mcpServers": {
    "contextd": {
      "type": "stdio",
      "command": "/usr/local/bin/contextd",
      "args": ["mcp"]
    }
  }
}
```

Check **Output → MCP Logs** if the server does not appear.

## Claude Code

```bash
claude mcp add --transport stdio --scope user contextd -- /usr/local/bin/contextd mcp
```

Or commit `.mcp.json` at the repo root to share it with the team:

```json
{
  "mcpServers": {
    "contextd": {
      "type": "stdio",
      "command": "/usr/local/bin/contextd",
      "args": ["mcp"]
    }
  }
}
```

## Claude Desktop

`~/.config/Claude/claude_desktop_config.json` on Linux
(`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "contextd": {
      "command": "/usr/local/bin/contextd",
      "args": ["mcp"]
    }
  }
}
```

Fully quit and reopen the app; reloading the window is not enough.

## Continue.dev

`.continue/mcpServers/contextd.yaml`:

```yaml
name: contextd
version: 0.0.1
schema: v1
mcpServers:
  - name: contextd
    type: stdio
    command: /usr/local/bin/contextd
    args:
      - mcp
```

MCP only works in Continue's **agent** mode.

---

## Checking it works without an editor

Drive the protocol by hand. Anything on stdout is JSON-RPC; logs go to stderr.

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"probe","version":"1.0"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  | contextd mcp 2>/dev/null
```

You should see `context_now`, `search_context`, `recall_similar`, and `set_intent`.

To skip MCP entirely and talk to the daemon directly, the socket speaks the same four verbs:

```bash
{"query":"now"}
{"query":"now","text":"login bug"}
{"query":"search","text":"connection refused","limit":10}
{"query":"recall","text":"flaky test"}
{"query":"intent","text":"fixing the login bug"}
```

Note that `nc -U` will hang, because it holds the connection open waiting for more input. Use a
client that half-closes after writing, the way `contextd mcp` does.

---

## Troubleshooting

**"Is the contextd daemon running?"** — the MCP server is fine; the daemon is not. Start `contextd`.

**Tools appear but every briefing is empty** — the daemon is running but has not observed anything
yet. It only watches the directory it was started in.

**Related history is always empty** — that half needs embeddings, which need Ollama. Everything else
keeps working without it; `search_context` falls back to literal matching and reports
`"semantic": false` so you can tell the difference.
