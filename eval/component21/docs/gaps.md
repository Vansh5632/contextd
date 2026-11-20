# Gaps

These items are unresolved. They are not passes.

## Not stably reproduced

- Compaction. The 20 November rescore marks `f10` incomplete, not a pass. The long output was removed and the conclusion was skipped. It did not catch a claim that had already been admitted and then lost its output. Enrichment can remove the output before the first handoff.
- Truncation. `f11` is incomplete, not a pass. This baseline skipped the prefix claim. An earlier run of the same input admitted it. Handoff and enrichment race, so one outcome is not a stable reproduction.
- Semicolon `so`. The supported prefix was kept and the whole sentence was not stored. The bad tail was absent, which is vacuous, not a semantic refusal. The semicolon shape did drop the supported prefix.
- `might`. It stayed a hypothesis.
- `probably`. It was dropped, which fails the uncertainty contract, but it was not stored as an established fact in this run.
- Multiple evidence. The chosen combined sentence was skipped, so `f12` is vacuous. The protocol still carries one support id, so composition is untested.
- Done, then fail again. The task stayed `active` through fail, pass, and fail. The projector did replace the exit-0 reality with exit 1. That does not show a Done task remaining Done.

## Not executed

- Token-budget trim. `context_now` has no budget parameter. Status `blocked`, reason `unobservable`.
- Shell-hook stdout. Hooks record the command, directory, and exit code. Status `blocked`, reason `open_contract`. The case was not run.
- Live continuation matrix. Status `blocked`, reason `spend_not_authorized`. Synthetic traces only calibrate the credit rule.

## Open contracts

`agent_claim` stored an evidence-free sentence as current inferred reality. The case is blocked and excluded from semantic rates. Whether that route should enter the evidence check is a product decision, not a hidden pass.

Held-out labels have no independent second review.

Evidence-reference accuracy on the 20 November rescore is 15/15 for stored regression and development claims that declare a snippet. That means the cited event contains the snippet, including the seven false admissions. It is not semantic support. The unit tests cover a wrong evidence id and a snippet missing from the cited payload. The current natural route cites the conclusion and its support event together, so a wrong id is not separately observable there. Held-out evidence stays 0/0 because the sealed file was not edited.

The historical regex import is a side channel. It scored the two old packets at recall 1.0 and is not used for any contract decision.

## Next milestone

Replace the grounding check and score that replacement only through this harness. Keep the sealed held-out file out of the regression and development metrics. Do not use regex-atom recall as the gate. Do not count a vacuous skip as a semantic pass. Do not change production behavior in order to satisfy a case; change the component, then rerun `python -m harness.run`.

A live agent comparison remains a separate, explicitly authorized experiment.
