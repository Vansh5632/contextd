# Component 2 status

Updated 3 November 2026. The numbers and the rerun command are in `docs/component-2-validation.md` section 7. The mechanism is in `docs/component-2-design.md` under "Live handoff".

Live tool results enter through `observe`, not the shell hook. `run_handoff` quotes them at startup, after a note, and before `context_now`, and writes claim events. A restart folds those events. An unsupported conclusion is skipped. A retract supersedes only the claim it quotes.

`cargo test --workspace --all-features` exited 0. Component 0 pytest passed 256 tests, including `arms check` with the generated packet present. On `component2-2026-11-03`, Component 2 was mean 3.20 turns and 121,386 tokens against 5.80 turns and 172,197 for Component 1, with no constraint violation and no forbidden action.

Still true: the shell hook stores no stdout, unmarked prose is skipped, dedup is exact text, and the retry-budget episode was not given to Codex.
