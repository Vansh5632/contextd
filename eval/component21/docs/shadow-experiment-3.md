# Experiment 3: atomic claim verification

`gpt-4.1-mini` labels atoms. Python decides the sentence. A sentence is `SUPPORTED` only when every atom is `SUPPORTED`. One supported clause cannot carry an unsupported tail. Production admission is unchanged. Held-out cases were not sent. `writes_memory` is false.

## Implementation

`harness/atomic.py`

- `decompose` splits on `。！？；` and `;`, then keeps a conditional sentence whole and adds its `if` / `only when` / `unless` tail as a second atom. A negation such as `never increases` stays one atom.
- The model returns `label`, `evidence_quote`, and `reasoning` for each atom. A quote that is not copied from the tool output becomes `INSUFFICIENT_EVIDENCE`.
- `polarity_conflict` marks `if P` contradicted when the evidence has `not` immediately before `P`, and leaves `if not P` alone when the evidence is also negative.
- `aggregate` returns `CONTRADICTED` if any atom is contradicted, `SUPPORTED` only if all are supported, otherwise `INSUFFICIENT_EVIDENCE`.

## Command

```bash
cd eval/component21
python3 -m harness.atomic \
  --from-experiment /tmp/c21-shadow-experiment.json \
  --output /tmp/c21-atomic-experiment.json \
  --repeat 2 \
  --i-accept-spend
```

The key is `CODEX_API_KEY` in `.env`, passed as `OPENAI_API_KEY`. It is not written to the results. Two passes, temperature 0, same 30 claims as experiment 2.

## Metrics

| Rate | Experiment 2, one label per sentence | Experiment 3, pass 1 | Experiment 3, pass 2 |
|---|---|---|---|
| False-claim admission | 3/16 = 0.19 | 0/16 = 0 | 0/16 = 0 |
| Supported recall | 14/14 = 1.00 | 13/14 = 0.93 | 12/14 = 0.86 |
| Precision | 14/17 = 0.82 | 13/13 = 1.00 | 12/12 = 1.00 |
| Evidence quote in the tool output | 30/30 | 30/30 | 30/30 |

Atomic coverage: 7 of 30 claims split, 37 atoms. Label agreement across the two passes: 29/30 = 0.97. The disagreeing claim is `d14-rust`, supported on pass 1 and insufficient on pass 2.

API usage across both passes: 22,062 input tokens and 5,278 output tokens, **$0.01727**. Mean latency 1.70 seconds per call, 60 calls, 111 seconds.

## Known failures

| Case | Experiment 2 | Experiment 3, both passes |
|---|---|---|
| `f07` `counts connections if idle` | `SUPPORTED` | `CONTRADICTED`. The condition atom disagrees with `if not item.idle`. |
| `f07` `if not idle` | `SUPPORTED` | `SUPPORTED` |
| `f04` sentence with the Chinese tail | `SUPPORTED` | `INSUFFICIENT_EVIDENCE`. The English atom is supported and `数据库连接池耗尽` is not. |
| `d15` fullwidth tail | `SUPPORTED` | `INSUFFICIENT_EVIDENCE`. `许可已经耗尽` is its own atom. |
| `f09` semicolon plus "database pool" | `INSUFFICIENT_EVIDENCE` | `INSUFFICIENT_EVIDENCE`. The pool clause is unsupported. |

## What recall lost

`d03-paraphrase`, "the quota left counter grows by one before the permit is released", is `INSUFFICIENT_EVIDENCE` on both passes. The listing shows `quota_left += 1` before `release_permit()`. The model will not treat that call as "the permit is released". A stronger paraphrase instruction was tried and recall fell to 10/14, so that wording was removed. The measured system is the milder prompt.

`d14-rust` is the unstable claim. It is the same shape as several supported claims that stayed supported, so this is model variance at temperature 0, not a split error.

## Trade-off

False admissions on this set fell from 3 to 0, and the three experiment-2 errors are fixed on both repeats. Supported recall is no longer 14/14. The stable miss is one paraphrase. Repeatability of the headline recall is 13/14 then 12/14. Do not move this aggregator into `run_handoff` until a repeat holds 14/14 without letting a tail or a reversed condition through.
