# Component 2 status

Updated 3 November 2026 after the live handoff was wired, on the working tree whose parent is `c397bef`. The morning audit of that commit, which found an offline quoter and no daemon integration, is the gap this change closes. No commit was made.

## What this answers

> Can a real agent's tool results and conclusions enter the daemon, become validated claims, survive a restart, and show up in `context_now` for a fresh agent, without fabricated or dropped state?

Tool results and conclusions can. They enter through `observe`, not through the interactive shell hook. The handoff quotes them, the Component 1 gate admits them, and the claim rows are what a restart folds. `context_now` renders that fold. A sentence the cited output does not support is not admitted. An explicit retract supersedes the claim it quotes and leaves the others. On today's limiter-handoff packet, run `component2-2026-11-03` (25/25, prompt `81030c81a3caba32…`), the successor reached the frontier in mean 3.20 turns and 121,386 tokens, against 5.80 turns and 172,197 tokens for Component 1. Paired turns −2.6 [−3.0, −2.2], paired tokens −50,811 [−73,170, −28,453]. Constraint violations 0, forbidden actions 0. The 2 November matrix used a different Component 2 prompt and is not this result.

## Actual execution flow

```text
observe (tool)  →  shell event with command, exit, cwd, output
observe (message / user / retract / update)
contextd note   →  source:note
shell hook      →  command, exit, cwd, and still no stdout
        │
        ▼
SQLite events
        │
        ├─ shell with an exit code  →  memory::project::rebuild
        └─ note or observe-note     →  stored; handoff runs after a note
        │
        ▼
run_handoff     startup, after a note, and before context_now
        │         get_handoff_events (shell, note, claim)
        │         extract::apply_extraction
        │         insert source:claim, then rebuild
        ▼
get_fold_events   still WHERE source IN ('shell', 'claim')
        │
        ▼
fold              shell exits and claim events only
        │
        ▼
context_now       broker::snapshot of that projection
```

`crates/broker/src/extract.rs` is the quoter and the gate. `project_claims` either admits a Python candidate file or, with `--extract`, runs that quoter. `eval/component0/harness/agent_observe.py` is the only adapter. It maps a trace of tool results and messages onto those events. It does not read `history.jsonl`.

`get_fold_events` does not load notes. A note still cannot open a task. That is covered by `note_event_does_not_open_a_task` and by `a_restart_rebuilds_the_same_snapshot`, which asserts the fold input has no notes after the claims exist.

## Feature status

| Requirement | Status | Evidence |
|---|---|---|
| Trajectory observation | Live for a coding agent that the adapter can see | `observe` stores the tool output. The shell hook still does not |
| Claim extraction | Speech acts, plus conclusions the adapter marked | Parity test on limiter-handoff. Natural episode admits 8 claims and skips the ungrounded one |
| Evidence verification | The gate checks ids, the verbatim span, and reality overlap | 0 rejected on both episodes. Realities cite the note and the shell event |
| Confidence | Extracted claims are stated, inferred, or hypothesis | `an extracted claim cannot be marked observed`. The exit line stays observed / projector |
| Dedup | Same kind and collapsed text, including a second handoff | The restart test's second handoff accepts nothing |
| Supersession | Explicit retract and update. No silent overwrite | Contradictory conclusions both stay until the retract names one. An ungrounded update leaves the old claim |
| Integration with Component 1 | Same fold and render. Notes stay out of the fold | Workspace tests exited 0. Focal id on limiter-handoff unchanged |
| Live automatic construction | Handoff at startup, after a note, and before `context_now` | `bin/contextd/src/main.rs` |
| Fresh agent reads the claims | `context_now` renders the rebuilt projection | The restart test compares the snapshot before and after reopen |

## Tests

```bash
cargo test --workspace --all-features
```

Exited 0. Broker lib tests are 62, including the six handoff tests in `extract.rs`. The ignored test is still `ai::ollama::tests::test_generate_embedding`.

```bash
cd eval/component0
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest tests -q --tb=line -p pytest
```

256 passed. That includes `test_arms_check_passes` with the generated Component 2 packet present. `test_generated_component2_packet_stays_out_of_the_freeze` creates the packet when it is missing and still requires a tampered oracle hash to fail. `test_rust_extractor_matches_python_on_the_episode` and `test_natural_trajectory_admits_only_grounded_claims` call `project_claims`.

CI (`.github/workflows/ci.yml`) runs the workspace cargo suite and then `test_extract_claims.py`, `test_import_trajectory.py`, `test_arms.py`, and `test_natural_trajectory.py`.

## Packet scores (3 November)

tiktoken `o200k_base`.

| Arm | Tokens | Required recall | Unit precision | Stale as current | Stale marked |
|---|---|---|---|---|---|
| none | 0 | 0 | — | 0 | 0 |
| summary | 170 | 0.14 | 0.75 | 0 | 0 |
| contextd-new | 318 | 0 | 0 | 0 | 0 |
| contextd-component2 | 1,032 | 1.00 | 0.90 | 0 | 1 |
| oracle | 381 | 1.00 | 1.00 | 0 | 1 |

Retry-budget, natural sentences, no speech-act prefixes: 503 tokens, required recall 1.00, unit precision 0.875, stale-as-current 0. The unmatched unit on both pages is the projector's `exited 1`.

## Agent matrix

`runs/component2-2026-11-02` is present and is not today's result: its Component 2 prompt does not match the 1,032-token packet. The matching run is `runs/component2-2026-11-03`, 25/25 scored, prompt `81030c81a3caba32895b737cb90cdfa29ffe5f09f3d85bea4fac3d326aba964f`.

| Arm | Solved | Mean turns | Repeated | Forbidden | Constraint | Mean tokens |
|---|---|---|---|---|---|---|
| none | 1.00 | 11.40 | 0 | 0 | 0 | 232,248 |
| summary | 1.00 | 4.00 | 0 | 0 | 0 | 200,962 |
| oracle | 1.00 | 3.00 | 0 | 0 | 0 | 70,512 |
| contextd-new | 1.00 | 5.80 | 0 | 0 | 0 | 172,197 |
| contextd-component2 | 1.00 | 3.20 | 0 | 0 | 0 | 121,386 |

Component 2 minus Component 1, paired: turns −2.6 [−3.0, −2.2], tokens −50,811 [−73,170, −28,453]. Versus the oracle, turns +0.2 [−0.4, +0.8] and tokens +50,874 [+38,389, +63,152]. The command and the image pins are in `docs/component-2-validation.md` section 4.

## Remaining limitations

1. The interactive shell hook still has no stdout. Grounded claims need `observe` or the trajectory importer.
2. A note that is not a speech act and is not marked `role: conclusion` is skipped. The adapter sets that role because it saw an agent message.
3. The natural cues are "I'll" / "I will", "might" / "maybe" / "not sure" / "could be", and "not the cause" / "not involved" / "not the problem", plus a two-word overlap for a reality. Other wordings abstain.
4. Dedup is not semantic. Two different sentences can both be current.
5. The next-action slot still replaces the frontier. That is the Component 1 rule. Other kinds are not replaced unless the later note quotes them.
6. The retry-budget episode was not run through Codex. Its result is a packet score and a restart test.
7. No paging or compaction. The limiter-handoff page is 1,032 tokens against an oracle of 381.

## How to re-check

```bash
cd /home/vansh5632/contextd
cargo test --workspace --all-features

cd eval/component0
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest tests -q --tb=line -p pytest
.venv/bin/python -m harness.arms check
.venv/bin/python -m harness.extract_claims --episode limiter-handoff
```

`arms check` passes while `packets/contextd_component2.txt` exists. Packet scores come from `harness.packet_score` over `packets/contextd_new.txt`, `packets/contextd_component2.txt`, `packets/oracle.txt`, and `packets/summary.txt`.
