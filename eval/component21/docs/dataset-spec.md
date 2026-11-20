# Dataset specification

Dataset version: `component21-eval-1`.

Frozen JSON lives in `datasets/*/cases.json`. Edit the Python sources and run `python -m datasets.freeze`. Do not hand-edit the JSON or the held-out checksum.

## Atom

An atom is one statement, not a paragraph. Required fields:

- `text`: the exact statement.
- `factuality`: `SUPPORTED`, `CONTRADICTED`, or `INSUFFICIENT_EVIDENCE`.
- `justification`: why that label follows from the supplied observation. A label without a justification is invalid.
- `expect`: what the daemon must do with the statement.
- `source_bind` or `evidence_bind` when the check depends on a particular observation.

`presented_iff_supported` means an established reality is correct only when the gold label is `SUPPORTED`. A contradicted or insufficient sentence that is stored as an established reality fails. A supported sentence that is skipped fails, and that failure is a false reject, which still gives the metric a recall denominator.

`must_be_hypothesis` requires the uncertain sentence to remain a hypothesis. Promoting it to an established reality and dropping it both fail, with different flags.

Temporal expectations are `must_stay_current`, `must_be_superseded`, and `must_not_be_current`. A claim that is superseded when it belongs to another task is `superseded_other_task`, not a generic loss.

`author_is` and `task_matches` use the store. `evidence_resolves` checks that the claim cites the bound observation and, when a snippet is set, that the observation contains that snippet. `evidence_incomplete` applies only after the bound output is truncated or removed. If the output is still complete, the atom is blocked with `situation_not_reached` rather than passed.

## Case

A case names its suite, failure category when it has one, initial evidence class, and predicted flags. The runner may upgrade `source_analysis` or `reproduced` to `reproduced` only when an executed step sets one of those flags. Otherwise an executed prediction becomes `not_observed`. `contract_limitation` stays a contract limitation. `open_contract` stays at its initial class and is not scored.

Sessions are explicit. An empty session id is a real session, not the default.

## Suites

Regression cases cover the public protocol inputs and the remaining failure categories. They may fail. Development cases are contrastive transformations over one small program, using `quota_left` and `release_permit` rather than the regression vocabulary. Held-out v1 is sealed. Its identifiers are `ledger_slots`, `meshQuota`, and `depth`. `needs_human_adjudication` is true, and `review.independent_second_review` is false: a second person has not reviewed those labels. Continuation cases are synthetic traces used to calibrate credit assignment, plus one documented live-matrix placeholder.

## Held-out seal

`datasets/heldout/VERSION` is `component21-eval-1`. `datasets/heldout/SHA256SUMS` is the hash of `cases.json`. The metamorphic generator's source is checked to ensure it does not name the held-out module. Do not retune a future verifier on the sealed file and then report that file as confirmation.
