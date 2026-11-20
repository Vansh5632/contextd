# Component 2.1 evaluation architecture

This harness answers six questions separately:

1. What did contextd observe?
2. What did the agent conclude?
3. What does the supplied evidence establish?
4. What was stored, and what was shown?
5. Is stored knowledge still valid?
6. Would the next agent benefit?

It does not answer them with one quality score.

## What is scored

Each atomic statement has its own gold label: `SUPPORTED`, `CONTRADICTED`, or `INSUFFICIENT_EVIDENCE`. The label is about the observation supplied in the case, not about whether the statement is true in a live repository. Statement order is not execution order.

A case result is one of:

- `pass`: the case ran and matched its contract.
- `fail`: the case ran and violated its contract. That is a successful measurement.
- `blocked`: the case did not produce a contract judgment. Reasons include a missing artifact, a property the protocol cannot show, an open product contract, unauthorized model spend, or a timeout.

`contract_holds` is true, false, or null. A blocked case has null and is left out of metric denominators.

## Observers

The public observer speaks the daemon protocol: `observe`, `handoff`, `now`, `agent_claim`, `user_claim`, and `intent`. It records the response, including accepted, rejected, and skipped handoff units, and any transport error such as `query handler unavailable`.

The store observer opens the case database read-only. It is allowed to see claim status, task id, origin, evidence ids, and event timestamps. Those fields are hidden from `now`. A property that neither observer can see is `blocked`, not invented. There is no verification bit distinct from confidence. An authored route that skips the evidence check is labeled `admitted_without_gate`.

The ordering oracle is `client_seq`, the order in which the harness submitted messages. Daemon timestamps are not that oracle. Extracted claims are stamped later than the note that produced them.

Checker rejection and renderer exclusion are different. `handoff.skipped` or `handoff.rejected` means the checker did not accept the unit. A unit can also be accepted and then absent from `now`. The current baseline did not observe that second case.

## What is not the judge

The production lexical matcher, `eval/component0/harness/evidence_probe.py`, and the regex atoms in Component 0 episodes are not the factuality oracle. Regex scoring of the old limiter and retry-budget packets is an optional side channel. It reports its own recall and is marked `factuality_oracle: false`.

Gold labels are written by hand in the case sources. This milestone does not call a model judge. The paid continuation matrix is not run; its case is `blocked` with `spend_not_authorized`.

## Layout

- `harness/` drives an isolated daemon, reads SQLite, judges atoms, and computes metrics.
- `datasets/` holds the regression, development, held-out, and continuation cases. `datasets/freeze.py` writes the JSON the runner loads.
- `datasets/heldout/SHA256SUMS` seals the held-out file. The development generator does not read it.
- `tests/` checks the grader against planted corruptions. Those tests do not need a daemon.
- `baseline/results.json` is the recorded run against the unmodified debug binary.

The daemon is configured like the existing live protocol test: a temporary directory, a Unix socket, and an Ollama URL on `127.0.0.1:9` so enrichment cannot reach a model. No eval service is required.

## Metrics

Rates keep their own denominators, and held-out atoms are not pooled into the regression or development rates. A skipped false claim is vacuous: it is not a semantic refusal, and it is not in the false-admission denominator. A system that admits nothing has supported-claim precision `null`, not 1. A system that admits everything fails false-claim admission. Evidence citation, uncertainty, restart state, and process restart are separate rates. Continuation credit is scored from supplied traces, not from a live agent. The corrected baseline reading is [integrity-report.md](integrity-report.md).
