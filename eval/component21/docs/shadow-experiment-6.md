# Experiment 6: generalization and safety

The router stays in shadow mode. It does not write memory and it does not change admission. Held-out gold was read once, the rules were frozen, and the sealed claims were sent to the model once. Nothing in this file was used to change a rule.

## What “supported” is allowed to mean

A deterministic `SUPPORTED` label is only the straight-line proof: one `+= N`, `++`, `-= N`, or `--`, then the named call, both statements directly under one function. Comments and string contents are blanked before that search. A loop, a branch, a second update, an alias, or the word `truncated` is not a proof. Those claims go to the atomic verifier or abstain.

## Gold review

`datasets/define_heldout.py` still says a second reviewer had not signed the seal. This reading agrees with all six frozen labels and does not edit them. The checksum is unchanged. Each claim is scored against the `source` listing only. The earlier `probe failed` observation is not evidence.

| Case | Gold | Why it stays |
|---|---|---|
| `h01-rust-supported` | `SUPPORTED` | `ledger_slots += 1` and then `commit_batch()` in `settle` |
| `h02-rust-order` | `CONTRADICTED` | `commit_batch()` runs before the increment |
| `h03-js-decrement` | `SUPPORTED` | `meshQuota -= 1` and then `closeChannel()` |
| `h04-js-negation` | `CONTRADICTED` | `drain` does decrease `meshQuota` |
| `h05-go-guard` | `INSUFFICIENT_EVIDENCE` | `depth--` is inside `if q.depth > 0`, not on every `pop` |
| `h06-mixed-tail` | `INSUFFICIENT_EVIDENCE` | The Spanish sentence is not in the listing |

## Adversarial coverage

`harness/adversarial.py` has 15 claims. None of them use the sealed identifiers. `evaluate()` scored 15/15 with no model.

| Topic | What the listing does | Required route |
|---|---|---|
| Straight line | `+= 1` then the call, and `-= 1` then the call | Deterministic `SUPPORTED` |
| Comment | A commented increment, or a `/* */` increment, before the real reversed body | Deterministic `CONTRADICTED` |
| String | The increment exists only inside quotes | Semantic |
| Loop | The increment is in a `for`, whether or not the call is in that loop | Semantic |
| Branch | `-= 1` only inside `if` | Semantic |
| Alias | `slots = quota_left` and then `slots += 1` | Semantic |
| Two updates | `+= 1` twice | Semantic |
| Contradiction | `+= 1` and a later `= 0` | Abstain |
| Incomplete | The listing ends with `[output truncated]` | Abstain |
| Negation | `never decreases` while `-= 1` is shown | Deterministic `CONTRADICTED` |
| Multilingual | A Spanish sentence after a true prefix | Deterministic `INSUFFICIENT_EVIDENCE` |
| Missing | Blank evidence | Abstain |

## Reliability

These are separate from the router.

| Check | Owner | Result |
|---|---|---|
| Omega note (`f05-unicode-panic`) | Product | The daemon query handler still panics, as recorded in the integrity report. `route_claim` on that same note returns twice with the same non-supported decision and does not raise. |
| Truncation race (`f11-truncation`) | Product | Handoff can still race enrichment. If the text contains `truncated`, the router abstains instead of proving the prefix. |
| API timeout | Router | `test_model_failure_fails_closed_without_raising` still turns a timeout into `INSUFFICIENT_EVIDENCE` with `writes_memory` false. |
| Restart | Router | The same claim and listing produce the same decision. Routing has no process state. |
| Task isolation | Router | Two rows in one batch keep their own listings. One can be `SUPPORTED` while the other is `CONTRADICTED`. |
| `note_claim` | Product | `record_agent_claim` stores an agent claim with empty `evidence_ids`. It does not call the router or `run_shadow`. |

## Commands

From `eval/component21`. The paid command reads `CODEX_API_KEY` from the repo `.env` and does not print it. The held-out model run is the file `/tmp/c21-experiment-6.json`.

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests -q --tb=line -p pytest
python3 -m harness.generalization --output /tmp/c21-experiment-6-plan.json
set -a && source ../../.env && set +a
export OPENAI_API_KEY="$CODEX_API_KEY"
python3 -m harness.generalization --output /tmp/c21-experiment-6.json --i-accept-spend
```

The plan command is the adversarial score plus held-out routes with no model. The paid command was not repeated. `git rev-parse HEAD` at the start of that process was `aaff2a91d911089bf1e4af0da1a9a0772c600529`, and the worktree already contained the safety changes. After that call, `apply_routes` was taught to keep `prompt_tokens` and `completion_tokens` on a semantic row. That does not change a label, and the sealed claims were not sent again. The stored hybrid dollar total is therefore 0 even though one call was made.

Pytest after that reporting fix: 90 passed.

## Held-out results

Routes before the model: 5 deterministic, 1 semantic (`h05-go-guard`), 0 abstentions. Model: `gpt-4.1-mini`, temperature 0, one pass.

| Rate | Deterministic only | Hybrid router | Full atomic pass |
|---|---|---|---|
| False-claim admission | 0/4 | 0/4 | 0/4 |
| Supported recall | 2/2 | 2/2 | 2/2 |
| Precision | 2/2 | 2/2 | 2/2 |
| Evidence quote in the listing | 6/6 | 6/6 | 6/6 |

`h01` and `h03` are the two supported claims. Both are the straight-line shape with identifiers that were not in the earlier 30. The router proved them with no model call. `h02`, `h04`, and `h06` were refused deterministically. `h05` was the only model call.

| Case | Gold | Hybrid | Atomic |
|---|---|---|---|
| `h01` | `SUPPORTED` | `SUPPORTED`, no call | `SUPPORTED`, 2 calls, recheck |
| `h02` | `CONTRADICTED` | `CONTRADICTED`, no call | `INSUFFICIENT_EVIDENCE`, 2 calls |
| `h03` | `SUPPORTED` | `SUPPORTED`, no call | `SUPPORTED`, 1 call |
| `h04` | `CONTRADICTED` | `CONTRADICTED`, no call | `CONTRADICTED`, 1 call |
| `h05` | `INSUFFICIENT_EVIDENCE` | `CONTRADICTED`, 1 call | `CONTRADICTED`, 1 call |
| `h06` | `INSUFFICIENT_EVIDENCE` | `INSUFFICIENT_EVIDENCE`, no call | `INSUFFICIENT_EVIDENCE`, 3 calls |

`h05` is not a false admission. Neither system labeled it `SUPPORTED`. Both called the guarded decrement a contradiction. The frozen gold says the evidence is incomplete for “whenever pop is called,” because the decrement does happen when `depth > 0`. That disagreement stands.

`h02` is the other miss, and it belongs to the full atomic pass. The recheck abstained, so the label became `INSUFFICIENT_EVIDENCE`. The router had already marked the reversed call `CONTRADICTED` without a model.

## Cost against Experiment 4 and Experiment 5

Experiment 5, on the original 30 claims, was 0/16 false admissions and 13/14 then 14/14 supported recall, at about $0.01613 against Experiment 4’s $0.02119. That was about 24 percent less spend, and it was not rerun here.

On the six sealed claims the atomic pass made 10 calls, 2,900 input tokens, 708 output tokens, **$0.0022928**, in 14.05 seconds (mean 2.34 seconds). The hybrid pass made **1** call. Its token bill was not stored. One call against ten is the measured saving. The five claims that match a sound literal rule never reached the model, which is why `h02` did not depend on the recheck that abstained.

Supported recall did not rise above the deterministic floor, because both true sealed claims were literal proofs. This set does not contain a paraphrase. Experiment 5 is still the evidence for those.

## What did not generalize

The sealed set is six syntactic variants of the same small listing, not a new kind of claim. A comment, a string, a loop, an alias, or a second update is refused by the unit tests, but those situations were not in the sealed file. `h05` shows the model will answer a guarded universal claim with `CONTRADICTED` where the gold says the evidence is only insufficient. That is safe under the false-admission rule and wrong if a contradiction is supposed to mean the event never happens.

Do not put this router on the admission path. The omega panic, the truncated-evidence admission, and the `note_claim` reality bypass were later fixed in the daemon. See [safety-blockers.md](safety-blockers.md).
