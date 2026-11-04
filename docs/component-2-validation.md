# Component 2 validation

Measured on 2 November 2026. The numbers in this note are the result. The generated briefing and the agent traces stay on the machine that ran them.

This note answers the question Component 1 left open:

> Can contextd automatically preserve what an agent learned, rather than merely what commands it executed, and does that measurably reduce rediscovery for the next agent?

**On this episode, yes for what was said, and yes for rediscovery against Component 1 and against no briefing. It does not reach the oracle's token cost, and it does not yet see a live shell.**

The predecessor is the scripted Session A, not a live model. Every required gold atom is a sentence in that session's notes. The extractor quotes those sentences. It does not read `gold/atoms.yaml` or `oracle.txt`. A fresh agent given the resulting page solved 5/5, broke no constraint, and used fewer turns and fewer tokens than the Component 1 page and than no page. Mean tokens were 113,018 against 188,948 for Component 1, 209,490 for no briefing, and 68,296 for the oracle.

---

## 1. What was tested first, with no agent

`python -m harness.extract_claims` reads `history/history.jsonl` and the 31-event replay log. `project_claims` admits candidates and renders them with `memory::project::fold` and `broker::snapshot::render`. The same focal task id as Component 1 comes back: `t_a58f76e3ca4078e8a849ed9682f450cc`. The pytest reality is still `exited 1`, confidence `observed`, origin `projector`. Nine candidates were admitted. Zero were rejected.

| Claim | Kind | Confidence | Evidence |
|---|---|---|---|
| Keep the public signatures and `handle_request` | constraint | stated | `history:A:8` |
| No new dependencies | constraint | stated | `history:A:8` |
| Mock backend ruled out | ruled_out | inferred | `history:A:12` |
| Lock and synchronization ruled out | ruled_out | inferred | `history:A:16` |
| Create branch counts only non-idle connections | reality | inferred | `history:A:18`, `history:A:17` |
| Redis counter rejected | ruled_out | inferred | `history:A:20` |
| Idle connections count toward the cap | decision | inferred | `history:A:21` |
| Keepalive cleanup left out of scope | unknown | hypothesis | `history:A:23` |
| Fix the create branch, then `scripts/unasync.py` | next_action | inferred | `history:A:24` |

Skipped, and checked in `packets/contextd_component2_claims.json`:

- Units 1 and 3, narration, no speech act.
- Units 9, 13, and 19, open hypotheses (mock suspect, lock suspect, Redis idea). They are not current.
- Units 25, 28, 30, 32, 35, and 42, Session B. That session has no command that is still failing.

A commands-only copy of the history extracts nothing. A note that names a database after a `sed` whose output does not contain those words is not a claim. A repeated decision is kept once. No extracted claim is `observed`.

Packet score against the real gold, tiktoken `o200k_base`, before any agent:

| Arm | Tokens | Required recall | Unit precision | Stale as current | Stale marked |
|---|---|---|---|---|---|
| none | 0 | 0 | — | 0 | 0 |
| summary | 170 | 0.14 | 0.75 | 0 | 0 |
| contextd-new (Component 1) | 318 | 0 | 0 | 0 | 0 |
| contextd-component2 | 1,030 | 1.00 | 0.90 | 0 | 1 |
| oracle | 381 | 1.00 | 1.00 | 0 | 1 |

The missing tenth of precision is the projector's pytest line, `exited 1`. It matches no gold atom. It is the observed failure, not an invented fact. Token precision on the same packet is 0.94. The Redis line is the one stale atom, and it is marked rejected. The keepalive note is present as a hypothesis. The host-header notes from Session B are absent. Daemon estimate of the packet is 1,042 tokens, under the 1,500 budget, so nothing was trimmed.

The admission binary finishes in well under a tenth of a second. It makes no model call. Extraction overhead on the predecessor's run is zero.

`cargo test -p contextd-core -p memory -p broker` passed (the projector and the claim rules). `pytest` on the extraction, arm, packet, and matrix tests passed, 81 tests. `harness.arms check` passed. The frozen `packets/contextd.txt` hash is unchanged.

---

## 2. Agent protocol

From `eval/component0`, after image preflight (all six pins matched the Component 1 run: agent `d51cf876163c`, scorer `1fee64413fff`, egress `de6801f2858f`, replay `6dd6ed88d4d6`, ollama `2a6e883b917f`, model `0a109f422b47`):

```bash
set -a && source /home/vansh5632/contextd/.env && set +a
.venv/bin/python -m harness.run_matrix \
  --run-id component2-2026-11-02 \
  --reps 5 \
  --arms none,summary,oracle,contextd-new,contextd-component2 \
  --i-accept-spend
.venv/bin/python -m harness.report runs/component2-2026-11-02
```

Forced placement. The agent does not call MCP. Model `gpt-6.1-sol`, Codex `0.159.3`, seed `20260930`, five reps, per-trial cap 2,000,000, matrix cap 40,000,000, timeout 1800s. Workspace tar prefix `f832caec9bc28c16`. G3, G4, and G5 passed. The matrix ran from 2026-11-02T15:36:06Z to 15:56:35Z, 25/25 scored, 3,895,005 input-plus-output tokens.

`contextd-new` is the Component 1 observation-only briefing already frozen in `packets/contextd_new.txt`. The report name is the arm name.

---

## 3. Agent results

Harness report: `runs/component2-2026-11-02/report.md`. Real gold. n=5. Rates are shares of trials.

| Arm | Solved | ttf median | ttf mean | Repeated | Forbidden | Constraint | Packet recall | Packet precision | Mean tokens | Mean wall |
|---|---|---|---|---|---|---|---|---|---|---|
| none | 1.00 | 11 | 10.60 | 0.20 | 0.00 | 0.00 | 0.00 | — | 209,490 | 45.1s |
| summary | 1.00 | 3 | 2.80 | 0.20 | 0.00 | 0.00 | 0.14 | 0.75 | 199,248 | 50.4s |
| oracle | 1.00 | 2 | 2.60 | 0.00 | 0.00 | 0.00 | 1.00 | 1.00 | 68,296 | 20.1s |
| contextd-new | 1.00 | 5 | 4.60 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 188,948 | 39.6s |
| contextd-component2 | 1.00 | 3 | 3.20 | 0.00 | 0.00 | 0.00 | 1.00 | 0.90 | 113,018 | 29.5s |

Failure tags: Component 2 and the oracle are `ok` on all five reps. Component 1 and the summary are `placement` on all five, which is the missing required atoms, not a wrong edit. `none-r2` is the one `behaviour` tag, from one repeated-work call. No trial recorded a constraint violation or a forbidden action. Hidden pool-cap tests passed on the Component 2 reps, including the keepalive-unchanged checks and the unasync regeneration check (`contextd-component2-r1/score.json`).

Paired deltas versus `none` in the same rep, from the harness report. Bootstrap 95% CI, seed 20260930, 10,000 resamples.

| Arm | Turns to frontier | Tokens |
|---|---|---|
| summary | −7.80 [−9.20, −6.20] | −10,242 [−36,615, +13,524] |
| oracle | −8.00 [−9.20, −6.40] | −141,194 [−155,611, −126,609] |
| contextd-new | −6.00 [−8.40, −4.00] | −20,542 [−49,294, +5,929] |
| contextd-component2 | −7.40 [−8.60, −6.20] | −96,472 [−128,406, −64,538] |

Component 2 against Component 1, same pairing method, arm minus `contextd-new`:

| Metric | Per rep | Mean | 95% CI |
|---|---|---|---|
| Turns to frontier | −3, −1, −2, −2, +1 | −1.4 | [−2.4, −0.2] |
| Tokens | −122,727, −114,657, −34,665, −50,132, −57,467 | −75,930 | [−106,594, −45,412] |

Every rep used fewer tokens than Component 1. Four of five reached the frontier sooner. Rep 5 took one more turn (3 against 2) and still used 57,467 fewer tokens.

Component 2 against the oracle:

| Metric | Mean | 95% CI |
|---|---|---|
| Turns to frontier | +0.6 | [−0.6, +1.8] |
| Tokens | +44,722 | [+25,786, +62,721] |

The turn gap to the oracle is not distinguishable at n=5. The token gap is. Mean tokens before the frontier were about 8,300 for Component 2 and about 7,630 for the oracle, against about 12,300 for Component 1 and about 65,600 for no briefing. The saving versus Component 1 is mostly after arrival: the page names the decision and the edit, and the successor does less rereading. It does not get as small as the 381-token human note.

---

## 4. Completion criteria

| Criterion | Result |
|---|---|
| Extracts useful claims from observable activity | Pass. Nine claims from Session A's notes and one grounded file read. Commands alone extract nothing |
| Preserves meaning, confidence, and evidence | Pass. Texts are verbatim spans. The finding is `inferred`, not `observed`. Each evidence id resolves to the note or the cited output |
| Updates working state without fabricating | Pass. The gate rejected nothing. Open suspects are absent. The Redis idea is absent and the rejection is marked. The pytest failure stays focal and stays `observed` |
| Improves on Component 1 without a constraint regression | Pass on this episode. Paired tokens −75,930 and turns −1.4, both intervals exclude zero. Constraint-violation rate 0. Forbidden-action rate 0. Repeated-work rate 0 |
| Closes the gap to the oracle | Not on tokens. The interval versus the oracle excludes zero, about 45,000 tokens still above it |

---

## 5. What this does not show

The predecessor's notes were written as the claims. A live agent that never says "Decision" or "Ruled out" would not yield this packet. The deterministic pass abstains on those trajectories, and the LLM pass from the design was not built.

The shell hook still does not store notes or stdout. This run read `history.jsonl`, which the benchmark already kept. A daemon that only has the 31 shell events still has required-atom recall 0, because that is what the commands-only test extracts.

One episode, five reps, one successor model. The token interval versus Component 1 is wide. It does not include zero. It is not a claim about other tasks.

The placed page is 1,030 tokens because the quotes are whole notes. A shorter rendering of the same claims might move the total toward the oracle. That would be a render change, and it was not part of this prototype.

---

## 6. Answer

contextd can preserve what this predecessor said, as typed claims with a confidence and a pointer back to the note or the tool output, and a later agent spends less of the investigation rediscovering it. The preservation is a quote of the trajectory, checked by the claim rules Component 1 already had. It is not a summary, and it is not a reading of the gold file. The human note is still the smaller page, and a live shell still does not carry the sentences the quote is made from.

## 7. Live handoff, 3 November 2026

Parent of the work is `c397bef`. Packet scores were recomputed with tiktoken `o200k_base` after `write_packet`. The Component 2 page is 1,032 tokens, daemon estimate 1,015, required recall 1.00, unit precision 0.90, stale-as-current 0, stale marked 1. The unmatched unit is the projector's `exited 1`. `none` 0, `summary` 170 / 0.14 / 0.75, `contextd-new` 318 / 0 / 0, `oracle` 381 / 1.00 / 1.00. The Rust quoter matches the Python proposer on kind, text, and confidence.

`episodes/retry-budget` has no `Decision:` or `Ruled out:` prefixes. The adapter plus `project_claims --extract` admits 8 claims and 1 retract, rejects 0, skips the closed session, the session with no command, and the ungrounded sentence. Required recall 1.00, 503 tokens, unit precision 0.875. The retracted sentence is not current. A file-backed restart rebuilds the same snapshot; a second handoff inserts nothing.

The 2 November agent prompt for this arm was `2bacd5d2b8b53764…`. Today's prompt is `81030c81a3caba32895b737cb90cdfa29ffe5f09f3d85bea4fac3d326aba964f`, so that run is not this packet. The matching run is `component2-2026-11-03`, 25/25, 2026-11-03T17:19:19Z to 17:42:13Z, 3,986,524 tokens, same image pins and seed as section 2.

| Arm | Solved | Mean turns | Constraint | Forbidden | Repeated | Mean tokens |
|---|---|---|---|---|---|---|
| none | 1.00 | 11.40 | 0 | 0 | 0 | 232,248 |
| summary | 1.00 | 4.00 | 0 | 0 | 0 | 200,962 |
| oracle | 1.00 | 3.00 | 0 | 0 | 0 | 70,512 |
| contextd-new | 1.00 | 5.80 | 0 | 0 | 0 | 172,197 |
| contextd-component2 | 1.00 | 3.20 | 0 | 0 | 0 | 121,386 |

Component 2 minus Component 1, paired, seed 20260930, 10,000 resamples: turns −2.6 [−3.0, −2.2], tokens −50,811 [−73,170, −28,453]. Versus the oracle: turns +0.2 [−0.4, +0.8], tokens +50,874 [+38,389, +63,152]. `cargo test --workspace --all-features` exited 0. `pytest tests` in `eval/component0` passed 256.
