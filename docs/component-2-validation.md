# Component 2 validation

Measured again on 3 November 2026, on the working tree whose parent commit is `c397bef`. Packet scores below were computed that day against the packets in the tree. The 2 November agent matrix used a different Component 2 prompt and is not reused as the result for this packet.

This note answers:

> Can contextd automatically preserve what a real agent learns during execution and use that knowledge to help a completely fresh agent continue with less rediscovery, without fabricating or losing important state?

**On the two measured trajectories, yes for preservation. Quotes are typed, evidenced, and reconstructed after a restart, and `context_now` renders them. On limiter-handoff the page recalls every required atom. The unmatched unit is the projector's exit line, which Component 1 also shows, and the successor matrix does not add a constraint violation or a forbidden action. A fresh agent given that page reached the frontier in fewer turns and fewer tokens than the Component 1 page, in the 3 November run `component2-2026-11-03`. It does not reach the oracle's token cost.**

---

## 1. Packet score, limiter-handoff, no agent

Command, from `eval/component0`, after `write_packet` regenerated the gitignored Component 2 files:

```bash
.venv/bin/python -c "
from harness.extract_claims import write_packet
from harness.common import EPISODES_DIR
from harness.packet_score import load_atoms, score_packet, split_units, PACKET_KINDS
from harness.arms import packet_text
import tiktoken
episode = EPISODES_DIR / 'limiter-handoff'
write_packet(episode)
"
```

Tokenizer `o200k_base`. Gold `episodes/limiter-handoff/gold/atoms.yaml`, seven recall atoms. The extractor does not read that file.

| Arm | Tokens | Required recall | Unit precision | Token precision | Stale as current | Stale marked | Unmatched unit |
|---|---|---|---|---|---|---|---|
| none | 0 | 0 | — | — | 0 | 0 | — |
| summary | 170 | 0.14 | 0.75 | 0.81 | 0 | 0 | the unmatched summary lines |
| contextd-new (Component 1) | 318 | 0 | 0 | 0 | 0 | 0 | both units |
| contextd-component2 | 1,032 | 1.00 | 0.90 | 0.94 | 0 | 1 | `[reality] … exited 1` |
| oracle | 381 | 1.00 | 1.00 | 1.00 | 0 | 1 | none |

Nine claims admitted, zero rejected. Focal task `t_a58f76e3ca4078e8a849ed9682f450cc`. Daemon `estimated_tokens` is 1,015, under the 1,500 budget, so nothing was trimmed. The unmatched tenth is the projector's observed pytest line. It is not a sentence the extractor invented. The Redis rejection is the one stale atom, and it is marked. The keepalive note is present as a hypothesis. Host-header notes from Session B are absent.

The Rust quoter and the Python proposer emit the same kind, text, and confidence on this episode (`test_rust_extractor_matches_python_on_the_episode`). Commands alone still extract nothing.

These token counts match the 3 November status audit's fresh render (1,032 / 1,015), not the 2 November note's 1,030 / 1,042.

## 2. Natural trajectory, no preformatted speech acts

Episode `eval/component0/episodes/retry-budget`. The input is `trajectory.jsonl` in the adapter's shape (tool result, agent message, user message, retract). No line starts with `Decision`, `Ruled out`, `Rejected`, or `Next step`. `agent_observe.adapt_trace` writes shell events with output and notes with `role: conclusion`. `project_claims --extract` admits them. The gold file is read only by `score_packet` after the packet exists. A leak marker lives only in `gold/leak_marker.txt`; the trajectory, the adapter, and the extractor do not contain it.

| | |
|---|---|
| Admitted | 8 claims plus one retract. 0 rejected. 3 skipped |
| Skipped | Session B (every command exited 0), Session C (no command), and "The database pool is exhausted…" (no overlap with the cited output) |
| Retract | "The first send is not counted…" was admitted, then superseded. It is not current |
| Tokens | 503 tiktoken, daemon estimate 499 |
| Required recall | 1.00 (6 recall atoms) |
| Unit precision | 0.875 (8 units) |
| Stale as current | 0 |
| Unresolved present | true (the "might be involved" hypothesis) |
| Unmatched unit | `[reality] python -m pytest -q tests/test_retry.py exited 1` |

Current page, and nothing else from the trajectory:

- reality, inferred: retry_count 2 where the test expects 1. Evidence is the note and the pytest output.
- reality, inferred: deliver increments retry_count before open_socket. Evidence is the note and the `sed` output.
- ruled_out, inferred: the timeout path is not the cause.
- unknown, hypothesis: a shared counter might be involved.
- constraint, stated: keep the signature. Constraint, stated: do not add a dependency.
- next_action, inferred: I'll move the increment…
- reality, observed, projector: the pytest exited 1.

The contradictory conclusion stayed current in a run that stopped before the retract, and was superseded once the retract note was in the log. An update whose new sentence does not overlap the cited output does not supersede the claim it names (`an_ungrounded_update_does_not_supersede`).

`project_claims --extract` on this trajectory took 0.095s with a warm binary, including process start. No model call.

## 3. Restart and context_now

`extract::tests::a_restart_rebuilds_the_same_snapshot` writes a failing shell event with output, a conclusion note, and a user constraint note into a file-backed database, runs `run_handoff`, drops the store, opens it again, and rebuilds from the log. The rendered snapshot is the same after a second handoff, which inserts nothing. The conclusion is inferred, with evidence ids. The constraints are stated and user-origin. The exit line is the only observed claim. `get_fold_events` still returns no notes, so the projector's selection contract is unchanged. `cargo test --workspace --all-features` exited 0 on this tree. `pytest tests` in `eval/component0` passed 256 tests.

The daemon calls the same `run_handoff` at startup, after `contextd note`, and at the start of `context_now`. The test drives the store those calls use.

## 4. Agent matrix

The 2 November run `component2-2026-11-02` is on disk. Its Component 2 prompt hash is `2bacd5d2b8b53764…`. Today's prompt is `81030c81a3caba32895b737cb90cdfa29ffe5f09f3d85bea4fac3d326aba964f`. `none`, `summary`, `oracle`, and `contextd-new` still match that older run. The Component 2 arm does not, so the 2 November agent totals are not a measurement of this packet.

The run that matches this packet:

```bash
set -a && source /home/vansh5632/contextd/.env && set +a
cd eval/component0
.venv/bin/python -m harness.run_matrix \
  --run-id component2-2026-11-03 \
  --reps 5 \
  --arms none,summary,oracle,contextd-new,contextd-component2 \
  --i-accept-spend
.venv/bin/python -m harness.report runs/component2-2026-11-03
```

Preflight matched the 2 November image pins: agent `d51cf876163c`, scorer `1fee64413fff`, egress `de6801f2858f`, replay `6dd6ed88d4d6`, ollama `2a6e883b917f`, model `0a109f422b47`. Model `gpt-6.1-sol`, Codex `0.159.3`, seed `20260930`, five reps, per-trial cap 2,000,000, matrix cap 40,000,000, timeout 1800s. Workspace tar prefix `f832caec9bc28c16`. Forced placement. The agent does not call MCP. `arms check` passed. The matrix ran from 2026-11-03T17:19:19Z to 17:42:13Z, 25/25 scored, 3,986,524 input-plus-output tokens. Each Component 2 trial's `prompt.sha256` is the hash above.

Report: `runs/component2-2026-11-03/report.md`. Real gold. n=5.

| Arm | Solved | ttf median | ttf mean | Repeated | Forbidden | Constraint | Packet recall | Packet precision | Mean tokens |
|---|---|---|---|---|---|---|---|---|---|
| none | 1.00 | 12 | 11.40 | 0 | 0 | 0 | 0 | — | 232,248 |
| summary | 1.00 | 4 | 4.00 | 0 | 0 | 0 | 0.14 | 0.75 | 200,962 |
| oracle | 1.00 | 3 | 3.00 | 0 | 0 | 0 | 1.00 | 1.00 | 70,512 |
| contextd-new | 1.00 | 6 | 5.80 | 0 | 0 | 0 | 0 | 0 | 172,197 |
| contextd-component2 | 1.00 | 3 | 3.20 | 0 | 0 | 0 | 1.00 | 0.90 | 121,386 |

Component 2 and the oracle are `ok` on all five reps. Component 1 and the summary are `placement` on all five, which is the missing required atoms. No trial recorded a constraint violation, a forbidden action, or repeated work.

Paired deltas versus `none`, from the harness report. Bootstrap 95% CI, seed 20260930, 10,000 resamples.

| Arm | Turns to frontier | Tokens |
|---|---|---|
| summary | −7.40 [−9.00, −5.80] | −31,286 [−64,706, +236] |
| oracle | −8.40 [−9.00, −7.60] | −161,735 [−174,093, −150,214] |
| contextd-new | −5.60 [−6.60, −4.60] | −60,050 [−93,183, −26,972] |
| contextd-component2 | −8.20 [−8.80, −7.60] | −110,862 [−133,952, −88,400] |

Component 2 against Component 1, same pairing, arm minus `contextd-new`. Turns per rep −3, −3, −2, −3, −2. Tokens per rep −18,514, −82,056, −61,831, −21,702, −69,953.

| Metric | Mean | 95% CI |
|---|---|---|
| Turns to frontier | −2.6 | [−3.0, −2.2] |
| Tokens | −50,811 | [−73,170, −28,453] |

Every rep used fewer turns and fewer tokens than Component 1. Constraint-violation rate 0. Forbidden-action rate 0. Repeated-work rate 0.

Component 2 against the oracle:

| Metric | Mean | 95% CI |
|---|---|---|
| Turns to frontier | +0.2 | [−0.4, +0.8] |
| Tokens | +50,874 | [+38,389, +63,152] |

The turn gap to the oracle is not distinguishable at n=5. The token gap is. Extraction added no model tokens on the predecessor: the predecessor is the scripted transcript, and the handoff calls no model. The successor's token total includes reading the 1,032-token page.

## 5. What this does not show

The natural classifier only fires on notes the adapter marked as conclusions, plus the speech-act prefixes. A daemon that only has the shell hook still has no stdout and no agent message, and a grounded reality still needs both.

One successor model, five reps, one coding task. The token interval versus Component 1 does not include zero. It is not a claim about other tasks. The retry-budget episode was not given to Codex. Its packet score is not a claim about a successor agent.

Dedup does not merge paraphrases. Two current realities can describe the same failure in different words until an explicit retract names one of them.

## 6. Answer

contextd can take a coding agent's tool results and its own sentences, keep the sentences that are speech acts or that overlap the tool output, and store them as claims with a confidence and a pointer back to the note or the output. A restarted daemon folds those claim events into the same page, and `context_now` is that page. It does not mark an extracted sentence as something the tool printed. It drops a conclusion the output does not support, and it does not replace a current claim unless the later note names it or the note is the single next action.

On limiter-handoff that page contains every required atom. In `component2-2026-11-03`, a fresh agent given it reached the frontier sooner and used fewer tokens than the same agent given the Component 1 page, with no constraint violation and no forbidden action. The human note is still the smaller page. The shell hook still does not carry the tool output the quote is made from; the adapter does.
