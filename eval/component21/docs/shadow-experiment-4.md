# Experiment 4: reliability and uncertainty recovery

A second pass looks only at atoms the first pass left `INSUFFICIENT_EVIDENCE`. It may abstain. It cannot become `SUPPORTED` without a quote copied from the tool output. Production admission is unchanged. Held-out cases were not sent.

## Gold audit

`d03-paraphrase` stays `SUPPORTED`. The listing is:

```python
def release(self, permit):
    self.quota_left += 1
    self.release_permit()
```

`quota_left += 1` is the counter growing by one. The next statement is the `release_permit` call. The callee body is not in the listing. The identity claim on this same listing is already gold `SUPPORTED`, and its justification treats that call as the release. Relabeling the paraphrase would make the two claims inconsistent. The Experiment 3 miss was the model abstaining, not a bad label.

`d14-rust` stays `SUPPORTED`. The Rust listing is `quota_left += 1;` before `release_permit();`. A pass that flips this is model variance.

## What changed

`harness/atomic.py`

- `decompose` also splits `because` into the two assertions it joins. `before` stays one ordered assertion.
- `coverage_report` checks clause boundaries, conditions, negations, `because`, and `before`.
- `uncertain_atoms` selects first-pass abstentions. Polarity contradictions are not reopened.
- `build_reverify_payload` asks for one atom. The instruction says abstaining is correct.
- `apply_second_pass` keeps `INSUFFICIENT_EVIDENCE` when the new label has no quote, or the quote is not in the evidence.

## Command

```bash
cd eval/component21
python3 -m harness.atomic \
  --from-experiment /tmp/c21-shadow-experiment.json \
  --output /tmp/c21-atomic-experiment-4.json \
  --repeat 2 \
  --i-accept-spend
```

`CODEX_API_KEY` from `.env` was passed as `OPENAI_API_KEY`. It is not in the results file. Model `gpt-4.1-mini`, temperature 0, two passes, the same 30 claims.

## Results

| Rate | Experiment 3 | Experiment 4, pass 1 | Experiment 4, pass 2 |
|---|---|---|---|
| False-claim admission | 0/16 | 0/16 | 0/16 |
| Supported recall | 12/14 to 13/14 | 14/14 | 14/14 |
| Precision | 12/12 to 13/13 | 14/14 | 14/14 |
| Evidence quote in the tool output | 30/30 | 30/30 | 30/30 |

Atomic coverage on these 30 claims: 30/30 marker checks passed. 7 claims are split, 37 atoms. Pass 1 rechecked 11 abstentions and changed 3 labels. `d03` moved from `INSUFFICIENT_EVIDENCE` to `SUPPORTED` with the quote `self.quota_left += 1`. The Chinese and fullwidth tails stayed `INSUFFICIENT_EVIDENCE`. `if idle` stayed `CONTRADICTED`. `d14-rust` was `SUPPORTED` on both passes, with no recheck.

Label agreement is 29/30. The one change is `d10-conditional`: `CONTRADICTED` on pass 1 and `INSUFFICIENT_EVIDENCE` on pass 2. The gold label is `INSUFFICIENT_EVIDENCE`. Neither label is a false admission, and both passes have the same false-admission and recall counts.

API usage across both passes, including rechecks: 25,814 input tokens, 6,788 output tokens, **$0.02119**. Mean time per claim record is 2.47 seconds. The 60 records include the recheck time. No memory write.

## Remaining gap

`d10` still moves between contradicted and insufficient. That does not admit a false claim. It means a repeated run can disagree on how hard to reject an unsupported condition. The second pass does not force those to supported.

## Trade-off

Recall on this set is back to 14/14, and false admissions stay at 0, because abstentions are retried once and then left alone when the quote is missing. The extra cost over Experiment 3 is about half a cent on this set. Do not put the recheck on the admission path. A tail or a reversed condition is still rejected by the atom rule, and a second pass is not allowed to overrule that.
