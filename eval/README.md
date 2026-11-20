# contextd product eval

Data-driven none / dump / MCP evaluation. Layer 0 talks to a real `contextd` binary over an isolated Unix socket and scores gold needles. Layer 1 (Cursor Agent CLI) is optional and never required in CI.

## Quick start

```bash
cargo build -p contextd
pip install -r eval/requirements.txt
python3 -m unittest eval.harness.test_harness
python3 -m eval.harness --layer 0 --ci
```

Never point this at your real daemon. Every run sets `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, and `XDG_RUNTIME_DIR` to a temp tree.

## What an episode is

One YAML file under `eval/episodes/`. The runner does not special-case task types. Fields:

- `history` — sessions with `offset` (`-26h`, `-4h`, `-15s`), optional `intent`, `events`, and/or `generate` for noise
- `gold.required` / `gold.forbidden` / `gold.frontier` / `gold.decisions`
- `l0_queries` — defaults to `[{query: now}]`. Search/recall `text` is a **literal** `LIKE` needle (the whole string must appear in a payload). The harness also searches each gold required/frontier/decision string.
- `restraint: true` — negative control; prior-task needles listed in `forbidden` must not appear in `now`
- `requires_embeddings: true` — skipped by `--ci`; marked **invalid** (not failed) if `semantic` is false
- `repo.kind: git` — cloned only when `EVAL_AGENT=1`; layer 0 never clones

Gold facts stay out of the fixture checkout. Injected `note` events are how decisions and ruled-out paths get into the daemon without leaking into the repo.

## Adding an episode

1. Copy a small YAML in `eval/episodes/l2/`.
2. Keep hidden facts in `history` notes, not in `eval/fixtures/`.
3. Run `python3 -m eval.harness --episode your-name --layer 0`.
4. If layer 0 fails, do not spend agent tokens.

## Layer 1 (optional)

```bash
EVAL_AGENT=1 python3 -m eval.harness --layer 1
```

Requires the Cursor `agent` CLI on `PATH` (`agent status` should show you logged in). Arms are `none` (fixture only), `dump` (snapshot stuffed into the prompt), and `mcp` (`contextd mcp` with the isolated XDG env, via a per-arm `.cursor/mcp.json`). The agent process keeps your real Cursor login; only the MCP child inherits the eval daemon's XDG dirs.

Optional: `EVAL_AGENT_MODEL` (default `auto`), `EVAL_AGENT_TIMEOUT` (seconds, default 300), `CURSOR_AGENT_BIN`. CI never sets `EVAL_AGENT`.

## Reports

`eval/reports/latest.jsonl` and `eval/reports/latest.md` after each run. That directory is gitignored.
