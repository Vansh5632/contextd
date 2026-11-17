# Component 2.1 validation

> Current investigation record (10 November 2026), not a frozen specification. Measurements below are recorded results, not a fresh run for this documentation refresh. See [current architecture](architecture.md), [status](status.md), and the [documentation index](README.md).

Measured on 10 November 2026. The research is `docs/component-2.1-research.md`. The rule is `docs/component-2.1-design.md`. `crates/memory/src/project.rs` and `crates/broker/src/snapshot.rs` were not edited. No model is called.

**Not frozen.** False claims that share a real identifier are rejected more reliably, and the required findings are still on both pages. On the pool bug the shorter page is a slower, more expensive handoff than the 9 November page. The turn gap to Component 1 on this rerun includes zero.

## 1. Adversarial set

Locked by `cargo test -p broker --lib extract::tests`. The 9 November checker is the baseline.

| Case | Baseline | After |
|---|---|---|
| retry_count 2 expects 1 | kept | kept |
| increments retry_count before open_socket, and the first send is already counted | kept | kept |
| timeout path is not the cause because deliver has no timeout handling | kept | kept |
| create branch only counts non-idle connections, then keepalive and eviction clauses | whole sentence kept | only "only counts connections that are not idle" |
| might be involved | hypothesis | hypothesis |
| The retry_count database pool is exhausted | kept as a reality | rejected |
| retry_count before open_socket means the database pool is exhausted | whole sentence rejected | true prefix kept, database clause dropped |
| increments retry_count before open_socket, so the database pool is exhausted | whole sentence rejected | true clause kept |
| not counted, so retry_count stays at zero | rejected | rejected, and "not counted" is not kept as its own claim |
| open_socket before retry_count | rejected | rejected |
| opposite Decision lines | both hypotheses | unchanged |
| Retract of one quoted claim | removes that claim | unchanged |

The retry-budget page was regenerated and compared to the file the 9 November agents read. The text did not change. That matrix was not repeated. Repeating it would not be a comparison of two implementations.

The pool page changed. Required-atom recall is still 1.00, unit precision 0.90, stale-as-current 0. Tiktoken `o200k_base` count is 1,008. The agent reality is now only the licensed clause. The decision, constraints, rulings, and next step are still there. Keepalive and eviction are not, because those words are not in the cited listing and they are not in the interpretive list.

Handoff remains a local pass. The 9 November retry handoff was 0.0046 seconds with no model call. This change adds no call.

## 2. Pool-bug rerun

Same model `gpt-6.1-sol`, Codex `0.159.3`, seed `20260930`, five paired reps, same image pins, same workspace tar prefix `f832caec9bc28c16`. Run `component21-limiter-2026-11-10`, 25/25, 2026-11-10T06:45:38Z to 07:06:44Z, 3,898,634 tokens. Report: `eval/component0/runs/component21-limiter-2026-11-10/report.md`.

| Arm | Solved | Mean turns | Repeated | Forbidden | Constraint | Recall | Precision | Mean tokens |
|---|---|---|---|---|---|---|---|---|
| none | 0.80 | 9.00 | 0 | 0 | 0 | 0 | — | 212,781 |
| summary | 1.00 | 3.20 | 0.20 | 0 | 0 | 0.14 | 0.75 | 195,104 |
| oracle | 1.00 | 2.80 | 0 | 0 | 0 | 1.00 | 1.00 | 73,564 |
| contextd-new | 1.00 | 5.20 | 0 | 0 | 0 | 0 | 0 | 177,544 |
| contextd-component2 | 1.00 | 4.40 | 0 | 0 | 0 | 1.00 | 0.90 | 120,734 |

Component 2.1 versus Component 1 in this run, seed 20260930, 10,000 resamples: turns −0.8 [−1.8, +0.2] per rep −2, −1, −2, +1, 0. Tokens −56,810 [−95,562, −18,058]. The token interval excludes zero. The turn interval does not. Solved 5/5. No constraint violation, no forbidden action, no repeated-work call. All five reps are `ok`. One no-page rep did not solve; that is the 0.80.

Against the 9 November Component 2 page, under the same conditions, this page is worse for the agent: mean turns 4.40 against 2.80, mean tokens 120,734 against 97,957. Both pages have recall 1.00 and precision 0.90. The difference is the keepalive and eviction wording the residue rule dropped. Those clauses are not copied from the tool output. They were also what made the handoff faster.

## 3. Retry-budget

Unchanged page, so the 9 November result stands. Mean turns 2.80 against 5.00 for Component 1 and 3.80 for no page. Solved 5/5. No constraint or forbidden regression. Every Component 2 rep still read `timeouts.py` once, as did the empty page and Component 1. The clause rule does not add a prohibition the predecessor did not write, and a directory listing does not go through claim admission.

## 4. Freeze

Not frozen.

| Criterion | Result |
|---|---|
| False claims rejected more reliably | Holds for a new cause in the same clause, and for a mixed sentence whose false half is dropped |
| Correct findings retained | Holds. Retry page unchanged. Pool required-atom recall 1.00. The keepalive and eviction clauses are gone |
| Ruled-out work avoided | Does not move. The retry repeated read is a repo listing, on the empty page as well |
| Turns and tokens | Pool turns against Component 1 are not distinguishable. Pool tokens against Component 1 still fall. Both are worse than the 9 November automatic page |
| No task-success or constraint regression | Holds. Pool Component 2 solved 5/5 with no constraint or forbidden action |

The model is still not the default. The specific case for one later is a clause whose extra nouns are a real paraphrase of the control flow, such as keepalive and eviction, and which the benchmark shows the agent uses. The clause rule cannot keep those without also being the rule that would keep a new mechanism named beside a real identifier. That tradeoff is the reason this component stays open.

## 5. Commands

From the repo root:

```bash
cargo fmt -p broker
cargo test -p broker --lib extract::tests -- --test-threads=8
```

From `eval/component0`:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  .venv/bin/python -m pytest \
  tests/test_extract_claims.py tests/test_natural_trajectory.py \
  tests/test_retry_budget_live.py -q
```

The pool rerun, after `set -a && source ../../.env && set +a`:

```bash
.venv/bin/python -m harness.run_matrix \
  --run-id component21-limiter-2026-11-10 \
  --episode limiter-handoff --reps 5 \
  --arms none,summary,oracle,contextd-new,contextd-component2 \
  --i-accept-spend
.venv/bin/python -m harness.report runs/component21-limiter-2026-11-10
```
