# Evaluation integrity

Scorer `integrity-2`. This report compares the 17 November 2026 file with a rescore of the same daemon. The product extractor was not changed. No live agent was run.

## Commands

Grader tests, no daemon:

```bash
cd eval/component21
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests -q --tb=short -p pytest
```

Result on 20 November 2026: 40 passed.

Daemon rescore, no model and no API key. Enrichment is pointed at `http://127.0.0.1:9`.

```bash
cargo build -p contextd --bin contextd
cd eval/component21
python -m harness.run --allow-dirty --reuse-binary --output baseline/integrity-after.json
```

That run finished at `2026-11-20T06:49:13Z`, exit 0, `harness_errors` 0. Python 3.12.3. Binary `/home/vansh5632/contextd/target/debug/contextd`, package 0.1.0, debug. Git revision `baa457146d5a6c9f592b6aea2fa5278b533e4b8d`. `git status` was not empty, so the file is `official: false`. `model` is null. The sealed held-out cases were not edited.

`baseline/results.json` is the before snapshot. It was not overwritten. It was generated `2026-11-17T11:01:11Z` on a clean tree at the same revision, with the previous scorer.

A later clean tree can record a new official file. Do not point that command at `results.json` until you mean to replace the before snapshot:

```bash
python -m harness.run --output baseline/integrity-clean.json
```

## What the old pass count mixed together

The before file reports 23 pass, 28 fail, 5 blocked, 56 cases. Its false-admission rate is 7/23. Sixteen of those 23 denominator atoms were claims the checker skipped or never saw. Every bad claim the checker actually accepted was stored as reality.

The 23 passes were:

| Role | Cases | What a pass meant |
|---|---|---|
| Daemon contract, claim actually handled | `f00-supported-control`, `f18-hedge-might` | The supported control was stored. `might` stayed a hypothesis |
| Skipped false or unsupported sentence | 9 development, 4 held-out, `f09-so-split`, `f12-multi-evidence` | The sentence was not presented. That is not a semantic refusal |
| Incomplete observation | `f10-compaction`, `f11-truncation` | Missing or truncated output was not presented as reality |
| Grader self-test | `f20-regex-gap` | The grader catches a regex false match. The daemon is not the subject |
| Synthetic continuation | `c01`, `c02`, `c03` | Scripted traces. No model |

Held-out atoms were also inside the pooled recall of 4/18. Evidence reference accuracy was 0/0 because no daemon atom checked a snippet, and the snippet path called `json.dumps` without importing `json`.

## After, by population

File counts are 6 pass, 28 fail, 15 vacuous, 2 incomplete, 5 blocked, 53 executed, 56 total. The 6 passes are the two daemon contract passes plus `f20` and the three synthetic traces. Read the populations, not that 6.

| Population | Pass | Fail | Vacuous | Incomplete | Blocked | Total |
|---|---|---|---|---|---|---|
| Daemon contract (regression and development) | 2 | 26 | 11 | 2 | 2 | 43 |
| Held-out, scored apart | 0 | 2 | 4 | 0 | 0 | 6 |
| Grader self-test | 1 | 0 | 0 | 0 | 0 | 1 |
| Synthetic continuation | 3 | 0 | 0 | 0 | 0 | 3 |
| Documented, not run | 0 | 0 | 0 | 0 | 3 | 3 |

The two daemon contract passes are `f00-supported-control` and `f18-hedge-might`. The 28 product fails are unchanged as fails. Seventeen old passes moved, and none of those moves is a product change:

- Vacuous: `d06` through `d11`, `d15`, `d16`, `d17`, `f09-so-split`, `f12-multi-evidence`, `h02`, `h04`, `h05`, `h06`.
- Incomplete: `f10-compaction`, `f11-truncation`.

`f09-semicolon` stays a fail. Its unsupported whole sentence is vacuous, and its supported prefix was skipped.

Checker decisions on the 53 regression and development atoms: 17 accepted, 0 rejected, 25 skipped, 11 absent. The daemon did not emit an explicit rejection in this run. A skip is coverage, not a refusal.

## Independent rates

### Supported-claim recall

Denominator: every submitted supported atom judged `presented_iff_supported`, including skips.

| Suite | Recall | Hits |
|---|---|---|
| Regression | 4/8 = 0.5 | `f00` control, `f07` not-idle, `f09-so-split` prefix, `f15` finding while revision A was current |
| Development | 0/8 | Identity, rename, paraphrase, comments, whitespace, JavaScript, Go, Rust were skipped |
| Held-out | 0/2 | Rust increment and JavaScript decrement were skipped |

The misses in regression are the paraphrase, the English clause of the unicode sentence, the semicolon prefix, and the restatement after retract. Do not quote 4/18. That pool put held-out misses in the tuning number.

### False-claim admission

Denominator: contradicted or insufficient atoms the checker accepted or rejected, or that were stored. Skips are `uncovered`, not refusals. The rate is null when nothing was covered.

| Suite | Admission | Uncovered, not successes |
|---|---|---|
| Regression | 7/7 = 1 | 3 (`f09-semicolon` whole, `f09-so-split` whole, `f12`) |
| Development | null (0/0) | 9 |
| Held-out | null (0/0) | 4 |

The seven admitted false claims are negation, numeric magnitude, the unicode whole sentence, execution order, the idle quantifier, and both causal directions. Each one also cites the listing that contains the true snippet. Citation and truth are different measurements.

Supported precision, among atoms presented as established realities: regression 4/11. Development and held-out are null because nothing was presented. A run that admits nothing has precision null, not 1.

### Uncertainty

Regression `must_be_hypothesis`: 2/3 kept. `might` and the `f15` hypothesis stayed hypotheses. `probably` was dropped (`uncertainty_dropped`) and was not stored as a fact (`presented_as_fact` 0). These atoms are not in the false-admission denominator.

### Evidence

Evidence reference accuracy is 15/15 on stored regression and development claims that declare a snippet and an evidence bind. The cited event's payload contains that snippet. Held-out evidence is null (0/0) because the sealed file was not given snippets.

Fifteen matches include the seven false admissions. The claim cites the real observation and the sentence is still unsupported. Wrong evidence ids and snippets that are absent from the cited payload fail in `tests/test_grader_validity.py`. This public route does not construct a wrong id on its own: accepted claims cite the note and the support event together.

Task association is 0/2. Author attribution is 0/2.

### Time, isolation, restart

| Rate | Value | Denominator |
|---|---|---|
| Stale presented as current | 1/1 | `f15` after the passing command |
| Supersession | 1/1 | `f15` retract before restart. The old 2/3 counted this retract again after restart |
| Cross-task contamination | 1/1 | `f16` alpha only. The widget claim is not in this denominator |
| Unrelated retention | 2/2 | The widget claim once, plus `f16` beta, which stayed current |
| Restart state | 1/2 | `f15` retract still held. `f17` was still current, with `timestamp_order_reversed` |
| Process restart | 2/2 | The process came back. This is not the state rate |

### Incidents

| Item | Value |
|---|---|
| Handoff and now | 104/106. Same two failures as the before file |
| All protocol requests | 261/263. Observe and authored calls are in this denominator. They succeeded |
| Transport errors | none |
| `query handler unavailable` | 2, both on `f05` after the panic |
| Panics | `f05-unicode-panic`. The case fails. The panic is an incident, not an empty briefing |
| Harness errors | 0. A harness error exits 1. A contract fail exits 0 |
| Incomplete observations | 2 not admitted (`f10`, `f11`), 0 admitted, 0 not reached |
| Blocked | `open_contract` 3, `unobservable` 1, `spend_not_authorized` 1 |

`f19-note-claim` stored `the pool is exhausted` as a current inferred reality with no evidence gate. The case stays `blocked` / `open_contract` and is outside the 7/7. That admission is visible on the atom and is not a pass.

## Remaining gaps

- Held-out labels still have no second reviewer. Do not tune on that file. Its ids are absent from the regression and development headline.
- A wrong evidence id is grader-tested and was not produced by the public protocol in this run.
- Compaction and truncation were not presented. This run still does not show an already-admitted claim surviving after its output disappeared. Truncation has raced the other way on an earlier run.
- Token-budget trim is `unobservable`. Shell-hook stdout was not run (`open_contract`). The live continuation matrix was not run (`spend_not_authorized`).
- No result here is a fresh-agent improvement.

## CI

`.github/workflows/ci.yml` builds `contextd` and runs regression, development, and continuation in one file, then held-out in another. The job fails if the process exits 1, if `model` is not null, if a held-out id appears in the first file, or if an uncovered false claim has status `pass`. It does not fail because recall is 4/8 or admission is 7/7.
