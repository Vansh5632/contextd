# Component 1 validation

Measured on 30 October 2026, against the working tree at `bf1826c` plus the uncommitted Component 1 projector, claim, and `context_now` changes. The binary under test is `target/debug/contextd`, built at 2026-10-30 19:36:47 +0530.

This note answers one question:

> Does the new claim-based working state genuinely help a fresh agent continue previous work more correctly and efficiently than the old event-based briefing?

**Against no context, no. Total tokens match `none`, and the agent still reads the same amount of the pool.** The new briefing only beats the old event briefing, which sends the agent to the wrong commit and makes it stop. It does not shrink the investigation once the agent is in the right area.

Every fresh agent that received the new briefing solved the pool-cap bug (5/5) and opened on the failed pytest. Every agent that received the old briefing treated Session B’s host-header commit as finished work and stopped (0/5 solved). Against no context, arrival on the frontier is earlier (paired turn delta −5.4, tokens before that action about 16k versus about 64k). That saving is spent back afterward. Mean tokens after the frontier are about 179k for the new briefing and about 132k for `none`, so the trial totals are 195,493 and 195,765. Both arms touch about 18 paths (`connection_pool.py`, the pool tests, `scripts/unasync.py`, config). The oracle, which names the idle-count decision and the create-branch edit, stays near 9 paths, one edit, and 75,066 tokens. The automatic packet does not contain those notes, because they were never events.

Component 1’s selection contract is ready to freeze. The automatic briefing is not a substitute for an authored handoff. Component 2 was not started.

---

## 1. What was tested

The prototype in `docs/working-state-representation.md` (section 8) folds the event log into tasks and typed claims. `context_now` renders the focal task. The checks below were run against a live daemon and, separately, against Component 0’s forced-placement agent harness. Unit tests were not treated as evidence.

**Live replay of the original 31-event `briefing-selection` log** (`episodes/limiter-handoff/regressions/briefing-selection/baseline/replay/gen-1/events.jsonl`: 24 shell, 6 file, 1 git). Isolated XDG directories and a SQLite file under the run directory. The watch path sat outside this git repo so the daemon would not install hooks here. Embeddings pointed at a closed port.

Checked:

- The failed `tests/test_pool_counts.py` pytest stays the focal reality and the next action.
- The Session B commit (`models: fix default port check in host header`) does not become the focal task.
- A `context_now` query whose text is that commit subject does not move focus.
- Authored constraint, decision, and ruled-out claims, plus one agent reality finding, survive a full daemon restart and a projection rebuild. The only JSON difference is `session_id`.
- The same pytest later exiting 0 supersedes the failure reality and the projector next action.
- Two failing commands: the oldest stays focal. `claim done` moves focus to the next oldest failure. Closing that leaves no focal task, including when an exit-0 commit task is still active. A new intent creates a focal task only once nothing is failing. An intent issued while a failure is focal attaches to that failure and does not move focus.

**Agent evaluation**, forced placement (wrapper, then the packet, then `prompt.txt`). The agent does not call MCP. Arms, same model, workspace tar, tools, and budgets:

| Arm | Packet |
|---|---|
| `none` | empty |
| `summary` | existing summary packet |
| `oracle` | existing ideal handoff |
| `contextd` | frozen event briefing, `packets/contextd.txt`, sha256 `e44ea0cdb976e412b9e61d0a9620961b2632723110e53942f62efc167b9fcc95` |
| `contextd-new` | automatic claim briefing from the 31-event replay, before any authored claims (`packets/contextd_new.txt`) |

`contextd-new` is the observation-only view. The authored claims were checked in the daemon replay and were not given to the agent. Notes in `session_a.script.yaml` were never ingested by the old replay either.

Five repetitions, seed `20260930`, model `gpt-6.1-sol`, Codex `0.159.3`, per-trial token cap 2,000,000, matrix cap 40,000,000, timeout 1800s. Order within each rep is the harness shuffle of that seed.

No in-scope code defect had to be fixed for these results. The packet scorer already flattens `focal_task` claim text, so the new JSON is scored as text rather than as an empty event feed.

---

## 2. Exact commands and setup

From the repo root:

```bash
cargo build -p contextd --bin contextd
python3 eval/component0/runs/component1-validation/replay_live.py
```

The replay script sets, per scenario:

- `XDG_RUNTIME_DIR=/tmp/c1-<scenario>` (socket path must stay within 107 bytes)
- `XDG_DATA_HOME`, `XDG_CONFIG_HOME`, `XDG_CACHE_HOME`, and `HOME` under `eval/component0/runs/component1-validation/<scenario>/`
- `CONTEXTD_WATCH` and `CONTEXTD_CWD` under `/tmp/c1w-<scenario>/`, outside this repo
- `ollama_url = "http://127.0.0.1:9"`

From `eval/component0`, after `python -m harness.images check` (all six pinned images, including the ollama image the preflight requires even though these trials do not call it):

```bash
# CODEX_API_KEY is read from the repo .env. Do not print it.
set -a && source /home/vansh5632/contextd/.env && set +a
.venv/bin/python -m harness.run_matrix \
  --run-id component1-agents \
  --reps 5 \
  --arms none,summary,oracle,contextd,contextd-new \
  --i-accept-spend
.venv/bin/python -m harness.report runs/component1-agents \
  --baseline-run episodes/limiter-handoff/regressions/briefing-selection/baseline/live
```

Image pins matched local ids: agent `d51cf876163c`, scorer `1fee64413fff`, egress `de6801f2858f`, replay `6dd6ed88d4d6`, ollama `2a6e883b917f`, model `0a109f422b47`. Workspace tar prefix `f832caec9bc28c16`. Preflight G3, G4, and G5 passed. The matrix ran from 2026-10-30T14:27:35Z to 14:52:41Z and spent 3,499,564 input-plus-output tokens.

Raw outputs:

- Replay: `eval/component0/runs/component1-validation/` (`report.json`, `packet-scores.json`, briefing JSON, SQLite, daemon logs, `replay_live.py`)
- Agents: `eval/component0/runs/component1-agents/` (`schedule.json`, `matrix.json`, `report.md`, `report.json`, and `<arm>-r<rep>/` with `trace.jsonl`, `exec.jsonl`, `rollout.jsonl`, `final.diff`, `final_tests.json`, `score.json`, `meta.json`)

---

## 3. Baseline versus new results

### Live briefing

After ingesting the 31 events, before any authored claims:

| | Old frozen packet | New automatic briefing |
|---|---|---|
| Focal reality | absent; headline is the Session B commit | `python -m pytest -q -p no:cacheprovider tests/test_pool_counts.py exited 1` |
| Next action | commit-headed recent list | `Re-run and inspect: ` the same pytest |
| Regression `selected` / `marked_failed` | false / false | true / true |
| Failure rank | none | 1 |
| Commit items in the briefing | 2 | 0 |
| Packet tokens (o200k) | 1122 | 1775 |
| Required-atom recall | 0 | 0 |
| Required-atom precision | 0 | 0 |

Focal task id `t_a58f76e3ca4078e8a849ed9682f450cc`. Twenty other active tasks are successful shell commands that fit the 1,500-token render budget (`estimated_tokens` 1396, `truncated` true). The commit subject itself was trimmed off that list. A `now` query whose text is the commit subject returned the same focal id.

Restart rebuild of that view, and a second rebuild after authorship, changed only `session_id`.

Authored text that was still current after the second rebuild:

- constraint: keep the public signatures of `ConnectionPool` and `handle_request` unchanged
- decision: every connection the pool holds, idle keepalive connections included, counts toward `max_connections`
- ruled out: the mock network backend is not the cause
- agent reality: the create-new-connection branch only counts connections that are not idle

Supersession of the same pytest, exit 1 then exit 0: the exit-1 reality and the projector next action are `superseded`; the exit-0 reality is `current` in SQLite. The rendered briefing is empty (`focal_id` null, 27 estimated tokens, the trust notice only), before and after restart. Exit-0 realities are not rendered, and with no failing reality and no user intent `focal_task` returns nothing, which also drops other tasks.

Two failures plus the commit: the pool pytest stays focal; the export pytest and the commit are other tasks. `claim done` on the oldest moves focus to the export pytest. A second `claim done` leaves no focal task. A later intent, `write the release notes`, becomes the focal task and the commit stays an other task. An intent issued while the pool pytest is failing (`fix the export tests`) does not change the focal id.

### Prior published agent baseline

`episodes/limiter-handoff/regressions/briefing-selection/baseline/live/report.md` (live-v2, same model and Codex, n=5): `none` solved 1.00, median turns to frontier 10, mean tokens 213,327; `contextd` solved 0.00, frontier not reached, mean tokens 55,178. live-v1: `summary` solved 1.00, median 3, recall 0.14; `oracle` solved 1.00, median 2, recall 1.0, mean tokens about 70,495.

This run reproduced that split. See section 5.

---

## 4. Passed and failed acceptance criteria

From the prototype’s “done when” checks, plus the extra lifecycle checks requested for this validation.

| Criterion | Result | Evidence |
|---|---|---|
| Failed pytest is a current reality and is marked failed | Pass | Live briefing; regression `stored`, `selected`, `marked_failed` all true; failure rank 1 |
| Session B commit is not the focal next action | Pass | Focal text has no commit; `commit_items` 0; query text does not retarget |
| Packet contains that claim independent of deque position | Pass | Projector fold, not a newest-10 window. The pytest is the oldest shell event |
| Authored finding and constraint survive a rebuild from the log | Pass | Constraint, decision, ruled-out, and agent reality identical after restart; only `session_id` changes |
| Exit 0 supersedes the failure | Pass in the claims table | Failure and next action `superseded`; exit-0 reality `current`. The rendered packet becomes empty |
| Oldest of several failures stays focal; closing it moves focus | Pass | `multi-focus` in `report.json` |
| Explicit focus change | Not implemented | `context_now` text is unused (`snapshot::build` never reads the query). Intent while a failure exists attaches to that failure. This matches prototype rule 3 (oldest failure wins). It is a missing capability, not a broken rule |
| State rebuilt from persisted events | Pass for claims | Shell and claim rows fold back to the same view |
| Required gold-atom recall of the automatic packet | Fail | Recall 0, precision 0. The atoms were never in the 31 events |
| Agent continuation versus the old briefing | Pass | 5/5 solved and on the pytest, versus 0/5 stopped on the commit |
| Agent continuation cheaper than no context | Fail on total tokens | Paired token delta −273, 95% CI [−16,807, +16,262] |
| Agent continuation faster to the frontier than no context | Pass | Paired turn delta −5.4, 95% CI [−6.4, −4.6] |
| Closes the oracle gap | Fail | Oracle median 3 turns and 75,066 mean tokens, recall 1.0 |

The `placement` tag on every `contextd-new` trial is the packet layer: `failure_tag` records missing required atoms first, even when the agent solved and broke no constraint. It is not an agent-behavior failure.

---

## 5. Agent-level metrics

Harness report: `eval/component0/runs/component1-agents/report.md`. Real gold. n=5. Rates are shares of trials. `ttf` is turns to the first on-frontier action (reading or editing the create-connection branch). Packet recall and precision are properties of the packet, so they do not vary by rep.

| Arm | Solved | ttf median | ttf mean (reached) | Repeated | Forbidden | Constraint | Packet recall | Packet precision | Mean tokens | Mean wall |
|---|---|---|---|---|---|---|---|---|---|---|
| none | 1.00 | 11 | 10.40 (5/5) | 0.00 | 0.00 | 0.00 | 0.00 | — | 195,765 | 66.4s |
| summary | 1.00 | 3 | 2.80 (5/5) | 0.20 | 0.00 | 0.00 | 0.14 | 0.75 | 193,179 | 65.8s |
| oracle | 1.00 | 3 | 2.60 (5/5) | 0.00 | 0.00 | 0.00 | 1.00 | 1.00 | 75,066 | 25.3s |
| contextd (old) | 0.00 | not reached | — (0/5) | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 40,411 | 20.9s |
| contextd-new | 1.00 | 5 | 5.00 (5/5) | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 195,493 | 62.5s |

Within-run paired deltas versus `none` in the same rep (arm minus none). Bootstrap 95% CI, seed 20260930, 10,000 resamples.

| Arm | Solved | Turns to frontier | Tokens |
|---|---|---|---|
| summary | 0.00 [0.00, 0.00] | −7.60 [−8.80, −6.20] | −2,587 [−21,166, +15,993] |
| oracle | 0.00 [0.00, 0.00] | −7.80 [−8.40, −7.20] | −120,700 [−140,252, −101,148] |
| contextd (old) | −1.00 [−1.00, −1.00] | not paired (old never reached) | −155,354 [−172,928, −138,162] |
| contextd-new | 0.00 [0.00, 0.00] | −5.40 [−6.40, −4.60] | −273 [−16,807, +16,262] |

Per rep, turns to frontier / total tokens / tokens before the frontier:

| Rep | none | summary | oracle | contextd old | contextd-new |
|---|---|---|---|---|---|
| 1 | 11 / 196,399 / 69,292 | 2 / 180,513 / 7,401 | 3 / 77,569 / 7,630 | — / 38,233 / — | 6 / 175,735 / 18,324 |
| 2 | 9 / 200,757 / 55,996 | 4 / 190,772 / 7,431 | 2 / 64,145 / 7,633 | — / 38,116 / — | 5 / 221,575 / 18,219 |
| 3 | 11 / 182,626 / 68,305 | 2 / 207,368 / 7,407 | 3 / 83,238 / 7,635 | — / 44,898 / — | 5 / 163,316 / 18,254 |
| 4 | 10 / 168,463 / 56,380 | 3 / 188,695 / 7,433 | 3 / 74,397 / 7,634 | — / 38,316 / — | 5 / 164,376 / 18,274 |
| 5 | 11 / 230,581 / 68,168 | 3 / 198,545 / 7,424 | 2 / 75,979 / 7,640 | — / 42,491 / — | 4 / 252,461 / 9,027 |

Mean tokens before the frontier: none 63,628; contextd-new 16,420; summary 7,419; oracle 7,634. The new briefing moves the first useful action forward. The rest of the trial, after the agent is on the frontier, costs about as much as starting from the repo alone. Wall time follows the same shape: the new briefing is about as slow as no context (62s vs 66s) and much slower than the oracle (25s). The old briefing is the fastest only because those trials exit in about 21s without a fix.

Hidden tests agree with the solved flag. `contextd-new-r1` passed the visible suite and the hidden pool-cap tests. `contextd-r1` failed the visible suite and the hidden pool-cap tests; keepalive and unasync checks still passed, which is what happens when the tree is unchanged.

No trial in this run recorded a constraint violation or a forbidden action. `summary-r5` is the only trial with a repeated-work call (1). The old arm’s low token count is the stale stop, not a cheaper successful path.

The preregistered predictions in the generated report compare the arm named `contextd` (the old packet) with summary and oracle. They do not score `contextd-new`. `costlier_than_oracle` missed for that old arm because quitting is cheaper than solving. G6 does not apply: this matrix did not include `stale-only`.

### What the agents actually did

Old briefing, all five reps. First message is about the host-header fix. First commands are `git log` / `git show HEAD` on `httpcore/_models.py`. Each agent then ran the model and HTTP/1.1 tests, reported commit `9813a75` as complete, and made no edit. That is the original stale-context failure, reproduced under the same model.

New briefing, all five reps. First message says the agent will rerun the failing pool-count tests. First search is for `test_pool_counts.py` and `connection_pool.py`. Each agent reran that pytest, read the pool, and landed on the frontier at turn 4, 5, or 6. Final messages describe counting idle connections toward `max_connections`. They were not told that decision; they recovered it from the failing test and the code.

No-context agents also solved, after first looking at `HEAD` and the README (median turn 11). One of them (`none-r4`) mentioned the host-header commit and still went on to fix the pool. The old packet is what stops that search. The new packet is what skips it.

Summary and oracle agents start on the pool assignment logic immediately (median turn 3). Oracle agents also regenerate the sync tree with `scripts/unasync.py`, which is in the oracle packet and absent from the automatic claim view.

---

## 6. Remaining limitations

These were observed. They were not changed. Automatic extraction, a new focus system, retrieval, and compaction are outside this validation.

**The decisive notes were never observed.** Gold atoms for the idle-count finding, the idle-count decision, both constraints, the mock-backend and lock ruled-out lines, and the create-branch next step do not appear in the 31 shell events. Ladder observation recall and selection recall are 0 for that reason. The regression gate for the pytest itself passes. Authoring those claims in the live daemon works and survives rebuild; the agent arm did not receive them, because the old episode did not either.

**Every successful command is an active task.** The placed packet carries about 20 one-line successes (`git apply`, greps, passing pytests) under the focal failure. That is the projector rule of one task per command, then the budget trim. The commit line was dropped by the trim. The noise did not retarget the agent in these five trials.

**A green re-run empties the briefing.** Supersession in SQLite is correct. The renderer then has no focal task, so the successor sees the trust notice and no other tasks. A passing command is stored and not shown.

**There is no focus command.** Oldest failing reality wins. `context_now`’s text argument does not select. `contextd intent` while a failure exists writes the intent onto that failure.

**File events were absent from the replay database that produced the measured briefing.** `replay-31` stored 24 shell, 1 git, and, after authorship, 4 claim events. The 6 `file_system` rows in the source log are not in that SQLite file. The fold reads shell and claim events only, so the focal claim does not depend on them. This validation did not turn that miss into a stable, reproduced defect, and it was not patched. If it is real, the stage is persistence, before projection.

**Exit code without output.** The reality text is `<command> exited <code>`. The pytest assertion text is not in the event, so it is not in the claim. The agent still re-ran the test to see it.

**Packet size.** Internal estimate 1,396 tokens, truncated under the 1,500 budget. Tiktoken o200k of the placed file is 1,775, against 381 for the oracle and 170 for the summary. The view is larger than the ideal handoff because of the other-task lines.

**The investigation after arrival is the same size as `none`.** Mean files touched are 18.4 for the new briefing and 17.2 for `none`. Mean commands are 14.4 and 16.6. Mean tokens after the frontier are about 179k and 132k. The early 47k-token head start is erased by that later reading. The packet says to re-run the pytest. It does not say that idle connections count toward `max_connections`, or that the edit is the create-connection branch, so the agent still reads both pool implementations and the tests to find the bug. The oracle does that reading in about 67k tokens after arrival, across 9.4 files and one edit.

---

## 7. Evidence-backed improvements

The selection failure documented in `docs/briefing-selection-failure.md` is fixed in the live daemon, not only in unit tests.

- The oldest non-zero shell exit is the focal reality (`crates/memory/src/project.rs`, `focal_task` sorts active failing tasks by `created_at_ms`). The pytest is event-oldest and still focal.
- The next action is `Re-run and inspect:` plus that command (`project.rs`).
- A later zero exit of the same command supersedes that reality and that next action.
- `context_now` renders that focal task (`crates/broker/src/snapshot.rs`, `render`). It does not build a newest-10 event list.
- Restarting the daemon rebuilds the same claims from the log. Authored constraint, decision, ruled-out, and agent finding remain current.
- On the agent: 5/5 trajectories with the new packet start on the pool pytest and pass the hidden pool-cap tests. 5/5 trajectories with the old packet start on commit `9813a75` and leave the bug in place. Turns to the frontier move from a median of 11 with no packet to 5 with the new packet, paired on every rep (deltas −5, −4, −6, −5, −7).

The improvement is selection of the failing command. It is not retention of the unobserved decision, constraint, or next-step notes, and it is not a reduction in total tokens versus an agent that only has the repo.

---

## 8. Whether Component 1 is ready to freeze

**Freeze the projector and renderer contract that this validation checked.**

That contract is: one task per shell command; the latest exit is the reality; a non-zero exit is the current failure and implies `Re-run and inspect:`; the oldest such failure is focal; a commit does not outrank it; authored claims survive a rebuild; exit 0 supersedes that failure in the log.

Those rules held in the isolated daemon, and the forced-placement agents behaved as that focal claim predicts. The frozen old packet hash is unchanged. Do not reopen the briefing-selection bug as an open question about whether the pytest stays focal.

**Do not freeze a claim that the automatic view is a sufficient handoff.** Required-atom recall is 0. Total tokens match `none`. The oracle, which carries the notes the log never had, still costs about 120k fewer tokens per paired rep. Emptying the view after a green re-run, ignoring `context_now` query text, and listing every successful command are recorded limitations of this contract.

Component 2 was not started. The gap that remains is unobserved notes (findings, decisions, constraints, ruled-out lines that were never events). Closing that gap is a different component. It is not a defect in the fold that this run measured.

---

## 9. Re-run after the load-bearing handoff (2 Nov 2026)

The 30 October matrix measured the earlier automatic briefing: the failing pytest plus about twenty commands that had already exited 0. This section measures the renderer in this tree. Standing constraints stay on the page, a former failure renders as “now passes” while the task is still active, other tasks are other failures, and authored notes stay on unfinished work. The placed file is `packets/contextd_new.txt`, the observation-only briefing from the 2 Nov live replay (318 tiktoken tokens, daemon estimate 236). The old event feed `packets/contextd.txt` was not changed.

Run `eval/component0/runs/component1-vs-c0-2026-11-02`. Same protocol as section 5: `gpt-6.1-sol`, Codex 0.159.3, seed `20260930`, 5 reps, arms `none`, `summary`, `oracle`, `contextd`, `contextd-new`. 25/25 trials scored. Cumulative tokens 3,799,877. There is no separate git commit for the 30 October tree; that run is section 5. This section is the measurement of the tree that contains the load-bearing renderer.

Primary metrics from `docs/component-0-evaluation.md` section 3. Rates are shares of trials. Packet recall and precision are properties of the packet. Handoff tax is the paired difference versus `none` in the same rep (arm minus none); a negative turn or token delta means this arm cost less. Stale-as-current is not in the agent report for this episode’s automatic packet; required-atom recall is 0 because those notes were never events.

| Metric | 30 Oct `contextd-new` | 2 Nov `contextd-new` | 2 Nov `none` | 2 Nov `contextd` (old feed) | 2 Nov oracle |
|---|---|---|---|---|---|
| Continuation solved rate | 1.00 | 1.00 | 1.00 | 0.00 | 1.00 |
| Turns to first on-frontier action (median) | 5 | 6 | 11 | not reached | 2 |
| Turns to frontier, mean of reached | 5.00 (5/5) | 5.60 (5/5) | 10.60 (5/5) | — (0/5) | 2.60 (5/5) |
| Repeated-work rate | 0.00 | 0.20 | 0.20 | 0.00 | 0.00 |
| Forbidden-action rate | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| Constraint-violation rate | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| Required-atom recall | 0.00 | 0.00 | 0.00 | 0.00 | 1.00 |
| Context precision | 0.00 | 0.00 | — | 0.00 | 1.00 |
| Active tokens (placed packet) | 1,775 | 318 | 0 | 1,122 | 381 |
| Total tokens (mean) | 195,493 | 175,166 | 220,538 | 50,897 | 74,663 |
| Handoff tax, turns | −5.40 [−6.40, −4.60] | −5.00 [−6.80, −3.20] | — | not paired | −8.00 [−9.40, −6.40] |
| Handoff tax, total tokens | −273 [−16,807, +16,262] | −45,372 [−69,438, −20,009] | — | −169,641 [−187,698, −147,711] | −145,875 [−172,116, −118,976] |

Per rep, turns to the frontier on `contextd-new` were 6, 5, 5, 5, 4 on 30 October and 4, 7, 4, 7, 6 on 2 November. The one-turn median move sits inside that spread. The token interval versus `none` excludes zero only on 2 November: the trimmed page is cheaper than starting from the repo alone. The old event feed still stops before the failing pytest. Its low token count is that early stop. The oracle remains the ceiling.

The `placement` tag on every `contextd-new` trial is still the packet layer (required atoms absent). Hidden-test solved rate is 5/5. No trial broke a constraint or took a forbidden action.

Daemon replay on the same binary (`eval/component0/runs/component1-live-2026-11-02`), against the section 4 checks that the renderer can change:

| Criterion | 2 Nov result |
|---|---|
| Failed pytest stays focal; commit is not focal | Pass. Focal `t_a58f76e3ca4078e8a849ed9682f450cc`, next action re-runs that pytest, `other_count` 0 |
| Exit 0 supersedes the failure in the claims table | Pass. Failure reality and projector next action are superseded |
| A green re-run keeps a current constraint and an unfinished decision | Pass. Page shows the signature constraint, the idle-count decision, and “pytest … now passes” (232 estimated tokens). It does not collapse to the trust notice |
| A former failure with no other standing note | Pass. `supersede` renders one “now passes” line and no focal task (111 estimated tokens) |
| Other tasks are other failures | Pass. `multi-focus` lists the second failing pytest and omits the commit |
| Required gold-atom recall | Fail. Unchanged. The notes were never events |

Two limitations in section 6 are changed by this renderer. Successful commands are no longer listed beside the focal failure, and a green re-run no longer empties the page when a constraint or an unfinished decision is still current. Required-atom recall is still 0. The oracle gap on total tokens is smaller than on 30 October and is still open.
