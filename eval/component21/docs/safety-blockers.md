# Production safety blockers

Three daemon bugs blocked any later use of the shadow verifier. The verifier is still not on the admission path. These fixes are only about not crashing, not treating a cut-off listing as proof, and not storing an evidence-free agent sentence as an established reality.

## What changed

`evidence_clauses` used to lowercase a sentence and then slice the original text with the lowercase byte indexes. `Ω` is three bytes and lowercases to `ω`, which is two, so `Ω so retry_count increments before open_socket` panicked the query handler. Clause splits now keep a map from each lowercase byte back to the original byte, and every slice stays on the original string.

A tool observation longer than 200 lines or 8,000 characters is stored with `[output truncated]`. Extraction now treats that marker, and a shell output that has been removed, as incomplete. A reality is not admitted from it. If a reality was admitted while the output was still complete and a later handoff finds the output cut off or gone, that reality is superseded and the same words are kept as a hypothesis.

`note_claim` with `kind=reality` has no tool output. `as_unverified` stores that request as `unknown` / `hypothesis`. Decisions, ruled-out claims, and next actions are unchanged. The response includes the stored `kind` and `confidence`.

## Commands

```bash
cargo test -p contextd-core --lib claim::tests::an_evidence_free_reality_is_stored_as_a_hypothesis
cargo test -p broker --lib the_f05_omega_note_does_not_panic_and_survives_a_restart
cargo test -p broker --lib a_truncated_listing_does_not_verify_the_visible_prefix
cargo test -p broker --lib a_restart_withdraws_a_reality_after_its_output_is_cut_off
cargo test -p broker --lib
cargo clippy -p broker -p contextd-core -p contextd --all-targets -- -D warnings
cd eval/component21
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests -q --tb=line -p pytest
cargo build -p contextd --bin contextd
python3 -m harness.run --suite regression --suite development --suite continuation \
  --allow-dirty --reuse-binary --output /tmp/c21-safety-blockers.json
```

The daemon run is dirty, so `official` is false. It is not a replacement for `baseline/integrity-after.json`.

## Results

Broker library tests: 86 passed. Core claim tests: 10 passed. Component 2.1 pytest: 90 passed. Clippy on `broker`, `contextd-core`, and `contextd` is clean.

Daemon suites regression, development, and continuation, compared with `baseline/integrity-after.json` on the same case ids:

| Case | Baseline | This run |
|---|---|---|
| `f05-unicode-panic` | fail | pass |
| `f10-compaction` | incomplete | incomplete |
| `f11-truncation` | incomplete | incomplete |
| `f19-note-claim` | blocked | blocked |

No other case changed status. Totals for these three suites: 7 pass, 25 fail, 11 vacuous, 2 incomplete, 5 blocked, 0 harness errors. The baseline file also contains the six held-out cases, which this command did not rerun.

`f05` no longer panics. The handler atom passes. `f11` did not present `retry_count increments`; the atom is `incomplete`, with `incomplete_not_admitted`. `f10` likewise did not present the claim after the output was gone. `f19` still has `judgment: open_contract`, so the harness blocks it, but the store row for “the pool is exhausted” is `unknown` / `hypothesis`, not an inferred reality. `stored_current_reality` is false.

## Remaining failures

The lexical false admissions are unchanged: negation, the wrong amount, execution order, quantifiers, and causality still fail. Paraphrases are still skipped. `f17-timestamp-order` still fails. `f19-note-claim` stays blocked until its case file stops saying the contract is open. Held-out was not part of this daemon run.
