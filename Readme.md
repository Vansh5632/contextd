# contextd

A Linux-first context broker for handing work between coding agents. A local daemon records development activity and serves a compact briefing of the current task, constraints, decisions, open questions, and next action through MCP or a Unix socket.

The current briefing is built from a **SQLite materialized task and claim projection**. History search is separate. Classification, summaries, and handoff extraction use rules; optional Ollama embeddings add semantic search.

## Start here

- [Documentation index](docs/README.md) — reading order and historical reports
- [Build and setup](docs/build-walkthrough.md) — prerequisites, configuration, hooks, and service
- [Connect an agent](docs/connecting-agents.md) — MCP tools and observation examples
- [Architecture](docs/architecture.md) — what the current code actually does
- [Status and limits](docs/status.md) — Component 2.1 and unfinished work

## Install

Requires Linux, Rust 1.88 or newer, a native build toolchain, and `nc` for shell/Git hooks. A systemd user session is optional.

```bash
git clone https://github.com/vansh5632/contextd.git
cd contextd
./install.sh
```

The script builds with `cargo build --release --locked`, installs to `~/.local/bin`, writes configuration and integrations, and attempts to start the service. By default it watches the directory where installation took place. Set `watch_root` in the generated configuration for a different project; see [setup](docs/build-walkthrough.md).

For a foreground development run without installing shell hooks:

```bash
cargo build --locked
cd /path/to/project
/path/to/contextd/target/debug/contextd run
```

In another terminal, using the installed binary:

```bash
contextd status
contextd intent "fix the login bug"
```

Configure an MCP client to launch `contextd mcp`, using the installed binary's absolute path if its environment does not include `~/.local/bin`:

```json
{
  "mcpServers": {
    "contextd": { "command": "contextd", "args": ["mcp"] }
  }
}
```

The daemon must already be running. It exposes five tools: `context_now`, `search_context`, `recall_similar`, `set_intent`, and `note_claim`.

## Current status — 10 November 2026

Task projection, authored claims, live `observe` ingestion, and local handoff extraction are implemented. **Component 2.1 is not frozen.** Stricter clause grounding rejects more unsupported conclusions, but also removes useful paraphrases; the latest pool-bug evaluation recorded a slower, more expensive handoff than the previous automatic page. These are episode-specific measurements, not a general quality guarantee. See [status and evidence](docs/status.md).

There is no editor-event producer. Shell hooks capture commands, working directories, and exit codes, **not stdout**; agents must supply tool output through `observe` for evidence-backed extraction.

Defaults are local: SQLite, a user-private Unix socket, and Ollama at `localhost:11434`. The Ollama URL is configurable, and consuming agents can use external services. No model is required for the current briefing or literal history search.

## Commands

| Command | Purpose |
|---|---|
| `contextd run` | Run the daemon; also the default with no arguments |
| `contextd mcp` | Serve MCP over stdio; `--mcp` remains an alias |
| `contextd intent <TEXT>` | Record the user's goal |
| `contextd note <TEXT>` | Store a note for handoff extraction |
| `contextd observe '<JSON>'` | Record a tool result, conclusion, user statement, retract, or update |
| `contextd handoff` | Extract and admit claims from stored activity |
| `contextd claim ...` | State a constraint/decision/ruled-out approach, retract a claim, or mark work done |
| `contextd status` | Print paths and daemon/task status |
| `contextd install` | Write config, shell/Git hooks, and a systemd user unit |
| `contextd uninstall` | Remove the managed shell block and unit; see [cleanup limits](docs/build-walkthrough.md#uninstall) |
| `contextd help` | Show command syntax |

Configuration defaults to `~/.config/contextd/config.toml`; data defaults to `~/.local/share/contextd`. XDG variables are respected. `max_memory_mb` exists but is not enforced.

## Development and license

See [contributing](CONTRIBUTING.md), [testing](TESTING.md), and [agent guidance](Agent.md). This is a Rust workspace; use `cargo test --workspace --all-features` for the Rust suite.

The repository's [LICENSE](LICENSE) is **PolyForm Noncommercial 1.0.0**. Cargo package metadata currently says `MIT OR Apache-2.0`; that discrepancy remains unresolved and does not replace the license file.
