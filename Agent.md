# Agent guidance

This file is the repository entrypoint for coding agents. Read [docs/README.md](docs/README.md), [current architecture](docs/architecture.md), and [current status](docs/status.md) before changing behavior. Read [CONTRIBUTING.md](CONTRIBUTING.md) and [TESTING.md](TESTING.md) for workflow and checks.

## Working rules

- Follow the current source and request/claim contracts; historical research and designs are context, not a frozen implementation mandate.
- Keep the daemon local-first. The only current model integration is optional Ollama embeddings. Do not add cloud runtime dependencies without an explicit request.
- Preserve ingestion when embeddings are unavailable. Classification, summaries, and handoff extraction currently use rules.
- Keep evidence, origin, and confidence intact. Agent-inferred claims must not become user-stated constraints or projector-owned observed facts.
- Preserve existing user work, including untracked research and evaluation artifacts. Do not overwrite measurements or call them fresh verification.
- Use the smallest complete change and run checks appropriate to it. Do not run paid/live-agent evaluation without explicit authorization.
- Keep generated runs and agent plans out of commits; CI checks added paths under `eval/component0/runs/` and `docs/superpowers/`.

The current briefing loads SQLite materialized task/claim state. The older RAM, graph, and archive infrastructure is not a four-tier retrieval path for `context_now`. There is no editor-event producer. Component 2.1 remains in progress; see the linked guides instead of duplicating its architecture here.

[claude.md](claude.md) adds Claude-specific entrypoint guidance. The repository's [LICENSE](LICENSE) is PolyForm Noncommercial 1.0.0; Cargo metadata currently differs.
