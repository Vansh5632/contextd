# Component 2 evidence validation

> Historical research/report: statements and measurements below describe the dated snapshot, not necessarily the current implementation. See [current architecture](architecture.md), [status](status.md), and the [documentation index](README.md).

Measured on 9 November 2026. The parent packet on limiter-handoff is unchanged: `test_rust_extractor_matches_python_on_the_episode` still matches the frozen page. This note is the evidence check that the 5 November generalization pass did not have. `crates/memory/src/project.rs` and `crates/broker/src/snapshot.rs` were not edited.

The question:

> Can contextd automatically construct accurate, evidence-backed working state that improves agent continuation without preserving misleading conclusions?

**Partly.** A conclusion that denies an increment the tool output shows, or that hangs a new cause off a real identifier with "means", is no longer a current fact. On both episodes a fresh agent reaches the edit sooner than with the observation-only page, and neither run added a constraint violation or a forbidden action. The token saving is clear on limiter-handoff and is not distinguishable on retry-budget. A new cause packed into the same clause as a real identifier, with no "so", "because", "means", or "and", can still be admitted. That hole is why this is not a freeze.

---

## 1. What was compared

Ten sentences, labeled by reading the tool output. The gold file was not an input. The set is `harness.evidence_probe`: the two retry-budget findings, the timeout ruling, the limiter-handoff idle paraphrase, the "not counted" sentence, an order reversal, the database-pool sentence that shares `retry_count` and `open_socket`, two ungrounded sentences, and the "might" hedge.

| Check | What it decides | Accuracy | Time | Cost | What it gets wrong |
|---|---|---|---|---|---|
| Overlap of at least 2 content words | Supported if enough words match | 7/10 | local | none | Both contradicted sentences, and the database-pool sentence, come out supported. The idle paraphrase is kept |
| Overlap of at least 3 | Same, higher bar | 7/10 | local | none | Same three errors. The extra word does not change them |
| Overlap of at least 4 | Same, higher bar | 6/10 | local | none | Drops the true timeout ruling as well. The two contradicted sentences still pass |
| Clause anchor, polarity, and order | Below | 10/10 | 0.422 ms for the ten sentences | none | Nothing in this set |

Raising the overlap count does not separate a contradiction from a paraphrase. The false sentence and the true increment sentence share the same identifiers with the same listing. The idle finding uses "keepalive", "eviction", and "branch", which are not in the `sed` output, and a stricter word count would be the thing that drops it.

A model was not called. One verifier call per conclusion would add a network round trip and a bill at every handoff, and it can reject the idle paraphrase (those words are not in the listing) or accept a fluent denial. The ten sentences were already separated without that call, so the call is not justified.

The check that scored 10/10:

1. A hedge ("might", "maybe", "could be", "not sure") stays a hypothesis. It is not promoted because it shares words with the output.
2. If the sentence denies a count ("not counted", "stays at zero", and the same family) and the output has `name += <nonzero>` for a name the sentence uses, the sentence contradicts the output.
3. If the sentence says identifier A is before or after identifier B, and both appear in the output in the other order, the sentence contradicts the output.
4. Otherwise the sentence is split on "so", "because", "means", and "and". Every clause must share a content word with the output. A clause that does not is insufficient, however many words the other clauses share.
5. The whole sentence still needs two shared content words. That is an anchor, not a score. More shared words do not make the sentence more supported.

Contradicted and insufficient sentences are skipped. They are not written as current realities and they are not relabelled as hypotheses. An explicit "might" is the predecessor saying it is unsure. A false assertion is not that.

Two current claims of the same kind that share two content words and take opposite stances (one denies a count, the other affirms it) are not left as two verified facts, and the later one does not replace the earlier one. The earlier claim is superseded by an explicit conflict event, then both texts are re-asserted as `unknown` / `hypothesis` with their original evidence ids. A later retract still removes only the sentence it quotes.

## 2. What the code does

The check lives in `crates/broker/src/extract.rs` (`evidence_relation`, `demote_if_conflict`). It runs before a natural conclusion or a grounded file-read note becomes an inferred reality, and again in `admit`, so a hand-built candidate cannot bypass it. Speech-act decisions, constraints, and explicit "Ruled out" / "Next step" lines are unchanged. The projector still owns exit codes. An extracted claim is still never `observed`.

On the retry-budget trajectory the gate now skips:

- "The first send is not counted, so retry_count stays at zero until a failure." Reason: contradicts the cited tool output. The listing is `retry_count += 1` before `open_socket` and `send`.
- "The database pool is exhausted…" after `class Courier:`. No overlap, as before.
- "The retry_count before open_socket means the database pool is exhausted." The "means" clause has no word in the listing.

The same current claims come out with the retract line removed and with it present. The retract of a sentence that was never admitted does not quote a current claim, so it changes nothing. The supported increment sentence stays.

Conflicting `Decision:` lines ("count the first send as a retry" and "leave the first send at zero") are both current hypotheses. Retracting the first removes only that text. The pytest `exited 1` line stays, and a retract of that line is still refused.

Limiter-handoff's idle sentence still comes through the file-read path. The required atoms on that episode are unchanged.

## 3. Packet scores

Tiktoken `o200k_base`, after the packets existed. Gold was not an input to the extractor.

Retry-budget:

| Arm | Tokens | Required recall | Unit precision | Stale as current |
|---|---|---|---|---|
| none | 0 | 0 | — | 0 |
| summary | 59 | 0.17 | 0.50 | 0 |
| contextd-new (Component 1) | 246 | 0 | 0 | 0 |
| contextd-component2 | 627 | 1.00 | 0.875 | 0 |
| oracle | 117 | 1.00 | 1.00 | 0 |

The daemon's own estimate of the Component 2 page is 499. The unmatched eighth is still the projector's `exited 1` line. The stale atom is the "not counted" sentence. It is absent, so it is not stale-as-current. The leak marker `kq7-retry-budget-marker-4e1c` is not on the page.

Limiter-handoff's Component 2 page is the one already frozen in the 3 November note: required recall 1.00, unit precision 0.90. Re-extraction matches it.

Live handoff on the retry-budget log, daemon in a temporary directory, took 0.0046 seconds and called no model. Restart rebuilt the same claims.

## 4. Agent runs

Both matrices use Codex `0.159.3`, model `gpt-6.1-sol`, seed `20260930`, five paired reps, per-trial cap 2,000,000, matrix cap 40,000,000, timeout 1800s. Image pins match the 3 November run: agent `d51cf876163c`, scorer `1fee64413fff`, egress `de6801f2858f`, replay `6dd6ed88d4d6`, ollama `2a6e883b917f`, model `0a109f422b47`. Forced placement. The agent does not call MCP. Gold is not in the prompt.

An earlier retry-budget attempt (`component2-evidence-retry-budget-2026-11-09`) stopped being the result after trial 0. Pytest wrote `__pycache__` into `git diff --binary`, and `score.py` reads that diff as text. The episode tree now has a `.gitignore` for bytecode and `.pytest_cache`. The workspace was rebuilt. That attempt is not in the table.

### Retry-budget

Run `component2-evidence-retry-2026-11-09`, 25/25, 2026-11-09T16:26:29Z to 16:34:58Z, 1,024,801 tokens. Workspace tar `cb908273b4393323fc2a09c8700354c237922569ebcb8434169819c455cc1021`. Report: `eval/component0/runs/component2-evidence-retry-2026-11-09/report.md`.

| Arm | Solved | ttf mean | Repeated | Forbidden | Constraint | Recall | Precision | Mean tokens |
|---|---|---|---|---|---|---|---|---|
| none | 1.00 | 3.80 | 1.00 | 0 | 0 | 0 | — | 42,434 |
| summary | 1.00 | 2.60 | 0.20 | 0 | 0 | 0.17 | 0.50 | 36,869 |
| oracle | 1.00 | 2.40 | 0.00 | 0 | 0 | 1.00 | 1.00 | 42,101 |
| contextd-new | 1.00 | 5.00 | 1.00 | 0 | 0 | 0 | 0 | 42,072 |
| contextd-component2 | 1.00 | 2.80 | 1.00 | 0 | 0 | 1.00 | 0.875 | 41,484 |

Paired versus `none`, seed 20260930, 10,000 resamples. Turns to the frontier for Component 2: −1.00 [−1.80, −0.20]. Tokens: −951 [−4,176, +1,003]. The turn interval excludes zero. The token interval does not.

Component 2 minus Component 1, same method: turns −2.2 [−2.8, −1.6] per rep −2, −2, −3, −1, −3. Tokens −588 [−3,683, +1,282]. Every rep reached the edit sooner than the observation-only page. The token gap includes zero.

Every arm solved the hidden tests. No constraint was broken and no forbidden edit was recorded. The Component 2 failure tag is `behaviour` on all five reps because each rep read `timeouts.py` once, usually inside a command that also read `courier.py` and `host.py`. The no-briefing arm and Component 1 did the same. The summary did it once in five. The oracle, which says not to edit that file, did not. The page did not revive the retracted story: the sentence is not in the packet, and no rep edited toward "the first send is not counted."

The task is small. An agent with an empty page already solves it in about four turns. The page saves about one of those turns. It does not save a reliable number of tokens, and it does not stop a directory listing from touching the ruled-out file.

### Limiter-handoff

Run `component2-evidence-limiter-2026-11-09`, 25/25, 2026-11-09T16:35:34Z to 16:55:37Z, 3,619,497 tokens. Workspace tar prefix `f832caec9bc28c16`, the same tree as the 3 November run. Report: `eval/component0/runs/component2-evidence-limiter-2026-11-09/report.md`.

| Arm | Solved | ttf mean | Repeated | Forbidden | Constraint | Recall | Precision | Mean tokens |
|---|---|---|---|---|---|---|---|---|
| none | 1.00 | 11.00 | 0 | 0 | 0 | 0 | — | 195,647 |
| summary | 1.00 | 3.20 | 0.20 | 0 | 0 | 0.14 | 0.75 | 208,521 |
| oracle | 1.00 | 2.80 | 0 | 0 | 0 | 1.00 | 1.00 | 60,932 |
| contextd-new | 1.00 | 5.20 | 0 | 0 | 0 | 0 | 0 | 160,843 |
| contextd-component2 | 1.00 | 2.80 | 0 | 0 | 0 | 1.00 | 0.90 | 97,957 |

Component 2 versus `none`: turns −8.20 [−9.20, −7.20], tokens −97,690 [−125,908, −68,477]. Versus Component 1: turns −2.4 [−2.8, −2.0] per rep −2, −3, −2, −2, −3; tokens −62,886 [−94,697, −31,074]. All five Component 2 reps are `ok`. No constraint violation, no forbidden action, no repeated-work call. The turn mean matches the oracle. The token mean is still about 37,000 above it.

This is the same shape as the 3 November result. The evidence check did not drop the idle finding and did not add a harmful-context regression on this episode.

## 5. What is still wrong

A sentence such as "The retry_count database pool is exhausted" is one clause. It shares `retry_count` with the listing, so the clause-anchor rule accepts it. The "means" split is what rejects the sentence we actually had. A denial that does not use the count phrases, and does not reverse "before" or "after", is also accepted if every clause touches the output. Those are the cases a model would be for. They were not in the measured set, and they are not a reason to add the call yet.

On retry-budget the successor still spends one command on `timeouts.py` when the page is the automatic one. The oracle's explicit "do not edit" is what drives that rate to zero. Naming a ruled-out file is not the same as telling the next agent to leave it alone.

## 6. Freeze

Not frozen.

| Criterion | Result |
|---|---|
| Unsupported conclusions do not become verified facts | Holds for a denial of an increment, a reversed order, and a new cause after "means". Does not hold for a new cause written in the same clause as a real identifier |
| Contradictions and retractions | Holds. Opposite decisions become hypotheses and keep their evidence. A retract removes only the quoted text. The observed exit cannot be retracted. The false retry sentence is absent with or without the retract line |
| A fresh agent benefits on both episodes | Holds for turns to the first on-frontier edit, against both the empty page and Component 1, on both episodes. Token benefit holds on limiter-handoff only |
| No new harmful-context regression | Holds against Component 1 and against no page: constraint violations 0, forbidden actions 0, and repeated work no higher than the empty page. The retry-budget page does not beat the short summary on repeated work |

## 7. Commands

From the repo root:

```bash
cargo fmt -p broker
cargo test -p broker --lib extract::tests -- --test-threads=8
```

From `eval/component0`:

```bash
.venv/bin/python -m harness.evidence_probe

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  .venv/bin/python -m pytest \
  tests/test_natural_trajectory.py tests/test_retry_budget_live.py \
  tests/test_extract_claims.py tests/test_matrix.py tests/test_arms.py -q

.venv/bin/python -m harness.arms check --episode limiter-handoff
.venv/bin/python -m harness.arms check --episode retry-budget
.venv/bin/python -m harness.build_workspace --episode retry-budget --verify-reproducible
.venv/bin/python -m harness.leakcheck --episode retry-budget
```

The agent matrices, after `set -a && source ../../.env && set +a`:

```bash
.venv/bin/python -m harness.run_matrix \
  --run-id component2-evidence-retry-2026-11-09 \
  --episode retry-budget --reps 5 \
  --arms none,summary,oracle,contextd-new,contextd-component2 \
  --i-accept-spend

.venv/bin/python -m harness.run_matrix \
  --run-id component2-evidence-limiter-2026-11-09 \
  --episode limiter-handoff --reps 5 \
  --arms none,summary,oracle,contextd-new,contextd-component2 \
  --i-accept-spend
```

`PYTEST_DISABLE_PLUGIN_AUTOLOAD` is required on this machine because the ROS `launch_testing` entry point imports a missing module. The Component 2 packet is gitignored and is rebuilt by `project_events` in the tests. The runs above used the packet already on disk.
