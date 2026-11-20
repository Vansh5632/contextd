# Validity audit

The grader is tested without the daemon. `tests/test_grader_validity.py` plants corrupt checkpoints and checks the judgment.

| Planted defect | Result |
|---|---|
| Contradicted sentence stored as an established reality | Fail, `presented_as_reality` |
| Supported sentence absent | Fail, `not_presented` |
| Claim cites a different observation than the one containing the snippet | Fail, `evidence_mismatch` |
| Cited observation does not contain the snippet | Fail, `evidence_mismatch` |
| Cited observation is missing from the store | Fail, `evidence_missing` |
| Snippet is present in the cited observation | Pass, `evidence_match` |
| Contradicted sentence skipped by the checker | Vacuous, not a semantic pass. False-admission denominator excludes it |
| Contradicted sentence explicitly rejected | Pass, and it counts as a covered non-admission |
| Uncertain sentence kept, dropped, or stored as reality | Pass, `uncertainty_dropped`, or `presented_as_reality` |
| Incomplete evidence not shown | `incomplete`, not a contract pass |
| Incomplete evidence shown as reality | Fail |
| Supported claim accepted and then omitted from the brief | Fail, `renderer_excluded` |
| Stale claim still current | Fail, `stale_current` |
| Agent note stored as user | Fail, `origin_user` |
| Claim attached to another task | Fail, `wrong_task` |
| `query handler unavailable` | Fail, not treated as an empty success |
| Regex matches `retry_count before open_socket` inside a negation | Atomic judge fails; regex side channel still matches |
| No established claims at all | Supported precision is null, not 1 |
| Poor handoff and a solved trace | Credit goes to the agent layer |
| Good handoff and a failed trace | Credit goes to the agent layer |
| Good handoff and a trace whose wrong edit came from the handoff | Credit goes to the memory layer |

`tests/test_schema.py` checks that frozen JSON matches the Python sources, that failure categories 1 through 20 exist beside the supported control, and that every daemon atom has factuality, a justification, and an expectation. `tests/test_heldout_seal.py` checks the checksum and version. `tests/test_metamorphic.py` checks that the generator does not name the held-out suite. `tests/test_baseline_file.py` checks that the recorded file uses only pass, fail, and blocked, and that pass and fail agree with `contract_holds`.

These tests passed locally: 40 tests, on 20 November 2026, including the baseline-file check. `baseline/results.json` remains the pre-repair snapshot. `baseline/integrity-after.json` is the `integrity-2` rescore and is not a clean-tree official file.

## What the daemon baseline adds

The live cases separate false admission, a handler panic, wrong task, wrong author, stale-as-current, a timestamp-reversed correction, and a cross-task retract. This run's checker accepted or skipped; it did not record an explicit rejection. The supported control shows that recall credit is possible.

## What would make the framework untrustworthy

- Counting a skip of unfamiliar wording as proof that negation or order was understood. Scorer `integrity-2` marks that atom vacuous and leaves it out of the false-admission denominator.
- Treating regex recall as factuality. The side channel is marked otherwise, and the planted negation disagrees with it. The regex case is a grader self-test, not a daemon pass.
- Labeling a source prediction as reproduced when the run did not set the flag. Compaction, the recorded truncation outcome, the `so` tail, and `might` stay `not_observed`.
- Scoring `agent_claim` as a product failure while that route is an open contract. It is recorded and excluded from the semantic denominators.
- Giving a reject-all run perfect precision. Empty admission leaves precision null.

## Comparison use

A replacement grounding component can be compared on this harness if it is scored only through these cases, the sealed held-out file is not used for tuning, and regex-atom recall is not the acceptance gate. The comparison is about the deterministic contract. It does not say the next agent will do better work. Suite D, the live continuation matrix, was not run.
