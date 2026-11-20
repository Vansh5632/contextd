# Component 2.1 baselines

`results.json` is the 17 November 2026 capture under the scorer that preceded `integrity-2`. Its `git.status` is empty and its `git.revision` is `baa457146d5a6c9f592b6aea2fa5278b533e4b8d`. Its 23 passes mix two daemon contract passes with skipped sentences, incomplete observations, a grader self-test, and synthetic traces. Read it as the before snapshot. The corrected reading is [the integrity report](../docs/integrity-report.md).

`integrity-after.json` is the 20 November 2026 rescore with scorer `integrity-2`. Its `official` field is false because the tree was dirty. Same revision, same daemon, no model. Do not cite it as a clean-tree baseline.

`dirty-tree-2026-11-12.json` is the superseded 12 November 2026 capture from dirty `0714127`. Do not cite it as the current result.
