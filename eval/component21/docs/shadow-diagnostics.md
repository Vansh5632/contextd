# Shadow diagnostics

Scorer `integrity-2`, plus a diagnostics block that does not change admission. No model was called. Held-out cases stay sealed and are excluded from the reason lists.

## Commands

```bash
cargo test -p broker --lib extract::tests
cargo test -p broker --lib shadow::tests
cd eval/component21
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests -q --tb=line -p pytest
python3 -m harness.run --allow-dirty --reuse-binary --output /tmp/c21-shadow-diagnostics.json
```

The daemon rescore that matched the recorded contract was written at revision `133843d17cb1f09d172e7f5dd686ae60f71e4b25` with a dirty worktree, so `official` is false. `model` is null. Case statuses match `baseline/integrity-after.json` with no differences: 6 pass, 28 fail, 15 vacuous, 2 incomplete, 5 blocked, 0 harness errors.

Regression semantic rates are unchanged: supported recall 4/8, false-claim admission 7/7, precision 4/11.

`f11-truncation` is the known handoff/enrichment race. One instrumented run admitted the truncated prefix (`fail`). The run recorded here skipped it (`incomplete`), which is the baseline outcome. Do not treat a single `f11` status as stable.

## What was added

`DecisionTrace` rows are attached to the handoff report from `keep_clause`, `apply_extraction`, and `admit`. Reason codes include `lexical_pass`, `polarity`, `order`, `residue`, `unanchored`, `low_overlap`, `empty_support`, `hedge`, `speech_act`, and `duplicate`. The rows do not decide admission.

`broker::shadow` classifies a `ShadowRequest` as `SUPPORTED`, `CONTRADICTED`, or `INSUFFICIENT_EVIDENCE` and can cite a span of the evidence text. `run_shadow` takes no SQLite connection and forces `writes_memory` to false. `run_handoff` does not call it. The evaluation default is `AbstainingShadow`, which labels every candidate `INSUFFICIENT_EVIDENCE` and cites nothing.

An atom receives a reason only when a trace has the same text. A longer accepted sentence does not donate `lexical_pass` to a shorter gold atom. That shorter atom can still name the enclosing trace as `parent_reason`.

## Regression, exact traces

36 gold atoms. 32 have an exact trace. Four cases have at least one atom without one: `f04-unicode-tail`, `f05-unicode-panic`, `f09-semicolon`, `f09-so-split`. `f05` records no trace because `evidence_clauses` still panics before the diagnostic vec is written.

### False claims accepted

All seven are `lexical_pass` at `admit`, and all seven are presented:

| Case | Claim |
|---|---|
| `f01-negation` | `retry_count never increments before open_socket` |
| `f02-numeric` | `retry_count increments by 999 before open_socket` |
| `f04-unicode-tail` | the sentence with the Chinese tail |
| `f06-execution-order` | `attempt_budget increments before open_socket` |
| `f07-quantifier` | `counts connections if idle` |
| `f08-causality` | both directions |

These are the claims a semantic verifier has to review even though the deterministic checker already accepted them.

### Correct claims skipped

| Case | Exact reason | What happened |
|---|---|---|
| `f03-paraphrase` | `residue` | `counter` / `socket opens` are not licensed tokens |
| `f16-restatement` | `duplicate` | the same span was already admitted |
| `f09-semicolon` | none | the supported prefix was never its own clause; the enclosing sentence was `residue` and skipped |
| `f04-unicode-tail` | none | the English prefix has no trace; the enclosing sentence was `lexical_pass` and accepted |

## Development

No false claim was accepted. Eight supported claims were skipped: seven `residue` (rename, comments, whitespace, JavaScript, Go, Rust, and the renamed baseline), and `d03-paraphrase` as `order`. The order rule can reject a true paraphrase, so a shadow pass that ignores `order` skips will miss that case.

## Next experiment

Run a shadow classifier outside the writer lock over two sets only: the seven `lexical_pass` accepts above, and the `residue` / `order` skips (`f03` and the eight development misses). Require a cited substring of the tool output. Compare the labels to these gold atoms. Do not change `admit`, do not open the held-out file, and do not call the model from `run_handoff`. A label that accepts `The retry_count database pool is exhausted` or a denial the polarity rule already drops is a failed shadow run.
