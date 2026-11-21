# Experiment 5: risk-aware verification router

The router is shadow-only. It does not write memory and does not change admission. Held-out rows are dropped before routing.

## Policy

`harness/router.py` reads the claim and the tool output.

| Situation | Route | Label |
|---|---|---|
| Empty evidence | Abstain | `INSUFFICIENT_EVIDENCE` |
| The same identifier is incremented and assigned a different number | Abstain | `INSUFFICIENT_EVIDENCE` |
| `if P` against `not P` in the output | Deterministic | `CONTRADICTED` |
| `never increases` or `never increments` while the output shows that increment | Deterministic | `CONTRADICTED` |
| `increases by N` while the output shows a different amount, including `++` as 1 | Deterministic | `CONTRADICTED` |
| `ident increases by N before ident2`, the output has that increment before the call, and the call is not earlier | Deterministic | `SUPPORTED` |
| The call appears before the increment | Deterministic | `CONTRADICTED` |
| A non-English atom, or a later clause whose words are absent from the output | Deterministic | `INSUFFICIENT_EVIDENCE` |
| Anything else, including paraphrases and `increments before` without an amount | Semantic | Atomic verifier from Experiment 4, including one recheck of abstentions |

Lexical overlap is not enough for `SUPPORTED`. A model error, timeout, or missing client labels the semantic claim `INSUFFICIENT_EVIDENCE` and returns. The caller does not raise.

## Command

```bash
cd eval/component21
python3 -m harness.router \
  --from-experiment /tmp/c21-shadow-experiment.json \
  --output /tmp/c21-router-experiment.json \
  --repeat 2 \
  --i-accept-spend
```

Same 30 claims as Experiments 3 and 4. `gpt-4.1-mini`, temperature 0. The key came from `.env` and is not in the results file.

## Results

Routes on this set: 16 deterministic, 13 semantic, 1 abstention (`d17`, conflicting `+= 1` and `= 0`).

| Rate | Deterministic only | Experiment 4 | Router, pass 1 | Router, pass 2 |
|---|---|---|---|---|
| False-claim admission | 0/16 | 0/16 | 0/16 | 0/16 |
| Supported recall | 7/14 | 14/14 | 14/14 | 13/14 |
| Precision | 7/7 | 14/14 | 14/14 | 13/13 |
| Evidence quote in the output | — | 30/30 | 29/30 | 29/30 |

The deterministic floor proves the seven literal `increases by 1 before ...` claims, including Rust and Go, and refuses the reversed call, the wrong amount, `never`, `if idle`, and the non-English tails. It abstains on the other seven supported claims. The semantic route recovers those seven on pass 1, including `d03` after its recheck.

Pass 2 loses `d03` again: the recheck abstained. False admissions stay 0. Label agreement is 29/30. That one change is `d03`, not a false admission.

The 29/30 evidence score is `d08`. The reversed-order contradiction was right, and its quote was empty, so the quote gate does not count it. The quote now cites the earlier call. That fix was not in the measured file.

## Cost

Semantic route only, both passes, including rechecks: 39 model calls, 18,672 input tokens, 5,415 output tokens, **$0.01613**. Experiment 4's two full passes cost **$0.02119** (25,814 input and 6,788 output tokens). This run is about 24 percent cheaper and makes about half as many model calls. Pass 1 semantic time was 36.8 seconds for 13 claims and 19 calls.

## Trade-off

Pass 1 matches Experiment 4's 0/16 and 14/14 at a lower cost. Pass 2 does not: `d03` is unstable under the recheck, so supported recall is 13/14. The literal route removes that variance for Rust, Go, and the plain increment claims, which no longer touch the model. Do not put the router on the admission path. A timeout already fails closed. The remaining risk is a paraphrase the recheck sometimes refuses, not a false supported label.
