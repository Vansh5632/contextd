# Eval episode catalog

Fill or thicken these on the same YAML schema. The harness already loads every file under `eval/episodes/`.

| Category | Episodes |
|---|---|
| Recent failure | `l1/live-error`, `l2/recent-failure-hook-faithful`, `l2/payment-webhook-restraint` |
| Intent continuity | `l1/intent-continuity`, `l2/intent-resume-feature` |
| Prior investigation | `l2/prior-investigation-timeout`, `l2/prior-investigation-pool` |
| Decision memory | `l2/decision-no-redis`, `l2/decision-no-public-api` |
| Negative knowledge | `l2/negative-knowledge-db`, `l2/negative-knowledge-config` |
| File continuity | `l2/file-continuity-rate-limit`, `l2/file-continuity-function`, `l2/file-continuity-restraint` |
| Branch switching | `l2/branch-switch-auth`, `l2/branch-switch-session-expiry` |
| Long-term recall | `l1/recall-similar`, `l2/long-term-recall-three-days`, `l2/long-term-recall-mutex` (embeddings), `l2/git-pinned-hello` |
| Agent handoff | `l2/agent-handoff`, `l2/agent-handoff-restraint` |
| Incident recovery | `l2/incident-recovery`, `l2/incident-recovery-page`, `l2/incident-recovery-restraint` |
| Deployment continuity | `l2/deployment-continuity`, `l2/deployment-continuity-restraint` |
| Review continuity | `l2/review-continuity`, `l2/review-continuity-nits`, `l2/review-continuity-restraint` |
| Multi-repo | `l2/multi-repo`, `l2/multi-repo-restraint` |
| Refactor continuity | `l2/refactor-continuity`, `l2/refactor-continuity-modules`, `l2/refactor-continuity-restraint` |
| Debugging frontier | `l2/auth-rate-limit-return`, `l2/debugging-frontier-mid-thought`, `l3/multi-day-supersession` |

Restraint (negative control) share is eight of ~37 files, about 22%. Thicken those before adding more look-alike recalls.

Optional later: `eval/harness/pack.py` turns a captured event JSONL plus a hand-written gold block into a new YAML. No second runner.
