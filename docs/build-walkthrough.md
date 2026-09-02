# contextd — step-by-step build walkthrough

How the daemon was actually assembled, what each crate is for, and what to build next.

This file describes the *running* code as of September 2026. The four-tier memory system, the pipeline classifiers, and packaging all exist now; where this document once said "designed but not built", it now says where the code lives.

---

## Mental model

contextd is a local background process. Sensors notice activity on your machine, convert it into one shared JSON type (`RawEvent`), push it onto an in-process broadcast bus, score it with if/else rules, and write it to SQLite. Everything expensive — embedding, classification, summarizing, graph building — happens afterwards in a bounded worker. An agent then asks the same Unix socket for a briefing assembled from every tier.

```
sensors  →  Tokio broadcast bus  →  heuristics score  ┬→  Tier 0 working set (RAM)
                                                      └→  Tier 1 SQLite events.db
                                                              │
                                          bounded enrichment queue (off hot path)
                                          analyze → decide → embed
                                                              │
                                     ┌────────────────────────┼──────────────────┐
                                     ↓                        ↓                  ↓
                              Tier 2 graph            vec_events (768-d)   Tier 3 zstd archive
                            (petgraph + tables)                            (via the pruner)

agent  →  contextd mcp (stdio JSON-RPC)  →  Unix socket  →  broker: rank all four tiers,
                                                            fit a token budget, reply
```

The load-bearing rule throughout: **watching never depends on a model being online.** Ollama being down costs you semantic search and nothing else.

---

## Crate jobs (what each piece is for)

| Crate | Job | What it does today |
|---|---|---|
| `crates/core` | Shared language | `RawEvent`, `ProcessedEvent`, `Intent`, `ContextRequest`, `AppConfig`, errors. Every other crate depends on this. |
| `crates/sources` | Sensors | Produce `RawEvent`s: Unix socket, filesystem (noise-filtered), git hooks, process poller, manifest chaining. Also owns hook installation. |
| `crates/pipeline` | Classification and summarizing | `heuristics` scores on the hot path; `classify`, `content`, `memory`, and `decision` run behind the queue. |
| `crates/store` | Tiers 1 and 3 | `Store` (one writer, a read pool, WAL) over `events` + `vec_events` (768-d), plus the zstd `archive`. |
| `crates/memory` | Tiers 0 and 2 | In-RAM `WorkingSet`, and a `petgraph` knowledge graph persisted to `graph_nodes` / `graph_edges`. |
| `crates/ai` | Local LLM client | Ollama health + `/api/embed`. Configurable model; every call is optional. |
| `crates/background` | Off-hot-path work | The bounded enrichment queue, and a pruner that archives rather than deletes. |
| `bin/contextd` | Glue and CLI | Starts tasks, owns the bus, is the only DB writer. Subcommands: `run`, `mcp`, `intent`, `status`, `install`, `uninstall`. |
| `crates/broker` | Agent-facing snapshot | Ranks all four tiers by recency + importance + similarity + graph proximity, then fits a token budget. |
| `crates/mcp` | Agent-facing protocol | `rmcp` stdio server: `context_now`, `search_context`, `recall_similar`, `set_intent`, and a `context://` resource. |

---

## Build sequence (what we actually shipped)

Each step below is one git-era. Later steps *use* earlier ones; they do not replace them.

### Step 0 — Workspace scaffold (Mar 29 – Apr 3)

**Files:** `Cargo.toml`, `CONTRIBUTING.md`, `.github/workflows/ci.yml`

Empty Rust workspace + CI (fmt, clippy, test). No daemon. This locked “single static binary, local only.”

### Step 1 — Shared event language (Apr 3, #1)

**File:** `crates/core/src/event.rs`

`RawEvent` is the contract:

- `id` — string (ULID in producers)
- `timestamp_ms`
- `source` — `shell` \| `file_system` \| `git` \| `editor` \| `proc` \| `manifest`
- `payload` — arbitrary JSON

`editor` is in the enum so later code can use it. Nothing produces editor events yet.

### Step 2 — Config and errors (Apr 5, #3)

**Files:** `crates/core/src/config.rs`, `crates/core/src/error.rs`, `crates/core/src/test_utils.rs`

Hardcoded defaults until a TOML file exists:

- DB: `/tmp/contextd/events.db`
- Socket: `/tmp/contextd/contextd.sock`
- `max_memory_mb: 500` — stored, never enforced

### Step 3 — Daemon + shell socket (Apr 11, #7)

**Files:** `bin/contextd/src/main.rs`, `crates/sources/src/shell.rs`

This is when it becomes a process.

1. Tokio runtime + tracing
2. Create `/tmp/contextd/`
3. Open SQLite
4. `broadcast::channel(100)` — the event bus
5. Spawn Unix listener on the socket
6. Main task: `recv` → persist

The socket is **ingest only**. Clients send newline-delimited JSON `RawEvent`s. There is no reply, no query.

Git hooks later reuse this same socket (`nc -U`). There is no second listener.

### Step 4 — Filesystem watcher (Apr 11, #8)

**File:** `crates/sources/src/filesystem.rs`

`notify` / inotify on the process cwd. Create / modify / remove become `FileSystem` events `{action, path}`. Paths under `target/` and `.git/` are dropped.

No debounce: a burst of saves is a burst of bus events.

### Step 5 — Process poller (Apr 12, #9)

**File:** `crates/sources/src/proc_poller.rs`

Every 1s, `sysinfo` looks at PIDs whose name contains `cargo`, `node`, `npm`, `python`, `docker`, or `rustc`. Emits `process_start` (cmdline) and `process_stop`.

This is how “I just started a build” gets into context without a shell hook.

### Step 6 — Heuristics + persist scores (Apr 22 #10, May 6 #11)

**Files:** `crates/pipeline/src/heuristics.rs`, `crates/store/src/db.rs`

The live pipeline is this one stage:

```
RawEvent  →  process_event()  →  ProcessedEvent { raw, score }  →  INSERT events
```

| Signal | Score |
|---|---|
| Manifest (`Cargo.toml` / `package.json` derived event) | 0.95 |
| `cargo build` / `npm run` (shell or process start) | 0.9 |
| Git `commit` / `checkout` | 0.8 |
| `Cargo.toml` / `package.json` filesystem save | 0.8 |
| Other process starts | 0.7 |
| Default / editor fallback | 0.5 |
| `ls` / `cd` | 0.1 |

No Phi-3 classifier. No summarization. Payload is stored verbatim.

### Step 7 — Git post-commit hook (May 15, #12)

**File:** `crates/sources/src/git.rs`

On boot: walk up from cwd to `.git`, install a managed `post-commit` hook (existing hook backed up). The hook sends `{action: commit, hash, message}` through `nc -U` to the socket.

Only post-commit. No checkout/push producer (the scorer already knows those action names).

### Step 8 — Manifest watcher (May 16, #14)

**File:** `crates/sources/src/manifest.rs`

Listens on the **same bus**. If a filesystem event path is `Cargo.toml` or `package.json` (not under `node_modules`), it injects a second event: `source=manifest`, `action=dependencies_updated`.

Filename match only — it does not parse TOML/JSON or diff dependencies.

This is the first **derived** source: one real OS event becomes two stored events.

### Step 9 — Ollama client (May 16–17, #13 #15)

**File:** `crates/ai/src/ollama.rs`

- `GET /api/tags` — health, 2s timeout
- `POST /api/embeddings` — `nomic-embed-text`, 768 floats

At boot the daemon logs “AI enabled” or “heuristics-only”. The health check is log-only: the client is kept either way, and every call fails open.

Note: `/api/embeddings` is the legacy Ollama endpoint, superseded by `/api/embed`.

### Step 10 — Vector store (May 21, #16) — `develop`

**File:** `crates/store/src/vector.rs`

Registers `sqlite-vec`, creates `vec_events(event_id, embedding float[768])`, implements insert + cosine KNN.

### Step 11 — Background pruner (May 21, PR #18)

**File:** `crates/background/src/pruner.rs`

Wakes every hour. Deletes rows with `timestamp_ms` older than 7 days **and** `score < 0.5`, then orphan embeddings.

Not an archive. `zstd` is a workspace dependency with no call sites. High-score commits are kept.

### Step 12 — Embed on ingest

**File:** `bin/contextd/src/main.rs`

After a successful `insert_event`, the daemon spawns a detached task that embeds the payload and calls `insert_embedding`. If Ollama is down the warn is logged and the row simply has no vector.

This is what finally gives `vec_events` a production writer. It is also unbounded: one `tokio::spawn` per event, with no queue and no backpressure.

### Step 13 — Socket query protocol

**File:** `crates/sources/src/shell.rs`

The socket stops being ingest-only. `parse_inbound_line` classifies each line:

- `{"query":"now"}` (optionally with `"text"`) → `InboundLine::Query`
- anything else that deserializes as `RawEvent` → `InboundLine::Event`

A query gets a `oneshot` channel wrapped in a `ContextQuery`, sent to the daemon over an `mpsc`. The daemon answers with a serialized snapshot on the same connection. Ingest stays fire-and-forget.

### Step 14 — Broker snapshot

**File:** `crates/broker/src/snapshot.rs`

`snapshot_now` returns `{ recent_activity, relevant_history }`. Recency is the newest 10 events; relevance is up to 5 KNN matches for the query embedding, skipping anything already in the recent window. If the query text is empty it embeds the newest payload instead.

Fail-open at every step: no Ollama, or a failed embed, or a failed KNN, still yields the recency half.

### Step 15 — MCP stdio server

**Files:** `crates/mcp/src/{stdio,rpc,daemon}.rs`, `bin/contextd/src/main.rs`

`contextd --mcp` does **not** start the daemon. It is a short-lived stdio child that Cursor/Claude launch, which forwards to the already-running daemon over the Unix socket.

Hand-rolled JSON-RPC 2.0, newline-delimited, protocol `2024-11-05`. Implements `initialize`, `notifications/initialized`, `ping`, `tools/list`, `tools/call`. One tool: `context_now`. A daemon that is not running comes back as an MCP tool error (`isError: true`), not a JSON-RPC error, which is the correct distinction.

Logging goes to stderr so stdout stays a clean protocol pipe.

---

## Follow one event (cargo build)

1. **proc_poller** sees a new `cargo` PID → `RawEvent { source: proc, payload: { action: process_start, command: "… cargo build" } }`
2. **broadcast bus** delivers it to the main loop (manifest watcher ignores it — not a filesystem event)
3. **heuristics** scores **0.9**
4. **store** inserts into `events`
5. **embedder** spawns, and if Ollama answers, writes 768 floats into `vec_events`
6. **pruner** — keeps it (score ≥ 0.5)
7. later, an agent calls `context_now` and this event comes back in `recent_activity`

A `Cargo.toml` save is different: filesystem emits one event (score 0.8) **and** the manifest watcher emits a second (score 0.95). Both are stored.

A git commit never touches the poller: the hook writes JSON to the Unix socket; the shell listener publishes it; score 0.8.

---

## What is designed but not built

- Use-case classifier, content processor, memory-type classifier, decision engine — the pipeline is still one if/else scorer
- Tier 0 in-RAM working set
- Tier 2 knowledge graph (`petgraph` unused)
- Tier 3 compressed archive (`zstd` unused) — the pruner hard-deletes
- Shell hook installer (`.bashrc` / `.zshrc`)
- Git `post-checkout` / `pre-push` producers (the scorer already knows `checkout`)
- systemd unit, TOML config file (`toml` unused), one-command install
- No indices on `events`, so every recency query is a full scan
- A single `Arc<Mutex<Connection>>` shared by the writer, the pruner, and every query

`EventSource::Editor` is unused and stays that way: there is no VS Code extension in scope.

---

## What is still open

The loop the project set out to build now works end to end. What remains is judgement, not plumbing:

- **The classifiers are rules, not models.** `crates/pipeline` decides use case and memory type with heuristics. The plan always allowed a small local instruct model as an async upgrade behind the same interface; nothing calls one yet.
- **Tier 3 is written but rarely read.** The archive is populated and can be read back, but the broker only surfaces a hint that it exists rather than searching inside it.
- **Multi-repo.** One daemon watches one root. Watching several projects at once means either several daemons or a real per-repo scope.

---

## How to verify locally

Paths below assume the defaults; `contextd status` prints the real ones.

```bash
cargo test --workspace
contextd install       # shell hook, git hooks, systemd unit, config file
contextd run           # or: systemctl --user enable --now contextd
```

In another terminal, check what it can see:

```bash
contextd status
contextd intent "fixing the login bug"
```

Send an event by hand, the way a hook does:

```bash
printf '%s\n' '{"source":"shell","payload":{"command":"cargo build","exit_code":0}}' \
  | nc -U -N "$(contextd status | awk '/^socket/{print $2}')"
```

Ask for a briefing the way an agent does:

```bash
printf '%s\n' '{"query":"now"}' | nc -U -N "$(contextd status | awk '/^socket/{print $2}')"
```

Note the `-N`. Without it most netcats never half-close the socket, so `nc` waits
for the daemon while the daemon waits for another line. The hooks in
`crates/sources/src/emit.rs` handle this themselves; when typing by hand you have
to remember it.

Or drive the MCP server directly:

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"context_now","arguments":{}}}' \
  | contextd mcp
```
