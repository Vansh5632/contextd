# Component 2 generalization validation

> Historical research/report: statements and measurements below describe the dated snapshot, not necessarily the current implementation. See [current architecture](architecture.md), [status](status.md), and the [documentation index](README.md).

Updated 9 November 2026. The 5 November note on `0714127c32aee3970d079f3a957e1f51ea151794` stopped at the packet: required-atom recall 1.00, precision 0.875, and a false sentence admitted because it shared `retry_count` and `send` with a listing that increments `retry_count`. This pass is the evidence check and the missing agent runs. The mechanism and the measurements are in `docs/component-2-evidence-validation.md`.

**Not frozen.** The false sentence is no longer a current fact, with or without the retract line. A fresh agent benefits on turns on both episodes. A new cause written in the same clause as a real identifier can still be admitted.

---

## 1. What changed since 5 November

`episodes/retry-budget` is now a Component 0 episode. It has a local tree (`src/`), pins, a continuation prompt, an oracle, action labels, hidden tests, and a counterfactual swap. The workspace is built without cloning httpcore. `harness.run_matrix --episode` selects it. The default remains limiter-handoff, and that episode's frozen packet still matches a fresh extraction.

The overlap gate in `crates/broker/src/extract.rs` is no longer the support decision. Shared words anchor a sentence to the output. A denial of an increment the output shows, a reversed "before" / "after", or a clause with no shared word is not a current reality. Opposite claims of the same kind are demoted to hypotheses instead of both staying verified or one silently replacing the other. Component 1's fold and renderer were not edited.

The first retry-budget matrix was discarded. Pytest bytecode entered the diff and scoring raised `UnicodeDecodeError`. The tree now ignores `__pycache__` and `.pytest_cache`. The result is `component2-evidence-retry-2026-11-09`.

## 2. Packet, then agent

On retry-budget, after the evidence check, tiktoken `o200k_base`:

| Arm | Tokens | Required recall | Unit precision | Stale as current | Fresh-agent mean turns | Mean tokens | Constraint | Forbidden |
|---|---|---|---|---|---|---|---|---|
| none | 0 | 0 | — | 0 | 3.80 | 42,434 | 0 | 0 |
| summary | 59 | 0.17 | 0.50 | 0 | 2.60 | 36,869 | 0 | 0 |
| contextd-new | 246 | 0 | 0 | 0 | 5.00 | 42,072 | 0 | 0 |
| contextd-component2 | 627 | 1.00 | 0.875 | 0 | 2.80 | 41,484 | 0 | 0 |
| oracle | 117 | 1.00 | 1.00 | 0 | 2.40 | 42,101 | 0 | 0 |

All five reps of every arm solved the hidden tests. Component 2 versus no page: turns −1.00 [−1.80, −0.20], tokens −951 [−4,176, +1,003]. Versus Component 1: turns −2.2 [−2.8, −1.6], tokens −588 [−3,683, +1,282]. The false sentence is not in the page. Dropping the retract line does not put it back.

The Component 2 reps are tagged `behaviour` because each one read `timeouts.py` once, which is the repeated-work path. The empty page and Component 1 did the same. The oracle, which says not to edit that file, did not.

Limiter-handoff, same model and seed, run `component2-evidence-limiter-2026-11-09`, 25/25, 3,619,497 tokens:

| Arm | Solved | Mean turns | Repeated | Constraint | Forbidden | Mean tokens |
|---|---|---|---|---|---|---|
| none | 1.00 | 11.00 | 0 | 0 | 0 | 195,647 |
| summary | 1.00 | 3.20 | 0.20 | 0 | 0 | 208,521 |
| oracle | 1.00 | 2.80 | 0 | 0 | 0 | 60,932 |
| contextd-new | 1.00 | 5.20 | 0 | 0 | 0 | 160,843 |
| contextd-component2 | 1.00 | 2.80 | 0 | 0 | 0 | 97,957 |

Component 2 versus Component 1: turns −2.4 [−2.8, −2.0], tokens −62,886 [−94,697, −31,074]. No constraint violation and no forbidden action. The idle finding is still on the page.

## 3. Freeze

The 5 November gaps that are now closed: the episode can be run, a fresh agent was given the page, and the overlap rule no longer admits the contradicted sentence.

What is still open is in the evidence note. A same-clause invention is not rejected. On the small episode the automatic page saves a turn and does not save a reliable number of tokens, and it does not keep the successor out of `timeouts.py`. That is enough to keep the component unfrozen, and not enough to put the model verifier back on the table.
