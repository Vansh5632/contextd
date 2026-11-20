# Current architecture

Implementation reference, checked against the working tree on **10 November 2026**. Historical designs and measurements are indexed in [docs/README.md](README.md). The source is authoritative when it differs from this guide.

## Data flow

```text
shell/Git hooks ───────┐
filesystem watcher ───┤
process poller ───────┼─> event bus -> score/filter -> SQLite events
manifest watcher ────┘                         ├─> RAM working set + graph
                                               └─> bounded enrichment queue
                                                     rules + optional embedding

agent observation -> Unix socket -> store tool result or note
                                  -> handoff extraction -> claim events
shell exits + claim events -> deterministic fold -> SQLite tasks + claims

MCP client -> contextd mcp (stdio) -> Unix socket -> handoff -> projection -> briefing
                                                -> search/recall -> live events/vectors
```

This is a Rust workspace with one `contextd` executable. The long-running daemon owns ingestion and storage; an agent launches a separate `contextd mcp` child that forwards requests to the daemon. The socket protocol is newline-delimited JSON, **not HTTP**. There is no `/context/now` HTTP endpoint.

## Ingestion and storage

[main.rs](../bin/contextd/src/main.rs) wires together these sources:

| Source | Current producer |
|---|---|
| Shell | Installed Bash, Zsh, or Fish hook: command, exit code, cwd; no stdout |
| Filesystem | Recursive `notify` watcher under `watch_root`, with noise filtering |
| Git | Managed `post-commit`, `post-checkout`, and `pre-push` hooks; push records an attempt before Git completes |
| Process | Linux process polling for development processes |
| Manifest | Derived events when relevant manifest paths change; not a dependency diff |
| Note | `contextd note` and agent observation notes |
| Claim | Authored claims and admitted extraction results |
| Editor | Enum value only; no editor-event producer or extension |

Sensor events travel through a bounded Tokio broadcast channel. The daemon applies heuristic importance scoring and early noise filtering before persistence, then updates the working set and graph and offers the event to enrichment. A lagging broadcast consumer can miss events; the daemon logs lag. Raw sensor ingestion is not an acknowledged durable-delivery protocol. Notes and `observe` have storage acknowledgements.

[Store](../crates/store/src/store.rs) provides one SQLite writer and pooled readers, using WAL for file-backed databases. Core tables include live `events`, the 768-dimensional `vec_events` index, materialized `tasks` and `claims`, graph tables, and compressed `archive` segments.

The event log provides replay evidence, but it is not an immutable transcript: noise can be dropped, long payloads are compacted, and eligible old rows move to the archive. Claim events and evidence needed by the current projection are protected from normal pruning.

## Projection and current briefing

[memory::project](../crates/memory/src/project.rs) folds shell exits and claim events in `(timestamp_ms, id)` order. Filesystem, process, and Git events do not independently become tasks.

- A shell task is keyed by trimmed cwd and whitespace-normalized command. This is command identity, not semantic grouping of a whole investigation.
- The newest exit for that task supersedes the previous reality. A successful rerun must match the same command identity to supersede its failure.
- The oldest active task with a current nonzero exit is focal. Otherwise, the active task with the newest user intent is focal. A recent commit does not outrank an unresolved failure.
- Claim events preserve authorship, confidence, status, replacement, evidence references, and task identity. User constraints stay visible until superseded, even when a task is done.

The daemon rebuilds the projection at startup and after fold-relevant writes. Before serving `context_now`, it runs handoff extraction. [snapshot::build](../crates/broker/src/snapshot.rs) then **reads the materialized projection from SQLite** and renders it. The briefing is not a RAM-ranked event feed and does not combine four memory tiers. Ollama, the graph, query text, and archive do not choose its contents; a RAM working set currently supplies the daemon session ID.

The response can contain `session_id`, `notice`, standing `constraints`, `passed_checks`, `open_work`, `focal_task`, `other_active_tasks`, `estimated_tokens`, and `truncated`. Empty optional fields are omitted. It does not return the old `recent_activity`, `relevant_history`, `related`, or `archived` fields.

A focal task groups intent, reality, decisions, ruled-out approaches, unknowns, next action, and artifacts. Previously failing checks that now pass get a short pass line; routine commands that never failed do not fill the briefing. The default 1,500-token target trims optional detail using a byte-length estimate (one token per three bytes, rounded up). Protected claims can exceed the target. This is not an exact tokenizer count or a hard serialized-size guarantee.

## Claims and handoff extraction

[claim.rs](../crates/core/src/claim.rs) validates the write contract:

- User intent and user-stated constraints/decisions/ruled-out approaches use the CLI/socket user path.
- MCP `note_claim` records agent `inferred` or `hypothesis` claims, never user constraints, intent, or `observed` facts. Unknowns are hypotheses. This direct authored-claim path has no automatic tool evidence and does not run Component 2.1 extraction/grounding; it validates the core origin/kind/confidence contract.
- Observed command outcomes belong to the projector. An agent conclusion passing extraction remains an inferred claim.
- Claims carry provenance; consumers still need to check them against the repository and current task.

[extract.rs](../crates/broker/src/extract.rs) runs locally without a model at daemon startup, after a note, on explicit `handoff`, and before `context_now`. `observe` records tool results and notes; the following handoff admits supported candidate claims through the existing validator and rebuilds the projection. `note` records raw text; a note is not automatically a claim.

Tool observations require a command and exit code, with optional cwd and output. Output is bounded to 200 lines / 8,000 characters with a truncation marker. Conclusions can cite an observation's returned `event_id` using `support_event_id`. Unmarked or unsupported prose may be skipped. Retractions and updates are scoped to eligible authored claims, not projector-owned observed outcomes.

The in-progress Component 2.1 rule checks conclusion clauses for evidence support, contradictions, order, and unsupported vocabulary. It can preserve a supported clause while dropping an unsupported one. It can also drop useful paraphrases. Passing these rules is not a semantic proof. See [status](status.md) and the [latest validation](component-2.1-validation.md).

## Enrichment, history, and older storage layers

[Enrichment](../crates/background/src/enrichment.rs) uses a bounded queue with database backlog sweeps. Use-case classification, content summaries, memory classification, and retention decisions are deterministic rules. No instruct model is called. Optional Ollama `/api/embed` requests produce vectors; unavailable embeddings do not prevent event recording or rule-based processing.

- `search_context` merges live-event semantic matches with literal payload substring matches.
- `recall_similar` uses semantic neighbors when an embedding is available and literal matches as its fallback.
- Both report a `semantic` flag indicating whether a query embedding was available; it does not prove that every vector lookup succeeded. Limits default to 10 and clamp to 1–50.
- Neither search tool searches compressed archive segments or the graph.

The older four-tier infrastructure still exists: RAM working set, live SQLite/vector storage, persisted `petgraph` graph, and zstd archive. That does not mean all four participate in the briefing. The graph is maintained and flushed; the current broker does not use it for briefing/history results. The archive has internal read APIs but no agent-facing archive search.

The pruner runs hourly, selecting batches of up to 1,000 eligible rows older than seven days with score below 0.5. It excludes durable semantic/procedural memories and protects claim events, active-task opening events, and current evidence. It archives before deleting live rows. `max_memory_mb` does not drive pruning and is not enforced.

## Boundaries and configuration

Defaults use XDG user directories and an owner-only (`0600`) Unix socket. The default embedding endpoint is `http://localhost:11434`, configurable through `ollama_url`; embeddings must have 768 dimensions. `CONTEXTD_EMBEDDING_MODEL` overrides the configured model. The daemon passes its configured URL explicitly, so `OLLAMA_HOST` does not override that URL.

These are local defaults, not an end-to-end guarantee that context never leaves the machine. A configured remote embedding endpoint receives embedding inputs, and a consuming agent may send the briefing to an external model. Same-user processes can access the socket and CLI; the user/agent distinction is a provenance convention, not process isolation. There is no general secret-redaction or per-client authorization layer.

One daemon watches one root. Shell/Git socket emitters and process polling have their own scope; `watch_root` is not an isolation boundary for all activity. Setup behavior, actual paths, and cleanup limits are in [build and setup](build-walkthrough.md).

## Workspace map

| Path | Responsibility |
|---|---|
| `bin/contextd` | Daemon wiring, query handling, CLI |
| `crates/core` | Config, event/request schema, claim types and validation |
| `crates/sources` | Producers, socket listener, hook/service generation |
| `crates/pipeline` | Scoring, classification, content processing, decisions |
| `crates/store` | SQLite, vectors, projection persistence, archive |
| `crates/memory` | Deterministic projection, working set, graph |
| `crates/broker` | Briefing rendering, extraction/admission, history search |
| `crates/mcp` | `rmcp` stdio tools and resource, daemon client |
| `crates/ai` | Optional Ollama embedding client |
| `crates/background` | Enrichment, backlog sweeps, archive pruning |
| `eval/component0` | Episode fixtures, local tests, optional live-agent evaluation harness |
