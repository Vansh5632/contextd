# Baseline report

Run at `2026-11-12T14:16:48Z` with:

```bash
cargo build -p contextd --bin contextd
cd eval/component21
python -m datasets.freeze
python -m harness.run --output baseline/results.json --with-historical
```

Binary: `/home/vansh5632/contextd/target/debug/contextd`, package `0.1.0`, profile `debug`. Git revision `0714127c32aee3970d079f3a957e1f51ea151794`. The status recorded in the result file shows a dirty tree; the new evaluation files were uncommitted, and no production crate was edited. Model judge: none. Random seed: none. Dataset: `component21-eval-1`.

Counts: 23 pass, 28 fail, 5 blocked, 53 executed, 56 total. The five blocked cases are the Done-reopen contract question, trim-budget, both authored-route cases, and the live continuation matrix.

## Semantic layer

Denominators are daemon atoms whose judgment is `contract` and whose status is not blocked.

| Rate | Value | Fraction | Denominator |
|---|---|---|---|
| False reality admission | 0.304 | 7/23 | Contradicted or insufficient atoms judged `presented_iff_supported` |
| Contradicted admission | 0.444 | 4/9 | Contradicted atoms judged `presented_iff_supported` |
| Supported recall | 0.222 | 4/18 | Supported atoms judged `presented_iff_supported` |
| Supported precision | 0.364 | 4/11 | Established realities among those atoms. Undefined if none are admitted |
| Uncertainty kept | 0.667 | 2/3 | `must_be_hypothesis` atoms |

The four contradicted admissions are negation, numeric magnitude, execution order, and the idle quantifier. The insufficient sentences admitted as reality include the unicode whole sentence and both causal directions. The four supported admissions that received credit are the public control, the supported quantifier arm, the `so` prefix, and the finding before it went stale.

Supported precision is not 1. A reject-all system would have recall 0 and precision null. This run admitted 11 established realities under the presentation check, 4 of which were supported.

## Evidence, provenance, and time

| Rate | Value | Fraction |
|---|---|---|
| Evidence reference accuracy | null | 0/0 daemon atoms. The grader unit test covers a wrong evidence id. |
| Evidence completeness | 1.0 | 2/2. Both the compaction and truncation atoms found incomplete output and did not present the claim. That is the safe outcome, not evidence that compaction or truncation is harmless. |
| Task association | 0.0 | 0/2 |
| Author attribution | 0.0 | 0/2 |
| Current-state accuracy | 0.571 | 4/7 |
| Stale presented as current | 1.0 | 1/1 |
| Correction success | 0.667 | 2/3 |
| Cross-task contamination | 0.333 | 1/3 |

The contamination numerator is only `superseded_other_task`. In the cross-task case, `alpha_budget increments` was superseded while the beta claim stayed current. The two unrelated widget checks stayed current and are not counted as contamination.

The lost correction is the user retract submitted after handoff. Replay order follows the extracted claim's later timestamp. The observe-retract inside the stale episode did supersede the finding and survived a restart.

## Reliability

| Rate | Value |
|---|---|
| Handoff and now success | 104/106 = 0.981 |
| `query handler unavailable` | 2, both on the omega case after the panic |
| Panics | 1 |
| Restarts | 2/2 |

Checker versus renderer on contract atoms: 31 checker rejections, 17 checker acceptances, 0 renderer exclusions, 17 presented as established.

## Historical side channel

The optional Component 0 scorer ran on the vendored limiter and retry-budget packet fixtures. Both scored required recall 1.0. `factuality_oracle` is false. The same channel matches the planted negation in the grader test while the atomic judge rejects it. High regex recall is not a memory result.

## How to read a pass

Development and held-out passes on negated, reordered, or conditional sentences used wording the checker did not admit. The regression negation uses `increments`, which is in the production interpretive list, and that negation was admitted. A pass on `increases` in another identifier family means the sentence was not presented. It does not mean the checker understood the negation.
