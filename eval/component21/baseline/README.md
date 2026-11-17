# Component 2.1 baselines

`results.json` in the measured commit is the 12 November 2026 capture. Its `git.revision` is `0714127c32aee3970d079f3a957e1f51ea151794` and its `git.status` is non-empty, including `crates/broker/src/extract.rs` and `eval/component21/`. That file is the superseded capture.

The clean-tree rerun replaces `results.json`. The superseded file is kept as `dirty-tree-2026-11-12.json`. Cite a file only when its `git.status` is empty.
