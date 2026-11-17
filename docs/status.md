# Current status and limits

Checked against source and the existing reports on **10 November 2026**. This page distinguishes implemented behavior from recorded evaluation evidence. No live-agent benchmark was rerun for the documentation refresh.

## Implemented

- Linux daemon, XDG configuration, shell/Git hooks, filesystem and process sources, and a systemd user unit.
- SQLite event storage, deterministic task/claim projection, compact current briefing, claim authorship and confidence, replacement/retraction, and explicit task completion.
- Five MCP tools plus `context://snapshot/current`, served by a stdio bridge to the Unix socket.
- `observe` for agent tool output and conclusions, `note` for raw notes, and local handoff extraction/admission.
- Rule-based enrichment, optional 768-dimensional Ollama embeddings, literal/semantic live-event history search, persisted graph, and compressed archive storage.

See [architecture](architecture.md) for scope and [setup](build-walkthrough.md) for commands. The presence of graph/archive infrastructure does not make it part of the current briefing or searchable history.

## Component 2.1 is not frozen

The latest working-tree change checks conclusion clauses against their cited tool output. It rejects more unsupported causes that merely share a real identifier and can keep the supported part of a mixed sentence. It calls no model and leaves Component 1's fold and renderer unchanged.

The [10 November validation](component-2.1-validation.md) records:

- Pool packet required-atom recall 1.00, unit precision 0.90, and stale-as-current 0. Useful keepalive/eviction wording was dropped because it was not licensed by the cited output under the new vocabulary rule.
- On five paired pool-bug reps, the Component 2.1 page solved 5/5 with no recorded constraint violation or forbidden action. Mean turns were 4.40 and mean tokens 120,734, versus 2.80 and 97,957 for the 9 November automatic page in the earlier recorded comparison.
- Against Component 1 in the latest run, the paired turn interval includes zero; the paired token interval excludes zero. This does not establish a general improvement in turns.
- The retry-budget page was unchanged, so its live-agent matrix was not repeated. The [9 November evidence](component-2-evidence-validation.md) remains the recorded result for that page.

The remaining problem is preserving useful, justified paraphrases without admitting a new unsupported mechanism beside a real identifier. These small episode-specific runs do not prove general correctness or broad agent compatibility. Admitted agent conclusions remain inferred claims, not verified facts.

The research, design, and validation chain is indexed in [docs/README.md](README.md). Earlier reports describe their own snapshots and may have limits that subsequent work addressed.

## Current limits

| Area | Limit |
|---|---|
| Automatic capture | No editor producer. Shell hooks record command/cwd/exit only; tool output requires `observe` or an explicit producer. |
| Task identity | Shell tasks are keyed by cwd and normalized command. Different flags can leave separate failures; unrelated commands are not semantically grouped into a task. |
| Extraction | Heuristic clause grounding and exact-text deduplication; unsupported or unmarked prose can be skipped, and valid paraphrases can be lost. |
| Briefing | Reads SQLite materialized tasks/claims, can run extraction before replying, and uses estimated token trimming. No RAM-only latency guarantee. |
| History | Live events/vectors only. Archive and graph are not searched or injected into the briefing. |
| Resource use | `max_memory_mb` is accepted but unenforced. Backlogs and the persistent archive can grow. |
| Delivery | The bounded sensor bus can lag and lose events. Hook delivery is best effort. |
| Scope | One watched root; shell hooks/process observation are not a strict per-project boundary. |
| Privacy | Local defaults; configurable embedding endpoint and external consuming agents. No general secret redaction or per-agent isolation. |
| Packaging | Native Rust release build, not a verified static or cross-platform distribution. Linux/systemd is the documented path. |
| Cleanup | Uninstall removes shell block/unit only; it does not stop the service, remove Git hooks, binary, config, or data. |
| Licensing | `LICENSE` is PolyForm Noncommercial 1.0.0; Cargo metadata still disagrees. |

## What counts as current evidence

[TESTING.md](../TESTING.md) documents reproducible local checks and the separate evaluation harness. Dated validation reports retain their original numbers and command history. Generated traces and run reports may be absent from a checkout. Do not turn those historical results into a claim that the current dirty tree passed a fresh run.
