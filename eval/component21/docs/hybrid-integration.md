# Production hybrid verifier

The daemon now admits a reality only when the hybrid verifier supports it from tool output. A contradicted or uncertain claim stays in the log as a hypothesis. The model call finishes before the SQLite writer is taken.

This is not an end-to-end agent result. The numbers below are the Component 2.1 checker on scripted cases. Gold labels were not changed, and the held-out suite was not used to change the rules.

## What the daemon does

`broker::verify` is the deterministic router, the atom split, and the quote gate. It has no database handle. A straight-line proof can be `SUPPORTED` or `CONTRADICTED` with no model call. Empty evidence, `[output truncated]`, and conflicting writes of the same command abstain. Anything else is semantic.

`ai::openai` posts one chat completion with a strict JSON schema (`SUPPORTED`, `CONTRADICTED`, `INSUFFICIENT_EVIDENCE`), a 45 second timeout, and temperature 0. The default model is `gpt-4.1-mini` (`OPENAI_MODEL` overrides it). A missing key, a transport error, a bad response, or a quote that is not a substring of the evidence fails closed to `INSUFFICIENT_EVIDENCE`.

`verified_handoff` reads the log, classifies the semantic spans, and only then takes `store.writer()`. It reads the log again and drops a verdict whose tool output changed while the request was in flight. `run_handoff_with` admits from the verdicts it was given. It does not call the network.

A sentence is split on `;`, `。`, and the older clause markers (`so`, `and`, `but`, `because`, `means`). A supported span can become a reality while an unsupported tail stays a hypothesis. The whole compound is not stored as one reality. Speech acts, hedges (`might`, `maybe`, `probably`), retracts, and observed exit codes do not go to the model. `note_claim` without tool output is still a hypothesis.

## Commands

```bash
cargo fmt -p broker -p contextd
cargo test -p broker --lib
cargo test -p contextd-core --lib
cargo clippy -p broker -p contextd-core -p contextd -p ai --all-targets -- -D warnings
cd eval/component21
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q
cargo build -p contextd --bin contextd
# OPENAI_API_KEY is the CODEX_API_KEY value in the repo .env. Do not print it.
export OPENAI_MODEL=gpt-4.1-mini
python3 -m harness.run --suite regression --suite development --suite continuation \
  --allow-dirty --reuse-binary --output /tmp/c21-hybrid-integrated.json
python3 -m harness.run --suite heldout \
  --allow-dirty --reuse-binary --output /tmp/c21-hybrid-heldout.json
```

Both daemon runs are dirty, so `official` is false. They do not replace `baseline/integrity-after.json` or `baseline/results.json`. The clean safety baseline recorded before this integration is `/tmp/c21-safety-baseline.json` at `bb8b25b` (pass 7, fail 25, blocked 5, vacuous 11, incomplete 2).

Price used for cost: gpt-4.1-mini, $0.40 per 1M input tokens and $1.60 per 1M output tokens.

## Results

Broker library tests: 99 passed. Component 2.1 pytest: 91 passed. Clippy on `broker`, `contextd-core`, `contextd`, and `ai` is clean.

Regression, development, and continuation (50 cases, 47 executed):

| | Safety baseline | This run |
|---|---|---|
| pass | 7 | 17 |
| fail | 25 | 8 |
| vacuous | 11 | 18 |
| incomplete | 2 | 2 |
| blocked | 5 | 5 |

Cases that changed against that baseline: `f01`, `f02`, `f04`, `f06`, `f07`, `f08`, and `f09` went from fail to vacuous (the unsupported text is not a current reality). `f03`, `f18-hedge-probably`, and `d01` through `d05`, `d12`, `d13`, and `d14` went from fail to pass. No case that passed the safety baseline failed here.

Supported atoms that the checker can see are current, including the English prefix in `f04` and the increment prefix in `f09`, while the unsupported whole sentence is not current. The case stays vacuous because the harness treats "skipped, and not established" as vacuous rather than a semantic pass. That is the checker, not a second reality.

Two older failures still have an insufficient claim current: `f15-stale-after-change` and `f17-timestamp-order`. Both were already failures before this integration. They are supersession and replay, not a new verifier label. `f13`, `f14`, and `f16` still fail for task identity, authorship, and retract or restatement. `f10` and `f11` stay incomplete. `f19-note-claim` stays blocked.

Usage on this run: 36 API calls, 9,895 input tokens, 2,831 output tokens, 54,252 ms of verification time inside the handoff, about $0.0085.

Held-out, run once after the code was frozen:

| Case | Gold | Result | Established |
|---|---|---|---|
| h01 | SUPPORTED | pass | yes |
| h02 | CONTRADICTED | vacuous | no |
| h03 | SUPPORTED | pass | yes |
| h04 | CONTRADICTED | vacuous | no |
| h05 | INSUFFICIENT_EVIDENCE | vacuous | no |
| h06 | INSUFFICIENT_EVIDENCE | vacuous | no |

0 fail. The two supported claims are current. The four others are not. h01 and h03 needed no model call. h05 used 2 calls and was not admitted. h06 used 3. Held-out usage: 5 calls, 1,331 input tokens, 326 output tokens, 6,365 ms, about $0.0011. Nothing in the rules was changed after this run.

Shadow experiment 5 is a different measurement (a label beside the old admission decision, not this store). It reported 0 false admissions on its false claims and about $0.016 for 39 calls. The daemon now spends the calls only on spans the router cannot prove, and it writes the result into the log.

## Remaining limitations

- A missing `OPENAI_API_KEY`, a timeout, or a quote that is not in the tool output leaves a semantic claim unverified. Deterministic proofs still admit with no network.
- The handoff client waits for the model. The writer lock does not. Each socket query runs on its own task, so another query is not stuck behind that lock. This run did not add a separate concurrent-load benchmark.
- `f15` and `f17` can still show an insufficient claim as current. The verifier does not repair supersession or replay order.
- A restated claim after a retract can still be skipped as a duplicate (`f16`).
- The episode checker counts a stale sentence as current only when it is presented as a reality. A hypothesis of that sentence stays in the log.
- Vacuous case status hides a supported prefix when the same note also has an unsupported tail. Read the atoms.
- h05 was not admitted. The sealed label is insufficient. The run was not used to change the prompt or the router.
- Every later handoff classifies semantic spans again. Restart replay does not. An admitted reality is already in the log, so the second handoff does not need the original verdict.
- Same-command listings that disagree abstain. Two identical reads of one command do not.
- No claim is made about a fresh agent completing a task.
