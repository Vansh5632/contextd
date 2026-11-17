# Documentation

Current implementation guidance was checked against the working tree on **10 November 2026**, including the in-progress Component 2.1 extractor. Dated reports below preserve what was measured at the time; they are not fresh verification of today's tree.

## Read in this order

1. [Project overview](../Readme.md)
2. [Build and setup](build-walkthrough.md)
3. [Connect agents and record observations](connecting-agents.md)
4. [Current architecture](architecture.md)
5. [Current status, limits, and evidence](status.md)

[What we are building](what-we-are-building.md) explains the product in plain language. For changes to the repository, use [CONTRIBUTING.md](../CONTRIBUTING.md), [TESTING.md](../TESTING.md), [Agent.md](../Agent.md), and [claude.md](../claude.md).

## Current investigation: Component 2.1

| Document | Role |
|---|---|
| [Research](component-2.1-research.md) | Why identifier overlap was insufficient evidence |
| [Design](component-2.1-design.md) | Clause-level rule implemented in the working tree |
| [Validation, 10 November](component-2.1-validation.md) | Latest recorded results; **not frozen**, with a grounding/usefulness tradeoff |
| [Detailed failure analysis, 11 November](component-2.1-failure-analysis.md) | Reproduced failures, source-derived mechanisms, evidence/state limits, and evaluation gaps from the 40-assignment study |
| [Evaluation contract](../eval/component21/docs/architecture.md) | Executable Component 2.1 contract, baseline, and what the current daemon does under that contract |

The [current status](status.md) interprets these reports without treating admitted agent claims as verified facts.

## Historical research, designs, and reports

Paths remain stable for citations. Read these as the history of a particular component or run; use [architecture](architecture.md) for current runtime behavior.

| Area | Documents |
|---|---|
| Evaluation foundations | [Component 0 evaluation](component-0-evaluation.md) |
| Working-state design | [Working-state representation](working-state-representation.md), [handoff research](research.md) |
| Briefing-selection diagnosis | [Failure report](briefing-selection-failure.md) |
| Component 1 | [Validation, 30 October](component-1-validation.md), [remaining-work audit](component1-remaining-work.md) |
| Component 2 proposal | [Research](component-2-research.md), [design](component-2-design.md) |
| Component 2 early results | [Validation, 2–3 November](component-2-validation.md), [status audit, 3 November](component-2-status-audit.md) |
| Component 2 broader checks | [Generalization validation, updated 9 November](component-2-generalization-validation.md), [evidence validation, 9 November](component-2-evidence-validation.md) |

Some reports reference generated run artifacts under `eval/component0/runs/`. Those artifacts are intentionally ignored and may exist only on the machine that ran the experiment. A report's recorded result remains historical evidence; a missing local run directory is not a reproduced result.

## Source and evaluation entrypoints

- [Daemon and CLI](../bin/contextd/src/main.rs), [CLI syntax](../bin/contextd/src/cli.rs)
- [Request schema](../crates/core/src/protocol.rs), [claim validation](../crates/core/src/claim.rs)
- [Projection](../crates/memory/src/project.rs), [briefing renderer](../crates/broker/src/snapshot.rs), [extractor](../crates/broker/src/extract.rs)
- [Evaluation harness](../eval/component0/harness/), [Python project](../eval/component0/pyproject.toml), [test instructions](../TESTING.md#evaluation-harness)
