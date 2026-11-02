# What a handoff should keep

Research note for Problem B: the briefing keeps routine commands and drops the notes a successor cannot reconstruct. Written 1 November 2026 from `docs/working-state-representation.md` sections 3 and 11, the Component 1 measurements, and the papers below.

This note decides what the briefing is for. It does not change the projector. The implementation plan is `docs/superpowers/plans/2026-11-01-keep-load-bearing-handoff.md`.

## The question

A successor opens one page. The repository already contains the files, the diff, and any test the successor is willing to re-run. The page is the only place that can carry what the tree does not: a rule still in force, a choice already made, a path already ruled out, a check that failed, and the fact that the same check later passed.

The page is doing the opposite. Successful commands fill it. After the failing check passes, the page becomes a one-line trust notice, and a constraint that is still current disappears from it. The row is still in the claims table. The successor never sees it.

## The retention rule

Keep a fact on the page when the successor would otherwise have to rediscover it, and when dropping it can make the successor do the wrong thing. Drop a fact the successor can re-read or re-run, unless that fact is the outcome of a check that used to fail.

| On the page | Until | Evidence |
|---|---|---|
| Rules (constraints), in their own list | Someone lifts the rule. A passing check and marking the work done do not lift it. | COMPINT: summaries keep about 17% of session constraints; a side list keeps more than 90%. |
| A check that is still failing, and the next action to inspect it | The same command exits 0, which supersedes that failure. | Handoff Debt: notes help most after a validation failure. Component 1: this selection is already right. |
| A short line when a check that used to fail now passes | The work is still unfinished. | The successor otherwise hunts a failure that is gone. A command that never failed does not get this line. |
| Decisions, ruled-out approaches, unknowns, and a next step a person or agent wrote | Someone marks that work done. | These are the non-reconstructable facts in Handoff Debt’s structured note. The tree does not contain them. |
| One line for each other command that is still failing | That command passes, or the budget trims it after unknowns and extra decisions. | A second failure is evidence. Its task id must not decide whether it survives. |

| Off the page | Why |
|---|---|
| Commands that exited 0 and never failed (`ls`, `grep`, `git diff`, a passing probe) | The successor can re-run them. They dominated the 1,775-token packet and did not reduce total tokens versus no briefing. |
| File contents and diffs | They are in the repository. Store a path when a claim needs one. |
| The raw event history | Handoff Debt’s raw trace carries the most information and the largest first prompt. SWE-ContextBench’s short oracle summary beat the full trajectory. |
| Superseded claims, other than a ruled-out line that is itself still current | MemoryAgentBench: leaving both the old fact and the new fact looking current fails conflict questions. |

Rules are a different object from the task summary. They render in a standing list even when nothing is failing and even when the task they were written on is done. Decisions and findings stay with the unfinished work and leave when that work is marked done.

## What the studies measured

### Handoff Debt

Dipesh and Budathoki interrupt a coding agent, freeze the repository, and give the successor one of four views of the same checkpoint: the repository alone, the raw trace, summary notes, or structured notes. Across 75 source tasks this produces 181 handoff points and 724 takeover runs per successor model.

Context-bearing handoffs cut median agent events by 20–59% and cumulative prompt tokens by 42–63% versus the repository alone. Solved-rate gains are smaller and depend on the model. Raw trace is the strongest solved-rate arm and the largest first prompt, about 87,000 characters, against about 10,000 for structured notes and about 7,200 for the repository alone. The debt is paid during reconstruction, not in the first prompt: a repository-only successor starts small and then re-derives what was tried, what failed, and what was decided.

The gain is largest after a validation result. The structured note splits fields into ones a program can fill and ones a model writes:

- Program: files changed, validation outcome, latest validation command, latest validation evidence, and whether the work needs finishing or is already solved.
- Model: what was understood, what was completed, what was observed, what failed, what is still uncertain, what to roll back, and the recommended next action.

Notes are labelled as evidence to verify. Three failure modes show up: the note omits an exact command, the successor trusts the predecessor too much, or the raw trace is so noisy the successor re-separates dead ends. Taking over solved work and taking over unfinished work are different problems.

### Lost in Compaction (COMPINT)

Wang, Zhang, Lee, and Yang compact long sessions and measure session constraints: instructions such as “do not delete any emails until I confirm” that govern later behavior and are not the task itself. Removing the constraint leaves the goal intact and changes which actions are acceptable. A constraint stays in effect until it is revised, revoked, or its scope ends.

Compactors retain about 17% of injected constraints on average. Most do worse than not compacting. A prompt that says “remember constraints” stays under 40% on the longest chat setting. An extractor that keeps constraints in a side list and appends that list after compaction retains more than 90% across chat, tool use, and research, without changing the compactor. In an inbox trial the agent deleted mail without approval in 30 of 100 sessions after compaction, and in none before it.

Retention depends on the compactor, the length, where the constraint was said, and how it was phrased. Saying it later helps recency-biased compactors. Saying it strictly helps a little. No constraint type is kept reliably. Process constraints (“show me a draft before you send”) are kept least. The best single type in their average, a preference under one open model, is 36%. On their software-engineering trajectories no compactor keeps more than 28.6%.

The implication for this briefing: a rule written into the task paragraph will be dropped. A rule kept in its own list survives.

### SWE-ContextBench

This benchmark is cross-task experience reuse, not a handoff inside one task. On the related-task slice, oracle summaries of about 217 tokens move resolution from 26.26% to 34.34%. Oracle full trajectories of about 25,000 tokens reach 27.27%. Retrieved free-form summaries fall to about 22%. A short note helps when it is the right note. A pool of summaries plus open retrieval can hurt. This does not justify storing a prose summary and searching it.

### MemoryAgentBench and LongMemEval

MemoryAgentBench includes conflict resolution over contradictory facts. The paper reports multi-hop conflict accuracy of at most about 28% and says every method fails that setting. The dataset card’s highlighted methods are at most about 7%. Either figure supports the same design rule: appending facts and hoping retrieval picks the newest one does not maintain current belief. Do not treat either number as a target for contextd.

LongMemEval (ICLR 2025) tests extraction, multi-session reasoning, temporal reasoning, knowledge updates, and abstention. Long-context models drop 30–60% against shorter evidence. Knowledge-update items are the public test of “prefer the later fact.” The benchmark scores the answer. It does not score whether the old fact was removed from the store. Removal has to be explicit in our claims table, which exit 0 already does for a reality.

### Anthropic, context engineering

The stated goal is the smallest high-signal token set. For long tasks they name compaction, structured notes, and sub-agents. Claude Code’s compaction is described as keeping architectural decisions, unresolved bugs, and implementation details, dropping redundant tool output, and keeping the five most recently accessed files. Just-in-time context means storing a path or a query and loading the body later. COMPINT later reports that this compaction prompt still drops session constraints. “Keep decisions in the summary” is not a retention mechanism. The summary and the rule list are separate.

### Letta / MemGPT

A small set of labelled blocks is in every prompt. Archival memory stays outside and is retrieved by an explicit tool. Overflow does not fill the blocks. Supersession is overwrite. The useful idea is a small always-present working set with a label per block. The missing idea is status and evidence. We already have status on claims. This note does not add a new memory block system.

### Truth maintenance and the log

A justification-based truth maintenance system treats a belief as in force only while its support holds. Retract the support and the belief goes out. AGM belief revision is the corresponding rule of minimal change. A full TMS is the wrong v1. The transferable rules are the ones the claim view already has: current belief is separate from the history that produced it, a current claim points at its support, and retraction is explicit.

Event sourcing is the same split one level down. The log is the source of truth. The briefing is a fold of that log. ActiveGraph states that split for agents. The renderer may change what the fold shows. It must not invent a belief the fold does not contain.

## What this project already measured

Component 1, 30 October 2026. A fresh agent with the automatic claim briefing solved the pool-cap bug 5/5 and reached the frontier in a median of 5 turns. The old event feed solved 0/5. Total tokens stayed level with no briefing (195,493 versus 195,765). Required gold-atom recall was 0 because those notes were never events. The oracle packet that contains them is 381 tokens and the no-briefing arm uses fewer tokens after the frontier.

The empty page is reproduced. Pool pytest exits 1, a constraint is authored, the same pytest exits 0. The claims table is consistent: the failure is superseded, the constraint is current, the task stays active. `context_now` is the trust notice, 27 estimated tokens, no focal task. Restart rebuilds the same empty page. That is a render rule. It is not a lost write.

The placed packet is large because of the other rule. The 31-event replay produced 24 shell tasks, 23 of them exit 0, and the pretty packet kept 20 of them. About 1,443 of 1,775 tokens are the other-task array. The focal pytest is about 255. Trim drops other tasks from the lexicographic end of the task id, so a late second failure can leave while `sed` stays.

Gold atoms absent from the 31 shell events, and therefore from any renderer until something authors them:

- `finding_idle_not_counted`
- `decision_idle_counts`
- `constraint_signatures`
- `constraint_no_new_deps`
- `ruled_out_mock_backend`
- `ruled_out_lock`
- `next_step_create_branch`

## Defaults this fix uses

The papers do not say whether a decision should move onto the standing list after the work is marked done, or whether the “now passes” line should outlive the work. Those two stay with the unfinished work. A rule does not. A later ablation can move them. The ablation is not part of the renderer change, because one episode cannot tell those two apart from “the sentence was copied onto the page.”

The ablation, when it is run, uses the metrics already fixed in `docs/component-0-evaluation.md`. Same successor, same frozen workspace, only the packet changes. Arms are repository only, rules only, rules plus check outcomes, that plus decisions and ruled-out lines, that plus ordinary successful commands, and the human minimal packet. Score constraint violations, repeated work, turns to the first useful action, solved rate, and total tokens. A higher solved rate that also breaks a rule is not a win. Run it on more than the pool-cap episode.

## Adjacent work that does not set this rule

These were read while gathering the sources above. They do not change the retention table.

- **The Handoff Tax** studies model switches mid-trajectory. It argues the handoff interface is its own problem. It does not say which fields to keep.
- **Git Context Controller** versions agent context like a repository and reports a SWE-bench gain. It is a different store.
- **ESAA-Conversational** separates turns (evidence) from decisions (curated knowledge) and from a handoff contract. That split matches the claim log. It adds no retention number.

## Sources

| Source | What it contributes | Link |
|---|---|---|
| Dipesh and Budathoki, *Handoff Debt*, EMNLP 2026 | Takeover cost of four handoff views. Efficiency moves more than pass rate. Structured fields. Validation evidence matters most. | https://arxiv.org/abs/2606.02875 · https://aclanthology.org/2026.emnlp-main.738.pdf |
| Wang, Zhang, Lee, and Yang, *Lost in Compaction* (COMPINT), 2026 | Session constraints retained at about 17% inside summaries. A side list exceeds 90%. | https://arxiv.org/abs/2608.11242 · https://zhiqieliwang.github.io/compaction-integrity/ |
| Zhu et al., *SWE-ContextBench*, 2026 | A short oracle summary beats a full trajectory. Free summary retrieval can hurt. Cross-task, not a handoff. | https://arxiv.org/abs/2602.08316 |
| Wu et al., *LongMemEval*, ICLR 2025 | Knowledge update and abstention. Scores the answer, not retraction from the store. | https://arxiv.org/abs/2410.10813 |
| *MemoryAgentBench*, ICLR 2026 | Conflict resolution fails for current memory agents. Paper: multi-hop at most about 28%. Dataset card: at most about 7% for the methods it highlights. | https://proceedings.iclr.cc/paper_files/paper/2026/file/fd1eff9dd295df50a41f2521942fa31d-Paper-Conference.pdf |
| Anthropic, *Effective context engineering for AI agents* | Smallest high-signal set. Compaction target list. Just-in-time identifiers. COMPINT shows the compaction prompt still drops constraints. | https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents |
| Packer et al., *MemGPT*, 2023; Letta memory blocks | Always-present labelled blocks versus archival recall. | https://arxiv.org/abs/2310.08560 · https://www.letta.com/blog/memory-blocks/ |
| Nakajima, *The Log is the Agent*, 2026 | The log is the source of truth. The working view is a fold. | https://arxiv.org/abs/2605.21997 |
| Doyle, *A Truth Maintenance System*, 1979 | Belief separated from inference. Retraction. | https://doi.org/10.1016/0004-3702(79)90008-0 |
| de Kleer, *An Assumption-based TMS*, 1986 | Assumptions, contradictions, multiple contexts. Not built in v1. | https://doi.org/10.1016/0004-3702(86)90080-9 |
| Alchourrón, Gärdenfors, and Makinson, 1985 | Minimal change when a new fact contradicts the current set. | https://doi.org/10.2307/2274239 |
| Nii, *Blackboard Systems*, AI Magazine 1986 | One structured board rather than a message feed. | https://ojs.aaai.org/aimagazine/index.php/aimagazine/article/view/537 |
| Fowler, *Event Sourcing* | The log versus the materialized view. | https://martinfowler.com/eaaDev/EventSourcing.html |
| Ganz, Nacson, Kalyanpur, and Litman, *The Handoff Tax*, 2026 | Model handoff is its own interface. Not used for the retention table. | https://arxiv.org/abs/2608.24358 |
| Hu et al., *Git Context Controller*, 2025 | Versioned agent context. A different store. | https://arxiv.org/abs/2508.00031 |
| dos Santos Filho, *ESAA-Conversational*, 2026 | Evidence, curated decisions, and a handoff contract as separate objects. | https://arxiv.org/abs/2606.23752 |
| contextd, *Working-state representation* | The claim schema and the reading order this note specializes. | `docs/working-state-representation.md` |
| contextd, *Component 0 evaluation* | Metrics for a later ablation. Constraint-violation rate is the COMPINT cut. | `docs/component-0-evaluation.md` |
| contextd, *Component 1 remaining work* | The empty page (C1-02), exit-0 noise (C1-03, C1-07), and id-order trim (C1-06). | `docs/component1-remaining-work.md` |
| contextd, *Component 1 validation* | 5/5 selection, token parity with no briefing, gold-atom recall 0. | `docs/component-1-validation.md` |
