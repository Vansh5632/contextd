# Shadow semantic verification experiment

Authorized run of `gpt-4.1-mini` on 20 November 2026. The call used the key in `.env` and `--i-accept-spend`. Admission was not changed. `writes_memory` stayed false. Held-out cases were not sent.

## Command

```bash
cd eval/component21
python3 -m harness.shadow_experiment \
  --from-results /tmp/c21-shadow-diagnostics-rerun.json \
  --output /tmp/c21-shadow-experiment.json \
  --i-accept-spend
```

The key was `CODEX_API_KEY` from `.env`, passed as `OPENAI_API_KEY`. It is not stored in the results file. Source daemon rescore: git `133843d17cb1f09d172e7f5dd686ae60f71e4b25`. Candidate sha256 prefix `218ca8795f20d2c8`. Model `gpt-4.1-mini`, temperature 0, strict JSON schema.

## Set

30 claims from regression and development. Cohorts:

| Cohort | Supported | Negative control | Proposed owner |
|---|---:|---:|---|
| `lexical_pass` accept | 3 | 7 | AI review |
| `residue` or `order` skip | 9 | 9 | AI if supported, rule if negative control |
| Unsplit or absorbed span | 2 | 0 | Escalate the splitter |

The three supported accepts are `f00`, the true arm of `f07`, and the `so` prefix of `f09-so-split`. The seven negative accepts are `f01`, `f02`, the Chinese-tailed `f04` sentence, `f06`, `counts connections if idle`, and both `f08` directions.

The nine supported skips are `f03` (`residue`), `d03` (`order`), and seven development `residue` skips (rename, comments, whitespace, JavaScript, Go, Rust, and the renamed baseline). The nine negative-control skips include the database-pool semicolon sentence, `f12`, and development negations, magnitudes, conditionals, and the false operation-order claim `d08`.

Two supported spans have no exact trace. `f09-semicolon`'s prefix sits inside a `residue` skip. `f04`'s English prefix sits inside a `lexical_pass` accept. Those are splitter bugs, not model labels on the stored claim.

## Checker, on this set only

The daemon stored a claim or it did not. Stored counts as the checker's `SUPPORTED`.

| Rate | Result |
|---|---|
| False-claim admission | 7/16 = 0.44 |
| Supported recall | 3/14 = 0.21 |
| Precision | 3/10 = 0.30 |

These denominators are the 30-claim experiment, not the full regression rates (4/8 recall, 7/7 false admission). The seven false admissions are the entire false-admission numerator. The checker did not store any of the nine negative-control skips.

## Model versus checker

Same 30 claims. A false admission is a gold `CONTRADICTED` or `INSUFFICIENT_EVIDENCE` claim labeled `SUPPORTED`.

| Rate | Checker | gpt-4.1-mini |
|---|---|---|
| False-claim admission | 7/16 = 0.44 | 3/16 = 0.19 |
| Supported recall | 3/14 = 0.21 | 14/14 = 1.00 |
| Precision | 3/10 = 0.30 | 14/17 = 0.82 |
| Evidence quote in the tool output | quote gate only | 30/30 |

The model corrected five checker false admits: `f01` never-increments, `f02` magnitude 999, `f06` execution order, and both `f08` causal directions (`INSUFFICIENT_EVIDENCE`). It kept two: the Chinese-tailed `f04` sentence, and `counts connections if idle`. It also accepted `d15`, a fullwidth sentence the checker had skipped. That is the third false admission.

`d10`, `d11`, and `d17` were gold `INSUFFICIENT_EVIDENCE` and the model said `CONTRADICTED`. Those were not stored, so they are not false admissions. They are stricter than the gold label.

## Cost

API usage: 5,865 input tokens, 1,774 output tokens. At $0.40 / 1M input and $1.60 / 1M output, **$0.005184**. Wall time 43.7 seconds, mean 1.46 seconds per claim. No memory write.

## Who should verify what

| Claim | Owner | Why |
|---|---|---|
| All 10 `lexical_pass` accepts | AI shadow | The reason does not separate the 3 true accepts from the 7 false ones |
| 9 supported `residue`/`order` skips | AI shadow | The rule drops paraphrases, renames, and `d03`'s true order paraphrase |
| 9 negative-control skips | Rule | The checker already refused them, including `d08`'s false order claim. A model that flips these has failed |
| `f04` English prefix and `f09` semicolon prefix | Escalate | Fix clause splitting. A model label on the fragment is not the stored claim |
| `f05` panic, `f11` truncation race, `f12` multi-evidence | Escalate | No trace, a timing race, or one `support_event_id` for two observations |

## Integration

Do not put this model on the admission path. Recall is solved on this set, and false admissions fell from 7 to 3, but the three remaining accepts are the failures that matter: a quantifier reversal (`if idle` versus `not item.idle`) and two sentences whose non-English tail the model ignored while citing the true English prefix. One of those, `d15`, is a claim the current rule already refused. A 1.5 second call per claim also does not belong under the SQLite writer lock.

Use the model as a shadow on `lexical_pass` accepts and on supported `residue`/`order` skips. Keep the rule as a veto on negative-control skips until a later run stops accepting non-English tails and reversed quantifiers. Splitter bugs (`f04` English prefix, `f09` semicolon) stay an escalation, not a model admission.
