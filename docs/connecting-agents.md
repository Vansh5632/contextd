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
| `context_now` | Before asking the user what they are working on. Returns standing constraints, the focal task when one exists, a short line for each check that used to fail and now passes, authored notes on other unfinished work, and one line per other failing command. Not a recency feed. |
| `search_context` | Looking for a specific past moment. Combines meaning-based and literal matching. |
| `recall_similar` | The current problem feels familiar and you want to know how it went last time. |
| `set_intent` | The user states a goal ("I am fixing the login bug") so later briefings stay on track. |
| `note_claim` | Record an inferred finding, decision, ruled-out approach, unknown, or next action on the focal task. Confidence is `inferred` or `hypothesis`. Cannot write constraints, intent, or observed facts. |

There is also one resource, `context://snapshot/current`, which is the same briefing as
`context_now` with no arguments. Clients that support resources can attach it without spending a
tool-call turn. Its `ttlMs` is `0` — a briefing is stale the moment it is read.

`context_now` no longer returns `recent_activity`, `relevant_history`, or `related`. Evidence stays
in the event log and is reachable through `search_context`.

### User-stated claims

Constraints, decisions, and ruled-out approaches that the *user* states are written from the CLI,
not from MCP:

```bash
contextd intent "fix the limiter undercount"
contextd note "the idle count looks wrong in staging"
contextd claim constraint "do not change the public signature"
contextd claim decision "keep this in-process"
contextd claim ruled-out "database contention"
contextd claim retract c_01HZX...
contextd claim done
```

`contextd note <TEXT>` stores a raw sentence on the event log (`source: note`). It is not a claim. A later handoff may quote it.

The daemon socket is local and same-uid. An agent that can run a shell can also run `contextd claim`.
That is a convention, not a security boundary.

`note_claim` is the agent path. It records `origin=agent` and will not accept `stated` or `observed`
confidence, nor `kind=constraint` / `kind=intent`.

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

You should see `context_now`, `search_context`, `recall_similar`, `set_intent`, and `note_claim`.

To skip MCP entirely and talk to the daemon directly, the socket speaks the same verbs:

```bash
{"query":"now"}
{"query":"search","text":"connection refused","limit":10}
{"query":"recall","text":"flaky test"}
{"query":"intent","text":"fixing the login bug"}
{"query":"user_claim","kind":"constraint","text":"do not change the public signature"}
{"query":"agent_claim","kind":"reality","text":"the check is in _assign_requests_to_connections","confidence":"inferred"}
```

Note that `nc -U` will hang, because it holds the connection open waiting for more input. Use a
client that half-closes after writing, the way `contextd mcp` does.

---

## Troubleshooting

**"Is the contextd daemon running?"** — the MCP server is fine; the daemon is not. Start `contextd`.

**Tools appear but every briefing is empty** — the daemon is running but has no current task.
Shell failures and `contextd intent` open tasks. It only watches the directory it was started in.

**Related history is always empty** — that half needs embeddings, which need Ollama. Everything else
keeps working without it; `search_context` falls back to literal matching and reports
`"semantic": false` so you can tell the difference.
