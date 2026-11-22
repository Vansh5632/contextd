# Connecting agents

The long-running daemon records activity and owns storage. A client launches `contextd mcp`, a separate **stdio** process that forwards requests to the already-running daemon over a Unix socket. `contextd --mcp` remains an alias. There is no HTTP MCP endpoint.

Start the daemon in your project, or use the installed user service:

```bash
contextd run
```

```bash
systemctl --user enable --now contextd
contextd status
```

Use one of those startup methods, with the same configuration as the MCP process. See [setup](build-walkthrough.md) for `watch_root`, XDG paths, and service behavior.

## Client configuration

Find the installed executable with `command -v contextd`. The default installer path is `~/.local/bin/contextd`; replace `/home/you` below with your actual home directory. An absolute path avoids differences between an editor's PATH and your shell's PATH.

For clients using an `mcpServers` JSON object:

```json
{
  "mcpServers": {
    "contextd": {
      "command": "/home/you/.local/bin/contextd",
      "args": ["mcp"]
    }
  }
}
```

**Cursor:** put this in `.cursor/mcp.json` for the project or `~/.cursor/mcp.json` for your user. Reload/enable the server in the client. See [Cursor's MCP documentation](https://prod.cursor.com/docs/mcp).

**Claude Code:** register the local stdio command for your user:

```bash
claude mcp add --transport stdio --scope user contextd -- /home/you/.local/bin/contextd mcp
```

For a shared project configuration, use the JSON block in `.mcp.json`. See [Claude Code's MCP documentation](https://code.claude.com/docs/en/mcp) for scopes and client approval behavior.

**Continue:** create `.continue/mcpServers/contextd.yaml`:

```yaml
name: contextd
version: 0.0.1
schema: v1
mcpServers:
  - name: contextd
    type: stdio
    command: /home/you/.local/bin/contextd
    args:
      - mcp
```

Use Agent mode. See [Continue's MCP setup](https://docs.continue.dev/customize/deep-dives/mcp). Other stdio clients, including Claude Desktop, need the same command and args in their supported configuration surface. These examples describe configuration; this documentation refresh did not exercise the clients themselves.

## Tools and resource

| MCP tool | Behavior |
|---|---|
| `context_now` | Current standing constraints, focal task, formerly failing checks that now pass, other unfinished authored work, and other failing-task lines |
| `search_context` | Semantic plus literal search over live recorded events |
| `recall_similar` | Semantic history neighbors, with literal matching when query embeddings are unavailable |
| `set_intent` | Record a goal stated by the user |
| `note_claim` | Record an agent-inferred finding, decision, ruled-out approach, unknown, or next action on the focal task |

Search/recall take `text` and an optional `limit` (default 10, clamped to 1–50). Their `semantic` flag reports query-embedding availability. They do not search the compressed archive or graph.

`context_now` reads the SQLite materialized task/claim projection after a handoff pass. The optional `text` argument is accepted for compatibility but currently does not change the selection. Old event-feed fields `recent_activity`, `relevant_history`, and `related` are absent. See [architecture](architecture.md) for the response contract.

`context://snapshot/current` exposes the same current briefing with no arguments. The resource contents have `ttlMs: 0`; the resource-list metadata has a separate one-hour TTL. Support for displaying/attaching a resource depends on the client.

`note_claim` accepts `kind` values `reality`, `decision`, `ruled_out`, `unknown`, or `next_action`; `confidence` is `inferred` (default) or `hypothesis`. Unknowns become hypotheses. A `reality` sent here has no tool output, so it is stored as a hypothesis rather than an established reality. `replaces` can identify an earlier eligible agent claim. It cannot author constraints, intent, or observed facts. This direct path does not run the evidence check. A conclusion that cites a complete tool observation still goes through handoff.

## User statements and raw notes

```bash
contextd intent "fix the limiter undercount"
contextd claim constraint "do not change the public signature"
contextd claim decision "keep this in-process"
contextd claim ruled-out "database contention"
contextd note "Decision: preserve the existing command identity"
```

`claim` writes stated user claims. It needs a focal task; `intent` can establish one. `note` stores a raw sentence and triggers extraction; it does not directly file a claim, and unrecognized text may be skipped.

Use an actual returned claim ID to retract a user-editable claim:

```bash
contextd claim retract c_REPLACE_WITH_ACTUAL_ID
contextd claim done
```

`done` closes the current focal task. Current constraints remain visible until superseded. Authorship is a protocol convention, not a security boundary: a same-user process with shell access can invoke the user CLI path.

## Record tool output for handoffs

Shell hooks do not record stdout. To preserve what an agent saw, send observations explicitly. A tool observation records a result; it does not execute the command:

```bash
contextd observe '{"kind":"tool","session":"agent-a","command":"inspect retry counter","cwd":"/path/to/project","exit_code":0,"output":"retry_count += 1
open_socket()"}'
```

Copy the returned `event_id` into a conclusion's `support_event_id`:

```bash
contextd observe '{"kind":"conclusion","session":"agent-a","text":"retry_count increments before open_socket","support_event_id":"REPLACE_WITH_TOOL_EVENT_ID"}'
contextd handoff
```

Handoff returns admission details under `accepted`, `rejected`, and `skipped`. This example demonstrates the wire shape; use real command output and identifiers for real work. Output is truncated to at most 200 lines / 8,000 characters. `observe` also accepts `kind: user`, `retract`, and `update`; updates require `replaces_text`. The exact schema is in [protocol.rs](../crates/core/src/protocol.rs).

Extraction also runs at startup, after raw notes, and before `context_now`. It uses local rules and preserves inferred/hypothesis confidence. Component 2.1 can reject unsupported clauses and valid paraphrases; see [current status](status.md).

## Check MCP discovery

This probe needs the built/installed binary, but tool listing does not require a running daemon. Protocol responses go to stdout and logs to stderr:

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"probe","version":"1.0"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  | contextd mcp
```

The tool list should contain all five names above. Client requests negotiate the protocol through `rmcp`; the version shown is a probe example.

## Direct socket access

The socket speaks one JSON object per line. Examples of request bodies:

```json
{"query":"now"}
{"query":"search","text":"connection refused","limit":10}
{"query":"recall","text":"flaky test"}
{"query":"intent","text":"fixing the login bug"}
{"query":"handoff"}
```

For an actual request without netcat variant differences, this Python client discovers the configured socket through `status`, sends one line, half-closes its write side, and reads a reply:

```bash
python3 - <<'PYCLIENT'
import json
import socket
import subprocess

status = subprocess.check_output(["contextd", "status"], text=True)
socket_path = next(line.split(None, 1)[1] for line in status.splitlines()
                   if line.startswith("socket "))
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
    client.settimeout(30)
    client.connect(socket_path)
    client.sendall((json.dumps({"query": "now"}) + "\n").encode())
    client.shutdown(socket.SHUT_WR)
    with client.makefile("r") as response:
        print(response.readline().rstrip())
PYCLIENT
```

A plain `nc -U` can wait indefinitely for connection closure. If using OpenBSD netcat, send a newline and use `-N` to half-close after stdin ends. Other implementations use different flags.

## Troubleshooting

- **Cannot reach daemon:** run `contextd status`; start the daemon and confirm the client uses the same config/XDG environment and socket path.
- **Briefing lacks a focal task:** filesystem edits and commits alone do not open projected tasks. Record user intent or a shell/tool result with a command and exit code. Standing constraints and open authored work can still be shown without a focal failure.
- **No learned conclusions:** the shell hook has no output. Record `observe` tool output and conclusions, then inspect the handoff report.
- **Semantic results unavailable:** check Ollama, configured model, and 768-dimensional output. Literal search remains available; archived events are not searched.
- **Wrong project:** check `watch_root` and the service working directory in [setup](build-walkthrough.md).
