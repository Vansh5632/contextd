# Initial audit

Component 0 and Component 1 already measure something real. They do not measure the contract this package adds. Their scoring was left unchanged.

## What the existing harness measures

`eval/component0/harness/packet_score.py` scores a handoff packet against regex atoms. A unit matches or it does not. `stale_as_current` is a stale atom that matched while a superseded marker is absent. Trial records also store whether the task was solved and how many turns and tokens it used.

That is a useful historical comparison for the limiter and retry-budget packets. It is not a judgment of whether a sentence is supported by the observation that was supplied.

## What it misses

- Semantic truth. A regex can match `retry_count before open_socket` inside a sentence that says the opposite.
- Evidence identity. The packet score does not check that a claim cites the observation that contains the supporting text.
- Provenance. User and agent origins are not scored.
- Task identity. Two sessions can share a command string and still be different tasks. The packet score does not check that.
- The difference between a checker skip and a claim that was accepted but omitted from the rendered brief.

## Leakage into the old tools

`eval/component0/harness/extract_claims.py` and `evidence_probe.py` copy production token lists, including interpretive words, stop words, and denial patterns. Some Component 0 gates also name specific atom ids. Episode `atoms.yaml` files use the vocabulary of those episodes. A future verifier that is tuned against those files can look good on the old packets while remaining a lexical overlap checker.

This harness does not import those modules as the factuality judge. The development and held-out cases use different identifiers from `retry_count` and from each other.

## Research artifacts

Local research notes under `docs/superpowers/` describe the 10 November public-protocol reproductions. Those paths are gitignored. The committed cases copy the six public inputs into `datasets/define_regression.py`. The runner does not read the research directory.

## What this audit does not change

`packet_score.py`, the Rust extractor, and the Component 0 and Component 1 validation reports are unchanged. Existing Component 0 pytest modules remain the CI check for that harness. The new grader tests are an additional CI step. They do not assert that the daemon satisfies the contract.
