# Component 0: How to evaluate context quality

Research note for contextd. This document answers one question:

> Before we build any context-management component, exactly how will we know whether it improved the context given to the agent, and whether that improvement produced meaningful engineering value?

It does not design the runtime. Findings from papers and vendor documentation are marked **Evidence**. Design choices for contextd are marked **Recommendation**. Where the evidence is weak or conflicting, it says so.

---

## 1. Executive findings

- **Task success is not a measure of context quality.** Several 2025–2026 studies hold completion fixed, or nearly fixed, while context treatment changes rediscovery cost, constraint retention, or retrieval noise. A patch that passes tests can hide repeated dead-end work and dropped constraints.
- **“Good context” is the smallest set of claims that would change the next correct action, and that excludes claims that would change it incorrectly.** Sufficiency, relevance, noise, staleness, and redundancy are different properties. A packet can be sufficient and still harmful if it also contains superseded decisions.
- **More context is not better.** Position bias, raw length, irrelevant text, unfiltered prior trajectories, and over-retrieval all reduce performance even when the needed facts are present. One 2025 study found 14–85% drops with perfect retrieval, including when distractors were whitespace or masked.
- **Summaries are not a safe default.** Oracle *summaries* of prior coding trajectories beat both no context and oracle *full traces* on SWE-ContextBench. Separately, production-style compactors retained about 17% of injected session constraints (COMPINT, 2026), and most were worse than not compacting. Repeated compaction is warned about by Codex’s own product copy, as reported in the 2026 compaction-theory case study.
- **An oracle packet is a valid upper bound only if it is load-bearing.** Give every arm the same model, tools, budget, and workspace. The oracle is a human minimal packet. If oracle does not beat workspace-only on the pre-registered behaviour, the gold is decorative or the fact is already in the repo. Do not treat oracle as “the right product.”
- **The informative comparisons are paired deltas, not a leaderboard score.** No established result supports one composite. RAG evaluation, embedding benchmarks, and agent-compression studies all warn that a single rank hides which stage failed.
- **Split retrieval failure from use failure.** AMemGym (write / read / utilization), RAGAS (context vs answer), and ContextBench (explored vs utilized) already do this. contextd needs two extra cuts: was the fact observed and stored, and was it actually placed in the prompt. An agent that never calls MCP is not evidence that the stored context was bad.
- **Handoff loss is mostly rediscovery cost, not pass rate.** Handoff Debt (2026) found context-bearing handoffs cut median agent events 20–59% and prompt tokens 42–63% versus repository-only takeover, while solved-rate effects were smaller and model-dependent. A separate switch-matrix study found that changing model mid-dialogue moves scores even when the evidence is still in context.
- **Non-reobservable state is the only state historical context can uniquely supply.** When dropped facts can be re-read from the environment, compression often does not change behaviour (shown on ALFWorld). Decisions, ruled-out hypotheses, and constraints that were never written into code, tests, filenames, or git messages are the facts worth evaluating.
- **Negative controls are mandatory.** LongMemEval includes abstention. MemoryAgentBench’s conflict tasks show multi-hop update accuracy at or below 7% for the methods tested. A benchmark that only rewards “remember more” will select systems that resurface stale work.
- **Gold strings are the wrong label.** Label atomic claims with a role (required, optional, stale, forbidden) and a behavioural consequence that can be checked in the trajectory or the workspace. Validate the labels with a counterfactual swap and an oracle-versus-workspace arm before trusting them.
- **A result is invalid** if arms differ in model, tool set, turn budget, or workspace; if the decisive fact is recoverable without the history; if success is a lexical match to a hidden string; if one stochastic run is treated as a difference; or if token savings are reported without constraint retention and repeated-work cost.

---

## 2. Recommended evaluation model

**Evidence.** Useful decompositions already exist, and they do not agree on the cut points:

| Source | Cuts |
|---|---|
| AMemGym (ICLR 2026) | write → read → utilization, then overall QA |
| RAGAS (2023) | context relevance / precision / recall, then faithfulness and answer relevance |
| ContextBench (2026) | gold code regions vs regions explored vs regions used in the patch |
| LongMemEval (ICLR 2025) | indexing, retrieval, reading; plus oracle-evidence vs full-history |
| Liu et al., compression cost (2026) | state retained vs reacquisition tool calls vs execution tool calls vs completion |
| Handoff Debt (2026) | frozen repo × handoff view, then events, tokens, and solved rate |

**Recommendation.** Use a six-stage ladder. A failure is attributed to the earliest stage that breaks. Later stages are reported anyway, because a packet can be retrieved correctly and still be unused or harmful.

```text
0  Observation     Did the sensor see the event?
1  Persistence     Is the event durably stored?
2  State           Is the working state correct (including supersession)?
3  Selection       Was the right subset chosen and the wrong subset excluded?
4  Placement       Is that subset in the tokens the model actually receives?
5  Use             Does the model notice it (probe or immediate behaviour)?
6  Behaviour       Does the action sequence change in the intended way?
7  Outcome         Does the engineering result improve, without a hidden regression?
```

Stages 0–4 are **context-system** stages. Stages 5–7 are **agent** stages. Do not average them.

Two protocols, never mixed in one number:

1. **Forced placement.** The arm’s packet is inserted into the prompt. This measures whether the packet is good. It is the primary protocol for Component 0, because current MCP results are confounded by agents that never call the tool.
2. **Voluntary access.** The agent may call contextd and may ignore it. This measures adoption. A miss here is an agent-use failure, not a retrieval failure.

Report both. A system can win protocol 1 and fail protocol 2.

**What “improved” means.** Pre-register both claims:

- **Context improved** if stages 2–4 move in the predicted direction on required, stale, and forbidden atoms, at a fixed token budget.
- **Engineering value improved** if stage 6 or 7 moves in the predicted direction (less repeated work, fewer constraint violations, fewer turns to the first useful action, or a higher solved rate) and no primary constraint metric regresses.

A solved-rate gain with a constraint regression is not a win. A token reduction with the same repeated-work rate is not a win. A recall gain with a precision collapse is not a win.

---

## 3. Recommended metrics

No composite score. Publish the primary set on every arm. Diagnostics explain a primary movement; they do not replace it.

### Primary

| Metric | Definition | Layer | How measured | Why it matters |
|---|---|---|---|---|
| Required-atom recall | Fraction of required claims present in the placed packet | Selection, Placement | Deterministic match against gold atoms (normalized claim, not raw string) | The packet omitted something the next decision needs |
| Context precision | Fraction of placed claims that are required or optional, not stale-as-current and not forbidden | Selection, Placement | Same atom matcher. Stale text is a hit only if marked superseded | Recall alone rewards dumping the log |
| Stale-as-current rate | Fraction of superseded claims that appear unmarked, or that the agent treats as still in force | State, Behaviour | Packet check, plus a probe question whose correct answer follows the latest decision | Old decisions overriding new ones is a known memory failure mode |
| Forbidden-action rate | Fraction of episodes where the agent takes a pre-registered forbidden action (repeat a ruled-out investigation, edit a frozen API) | Behaviour | Trajectory and workspace diff against an action list | This is the harm test. Remembering the wrong thing is worse than forgetting |
| Constraint-violation rate | Fraction of episodes that break a gold constraint in the final artifact or in an intermediate action | Behaviour, Outcome | Executable checks (signature diff, test, policy checker), not an LLM judge | COMPINT shows compactors drop session constraints while tasks still “continue” |
| Repeated-work rate | Share of tool calls or files that re-enact a ruled-out path | Behaviour | Classify calls against a gold dead-end manifest (paths, commands, query shapes) | Handoff Debt and the 2026 compression-cost study: completion misses rediscovery |
| Turns to first on-frontier action | Turns until the first action that matches the gold next step (right file or right probe), not any edit | Behaviour | Trajectory timestamp | Separates “eventually stumbled into the fix” from “resumed the frontier” |
| Continuation solved rate | Binary: the frozen task’s fail-to-pass checks pass under the constraints | Outcome | Test suite plus constraint checks | Still necessary. It is not sufficient |
| Handoff tax | Paired difference, workspace-only minus this arm, on repeated-work and turns-to-frontier. Report solved-rate delta separately | Behaviour, Outcome | Same successor model, paired episodes | Matches the only coding-handoff result we have: efficiency moves more reliably than pass rate |
| Active tokens | Tokens in the placed context packet | Efficiency | Tokenizer for the eval model | The product claim is a small sufficient packet. Budget is part of the claim |
| Total tokens | All prompt and completion tokens until stop | Efficiency | API or harness accounting | Catches systems that shrink the packet and then re-fetch everything |

### Diagnostic

| Metric | Definition | Layer | How measured | Why it matters |
|---|---|---|---|---|
| Observation coverage | Fraction of scripted events the sensor emitted | Observation | Compare scripted event log to daemon ingest | A miss here makes every later metric meaningless |
| Persistence coverage | Fraction of emitted events readable back from storage | Persistence | Store query | Distinguishes “never stored” from “stored but dropped” |
| Supersession accuracy | Gold retractions applied, gold current claims kept | State | Query the working set before selection | Flat logs fail this even with perfect retrieval |
| Retrieval recall@k / precision@k | If the system retrieves before packing: gold atoms in the candidate set | Selection | Logged retrieval, before the packer | Localizes packer bugs vs index bugs |
| Placement fidelity | Placed packet equals the selected packet (no silent truncation) | Placement | Byte or token compare | Budget code can drop the constraint it just selected |
| Use probe | Short question answerable only from a required atom, asked before the agent edits | Use | Exact or normalized answer | ContextBench’s explored-vs-utilized gap. A correct probe plus a wrong action is behaviour failure |
| Clarification count | Turns where the agent asks the user for a fact that is in the gold packet | Behaviour | Intent classifier on user-facing questions, audited on a sample | The cost contextd exists to remove |
| Context churn | Fraction of packet atoms that change between consecutive placements | Selection | Set diff of packets | Oscillating context is a product bug even if one snapshot scores well |
| Eviction/reload count | Times a required atom leaves and re-enters the packet | Selection | Packet timeline | Distinguishes stable working state from paging thrash |
| Compaction retention | Required and constraint atoms still present after each summary step | State | Atom diff across compaction boundaries | The direct COMPINT metric. Run it even if the final task passes |
| Drift under re-compaction | Atom flip rate after 2+ sequential compactions of the same history | State | Scripted replay | Codex warns that repeated compaction reduces accuracy; measure it |
| Abstention accuracy | On restraint episodes, rate of correctly ignoring history | Behaviour | Pre-registered “do not use history” label | Stops the benchmark from rewarding compulsive retrieval |
| Latency | Wall time to first packet and to task stop | Efficiency | Harness clock | Secondary. Do not optimize it against the primary set |
| Solved-rate vs history length | Primary behaviour and outcome metrics binned by distractor sessions | Outcome | Same task, more interleaved noise | The scaling claim. A flat solved rate with rising repeated-work is a failure |

**Evidence against a weighted score.** RAGAS keeps retrieval and generation metrics separate because they disagree. MTEB’s own result is that no embedding model wins across tasks. CompressAgent (2026) found compression reliability depends on the control document, so a universal ranking was the wrong summary. SWE-ContextBench reports accuracy, time, and cost separately and finds they pick different winners (oracle summaries win accuracy; oracle full traces win cost).

**Judges.** Prefer executable checks and atom matchers. LLM-as-judge is an acceptable *audit* of clarification labels and paraphrase, calibrated on a human slice. It is not the scoring authority. Position, verbosity, and self-preference bias in LLM judges are documented (Zheng et al., NeurIPS 2023). RAGAS itself was weakest on context relevance, which is the judgment we would be tempted to automate.

---

## 4. Recommended benchmark arms

Hold fixed: model, sampler settings, tool set, turn and token budget, container image, workspace snapshot, and the continuation prompt. The only difference is the context packet. Run every arm on the same episode seeds. Repeat each cell enough times to see variance (see open questions).

| Arm | Packet | What a win for contextd would mean | What it does **not** prove |
|---|---|---|---|
| `none` | Workspace only. No history, no summary | History contains decision-relevant state that cannot be re-read from the tree | That the system retrieved well. The agent may simply be a good coder |
| `raw` | Full prior transcript and observed events, truncated only by the model window, oldest dropped first | Selection beats an unfiltered dump on precision, stale-as-current, repeated work, or tokens, without losing required recall | That summaries are good. Raw can win recall and still lose behaviour |
| `summary` | One conventional compaction of `raw`, using a fixed public-style prompt (keep decisions, drop tool dumps). Not hand-edited | Structured state beats generic summarization on constraint retention and dead-end avoidance | That compaction is useless. SWE-ContextBench’s best accuracy arm was an oracle summary |
| `structured-notes` | Fixed schema filled by a prompted extractor: objective, decisions, constraints, ruled out, next step, stale. Same schema as gold, model-written | The automatic packer beats a one-shot structured summary, or ties it at lower tokens | A reason to skip `summary`. Handoff Debt treats summary notes and structured notes as different views |
| `oracle` | Human minimal packet: required atoms, constraints, ruled out, next step, and stale claims explicitly marked superseded. No extra narrative | The gap contextd→oracle is the remaining selection error. The gap oracle→perfect is model and task difficulty | That the human packet is what we should ship. It is an upper bound on *this label set* |
| `contextd` | Whatever the system under test places, under the same token budget as `oracle` if a budget is part of the claim | — | — |
| `stale-only` | Oracle packet with superseded claims left unmarked and current decisions removed | Not an arm to “win.” It is a negative control. Using it should *raise* forbidden-action and constraint-violation rates versus `none` | — |
| `distractor` | Oracle required atoms buried in unrelated sessions, or unrelated sessions only | Required recall holds and forbidden-action does not rise as noise grows | — |

`structured-notes`, `stale-only`, and `distractor` are included because the papers above treat them as different treatments, not as flavors of `summary`.

**What each comparison isolates**

- **contextd vs `none`.** Value of history beyond the workspace. If they tie on behaviour and outcome, either the history is irrelevant or the episode leaked (the fact is in code, tests, filenames, or the prompt).
- **contextd vs `raw`.** Value of selection and compression against noise and staleness. Expect this comparison to be mixed: raw should often win required recall and lose precision, repeated work, and tokens. A contextd win requires recall close to raw and better behaviour or cost.
- **contextd vs `summary`.** Value of explicit state (supersession, dead ends, constraints) over a prose recap. This is the comparison that justifies anything more structured than compaction.
- **contextd vs `structured-notes`.** Value of the system’s own construction over a single prompted extraction into the same schema. If they tie, the schema is doing the work and the runtime is not.
- **contextd vs `oracle`.** Headroom. A small gap means the automatic packet is near the label set. A large gap is a selection or construction defect, not a reason to change the task.
- **`oracle` vs `none`.** Validity check for the episode. Run this first. If oracle does not reduce repeated work or constraint violations, do not use the episode to rank systems.
- **`stale-only` vs `none`.** Validity check for the harm test. If unmarked stale context does not change behaviour, the episode cannot detect harmful memory.
- **`distractor` vs `oracle`.** Robustness. The drop is the noise penalty.

**Evidence for the oracle arm.** SWE-ContextBench uses no-context, oracle full trajectory, oracle summary (~217 tokens), and free retrieval. On their Lite related-task slice, oracle summaries moved resolution from 26.26% to 34.34%; oracle full trajectories only to 27.27%; free summary retrieval fell to 22.22%. LongMemEval reports a 30–60% drop from oracle evidence sessions to reading the full ~115k history. Acon treats “succeeds uncompressed, fails compressed” as evidence the compressor dropped a needed fact, and uses restoration as a check. Handoff Debt’s four views are the coding-agent version of `none` / `raw` / `summary` / `structured-notes`.

**Evidence the oracle can mislead.** If the oracle is written after seeing model failures, it overfits the label set. ContextBench’s gold contexts are repository regions experts marked necessary; that is the right *kind* of label for code navigation and the wrong kind for decision state. Do not copy their gold files as our gold decisions.

---

## 5. Episode design

**Evidence.** SWE-bench-style tasks score a hidden fail-to-pass test suite and therefore leak any fact that is written into tests, assertions, or gold patches. LongMemEval builds a length-configurable haystack around annotated evidence sessions and scores both retrieval and the answer, including knowledge updates and abstention. Handoff Debt interrupts at deterministic points, freezes the repo, and swaps only the handoff view. The 2026 compression-cost paper shows reacquisition appears when dropped state cannot be re-observed, and disappears in an environment where it can (ALFWorld). NoLiMa and later needle tests show lexical overlap makes “hidden string” retrieval look solved.

**Recommendation.** One episode is a frozen workspace plus a scripted history plus a label file the agent cannot see. The continuation prompt must not contain the gold.

### Anatomy

```text
episode/
  workspace/          # frozen tree the agent may edit
  history/            # scripted sessions (events, shell, notes). Not in the workspace
  prompt.txt          # underspecified continuation instruction
  gold/
    atoms.yaml        # required, optional, stale, forbidden
    actions.yaml      # on-frontier, forbidden, constraint checks
    tests/            # fail-to-pass, silent about the cause
  noise/
    other_sessions/   # unrelated work interleaved in the history only
```

### Session script

1. **Work session.** Produce a reproducible failure, at least one ruled-out hypothesis, one constraint that is never written down, one decision, and one next step. Stop before the fix.
2. **Distractor session.** An unrelated task that is completed and leaves ordinary files in the tree. Its events sit in the history between the decision and the handoff.
3. **Gap.** A session boundary. Do not depend on wall-clock delay; record a timestamp so temporal reasoning can be tested later.
4. **Takeover.** New process, empty conversation. Prompt is only “continue the interrupted work.” Arms differ solely by the packet.
5. **Optional restraint probe,** either as a second episode or a first question: a plausible action that the early history supports and the later decision forbids.

### Leak controls

The decisive facts must be **non-reobservable** from `workspace/` at handoff:

- Not in source, comments, docstrings, or commit messages.
- Not in test names, assertion messages, or fixture filenames.
- Not in the continuation prompt or tool descriptions.
- Git history is either squashed to content-only commits or replaced with a snapshot that has no rationale.
- Scratch experiments from ruled-out paths are deleted from the tree. Their only trace is `history/`.
- Benchmark metadata lives outside the container mount.
- Logs the agent can read are scrubbed of the hypothesis. The scripted shell transcript is part of `history/`, not part of the workspace.

Two validity tests, borrowed from causal checks in the compression literature:

- **Counterfactual swap.** Replace one required decision with a different legal decision. The on-frontier action must change. If it does not, that atom is not doing work.
- **Re-observation check.** Confirm a `none` agent cannot recover the constraint by reading the tree. If it can, the episode is testing coding, not context.

### What not to do

- Hidden passphrase or “needle” files. They measure string retrieval. NoLiMa exists because lexical needles are too easy, and Peng et al. (2025) showed perfect retrieval still does not preserve reasoning.
- Scoring only the final test. That is SWE-bench, which this component is not allowed to treat as sufficient.
- Letting the agent that *generated* the history also write the gold after the fact.
- Interactive LLM users as the source of truth for v0. AMemGym shows on-policy dialogue is more realistic and also moves the target. Script the history so the gold stays fixed. Add on-policy noise later.

### Reproducibility

Pin the container, the model id, and the seeds. Record the placed packet, the full trajectory, and the workspace diff. Prefer deterministic atom checks. Where the model is stochastic, report paired deltas across repetitions, not a single run. Handoff Debt’s protocol (deterministic interrupt, frozen tree, swapped view) is the template.

---

## 6. Gold-context design

**Evidence.** ContextBench gold is a set of file, AST, and line spans, scored with overlap precision and recall. That fits “which code did you need,” not “which decision is still in force.” COMPINT gold is a list of session constraints checked for retention after compaction. LongMemEval gold includes evidence-session ids, updated facts, and questions whose correct behaviour is abstention. RAG relevance labels are per-item judgments, and precision without recall is incomplete. handoff-style structured state (objective, decisions, dead ends, next step, explicit supersession) matches the claims we need, but public tools in that family mostly score the store deterministically, not the agent.

**Recommendation.** Gold is a set of typed atoms plus the actions those atoms require. It is not a paragraph and not a string to grep.

```yaml
required:
  - id: bug
    claim: "Undercount is reproduced by concurrent decrement, not by a single-threaded run."
  - id: api
    claim: "allow(key) signature and error type are frozen."
    check: signature_unchanged
ruled_out:
  - id: db
    claim: "Database contention was measured and excluded."
    forbidden_action: [touch_db_tests, add_db_index]
unresolved:
  - id: fence
    claim: "Whether the window should be per-tenant is undecided."
    forbidden_action: [implement_per_tenant]
next_step:
  - id: path
    claim: "Next unread site is the concurrent decrement path."
    on_frontier: [open_decrement_path]
stale:
  - id: redis
    claim: "An earlier note proposed moving the limiter to Redis."
    superseded_by: db
    forbidden_as_current: true
optional:
  - id: load
    claim: "The failing interleaving was 8 threads, 1000 ops."
forbidden_in_packet:
  - "Benchmark atom ids, this file, or the phrase gold context."
```

Roles:

| Role | In the placed packet | If the agent acts on it |
|---|---|---|
| required | Must appear | Missing it is a context miss. Ignoring it after placement is a use or behaviour miss |
| optional | May appear. Does not count against precision | Neutral |
| stale | May appear only marked superseded | Unmarked, or followed, is stale-as-current |
| forbidden | Must not appear as a current fact | Acting on it is a forbidden action |
| unresolved | Must not be stated as decided | Implementing it is a behaviour failure |

**Behavioural consequences are part of the gold**, because a packet can contain the sentence and not change the action. Each required constraint and each ruled-out item gets an executable check or a trajectory predicate.

**How to validate a label set before trusting scores**

1. `oracle` beats `none` on at least one primary behaviour metric.
2. `stale-only` loses to `none` on forbidden-action or constraint-violation.
3. Swapping one required atom changes the on-frontier action.
4. A second annotator, or the same annotator a week later, agrees on atom boundaries. Disagreements become `optional`, not `required`.

If (1) or (2) fails, the episode is rejected. That is the guard against gaming: we cannot mark an easy string “required” and declare recall solved.

**Paraphrase.** Match claims by a small entailment check with a frozen judge, audited by humans on every new episode family. Do not require verbatim wording. Do not let the system under test be the judge.

---

## 7. Failure taxonomy

Use the earliest stage. Names below follow the ladder in section 2. Literature equivalents are in parentheses.

| Failure | Definition | Typical evidence | Do not confuse with |
|---|---|---|---|
| Observation | The live event never entered the pipeline | Scripted shell or git event absent from ingest | Persistence |
| Persistence | Seen, then absent from storage | Ingest log hit, store miss | Selection |
| State construction | Stored facts exist, but the working set is wrong: missing supersession, merged contradictions, wrong objective | Supersession accuracy low while persistence coverage is high | Retrieval (MemoryAgentBench conflict resolution; LongMemEval knowledge update) |
| Retrieval | The right item is stored and not returned by search | Recall@k low; item present in store | Placement |
| Selection | Candidates are available; the packed subset drops a required atom or includes noise or unmarked stale claims | Precision or stale-as-current moves while recall@k is fine | Compaction |
| Compaction loss | A summary step deletes a constraint, decision, or dead end that was in the pre-summary text | Atom diff across the summary (COMPINT) | Selection on raw events |
| Placement | Selected packet is truncated, reordered into the middle, or never inserted | Packet log ≠ prompt slice (lost-in-the-middle; length penalty) | Use |
| Agent-use | Packet is in the prompt; a probe for that atom fails | Use-probe miss with placement fidelity pass (AMemGym utilization; ContextBench utilized < explored) | Retrieval. Also: voluntary MCP not called, under protocol 2 only |
| Behaviour | Probe passes, or the atom is quoted, but the action is wrong: dead end repeated, constraint broken, stale plan followed | Forbidden-action or repeated-work hit | Outcome. The tests may still pass later |
| Outcome | Behaviour was acceptable and the task still fails, or tests pass while a constraint check fails | fail-to-pass vs signature check disagree | A pretext to ignore behaviour metrics |
| Harmful context | An arm with history is worse than `none` on a primary behaviour or outcome metric | Negative paired delta, especially `raw` or `stale-only` | Low recall. This is the opposite defect |
| Handoff degradation | The same packet yields a worse behaviour delta for model B than for model A | Switch matrix on one frozen packet (switch-matrix paper, 2026) | A bad packet. Cross-check by running both models on `oracle` |
| Restraint failure | On a negative-control episode, the agent follows history it should ignore | Abstention miss | Low recall on a positive episode |

**Recommendation.** Every failed episode stores one primary tag plus the metrics that justified it. “Agent failed” is not a tag.

---

## 8. Existing work comparison

Only work that changes how we measure. Dialogue-memory suites (LoCoMo, PerLTQA, MemoryBank) are omitted: they score persona QA, not engineering continuation.

| Work | Main idea | Evaluation method | Useful to us | Limitation |
|---|---|---|---|---|
| Lost in the Middle, Liu et al., TACL 2024 | Models use the start and end of a long input and miss the middle | Multi-doc QA and key-value retrieval; move the relevant span | Position is a placement variable. Do not score a packet as “present” if it is buried | Retrieval-shaped tasks. Later work shows length hurts even with perfect retrieval |
| Context length alone hurts, Peng et al., arXiv 2510.05381, Oct 2025 | 14–85% drops on math, QA, and code despite perfect retrieval; whitespace and attention masks still hurt | Short task padded to long context; retrieval checked separately | Necessary vs sufficient: a found fact can still be unused. Motivates the use probe | Not an agent loop. Effect size will not transfer to our episodes |
| Shi et al., ICML 2023 | Irrelevant sentences distract arithmetic reasoning | GSM-IC, controlled distractors | Irrelevant context is a first-class negative condition | Small problems, not software work |
| SWE-bench, Jimenez et al., ICLR 2024 | Can the agent patch a real repo so tests pass | Fail-to-pass in a pinned container | Container isolation, hidden tests, paired setup | Pass rate hides path, constraints, and rediscovery. Gold often leaks via tests |
| ContextBench, Li et al., arXiv 2602.05892, Feb 2026 | Gold *code* context, not just the patch | File/block/line precision, recall, F1 along the trajectory; Pass@1 beside them | Explored ≠ utilized. Agents buy recall with noise. Scaffolding did not fix retrieval | Gold is source locations for issue solving, not decisions or stale beliefs |
| SWE-ContextBench, arXiv 2602.08316, Feb 2026 (rev. May 2026) | Prior related issues as reusable experience | No-context, oracle trace, oracle summary, free retrieval. Accuracy, time, tokens | Closest arm design. Unfiltered and mis-selected context is flat or harmful. Compact correct summaries beat raw traces on accuracy | Measures cross-issue transfer inside SWE-bench, not session handoff of uncommitted reasoning. Free-retrieval confound mixes agent policy with memory |
| Handoff Debt, KC & Budathoki, arXiv 2606.02875, Jun 2026 (rev. Aug 2026) | Cost for a new agent to resume an interrupted coding task | Freeze repo; views = repo only, raw trace, summary, structured notes. Events, tokens, solved rate | Directly supports our arms and the claim that solved rate understates handoff quality | Does not label stale vs forbidden content. Solved-rate effects are model-dependent; do not expect a large pass-rate delta |
| Khraishi et al., arXiv 2603.03111, Mar 2026 | Mid-dialogue model switch | Prefix model then suffix model vs no-switch; paired bootstrap | Same packet, different model, is its own factor. Drift of roughly −8 to +13 points on one instruction benchmark, and about ±4 F1 on CoQA | Not coding. One turn. Shows handoff loss is not only missing facts |
| COMPINT / Lost in Compaction, Wang et al., arXiv 2608.11242, Jul 2026 | Session constraints silently dropped by compactors | Inject constraints; measure retention. Average retention ~17%; most compactors worse than no compaction | Constraint-retention metric. Summaries are an unsafe baseline until measured | Injected constraints, not naturally arising engineering decisions |
| Context Compaction Theory, arXiv 2608.01326, 2026 | Compaction as a communication problem | Anthropic compaction endpoint vs a Bloom filter on set membership; endpoint near chance | Formal reason a fluent summary can drop membership facts. Repeated compaction is lossy | Toy query. Not a software-engineering result |
| Compression interaction cost, Liu et al., arXiv 2608.16370, Aug 2026 | Completion can stay flat while reacquisition calls triple | Fixed horizon; drop vs fact-preserving compression; oracle restoration; retrieval vs execution calls | The metric we were missing: repeated tool use. Restoration proves the dropped state caused it. Cost appears only when state is not re-observable | One planning environment plus a negative ALFWorld result. Not code |
| CompressAgent, arXiv 2608.01056, Aug 2026 | Compressing tool and policy instructions | Environment-verified success at shrinking budgets. ~94% at full context; sharp drop by 35% retained; failures become tool-execution errors | Token cut is not reliability. Rank methods per context, not globally | Static control text, not episodic work history |
| Acon, Kang et al., arXiv 2510.00615, Oct 2025 | Optimize compression guidelines against failures | AppWorld, OfficeBench, multi-objective QA. Peak tokens −26–54%, success up vs other compressors; sometimes above no-compression by cutting distraction | Compression can help. Compare against both `raw` and `none`, or you will misread a noise reduction as memory | Optimizes for task reward. Authors note reward-only tuning can drop facts that do not affect the sparse success bit |
| LongMemEval, Wu et al., ICLR 2025 | Five chat-memory abilities, including knowledge update and abstention | ~115k and ~1.5M histories; Recall@k and answer accuracy; oracle sessions vs full history | Update and abstention tasks. Oracle-vs-full gap of 30–60%. Reading stage separated from retrieval | Personal assistant QA. Easy to overfit with a paraphrase judge |
| MemoryAgentBench, Hu et al., arXiv 2507.05257, Jul 2025; ICLR 2026 | Retrieval, test-time learning, long-range understanding, conflict resolution | Incremental chunks; exact match. Conflict multi-hop ≤ ~7% | Stale-fact arm. RAG can win retrieval and lose updates and global state | Mostly synthetic QA. “Selective forgetting” is still scored as answer accuracy |
| AMemGym, Jiayang et al., ICLR 2026; arXiv 2603.01966 | On-policy memory eval | Accuracy plus write / read / utilization failure rates | The diagnosis pattern: if a probe shows the fact is known and the task still fails, tag utilization | Conversational state, simulated users. Off-policy scripted episodes remain the right v0 |
| MemBench, ACL Findings 2025 | Factual and reflective memory; participation vs observation | Accuracy, recall, capacity, time. Full-context degrades as the window fills | Observation scenario is closer to contextd than chat participation | Small model, multiple-choice. Not a coding handoff |
| RAGAS, Es et al., arXiv 2309.15217, 2023 | Reference-free RAG metrics | Faithfulness, answer relevance, context relevance kept separate | Do not collapse retrieval and generation. Context relevance was the hardest to judge | LLM judge. Built for QA passages |
| Anthropic, “Effective context engineering,” 29 Sep 2025; compaction docs and cookbook, 2026 | Smallest high-signal token set; compaction, tool-result clearing, and external notes are different tools | Product guidance, not a benchmark. They state compaction is lossy and that aggressive compaction drops context whose value appears later | Aligns the product claim with a measurement target. Clearing re-fetchable tool output is not the same problem as dropping a decision | No public constraint-retention number for Claude compaction. COMPINT is the empirical check |
| MemGPT / Letta, Packer et al., 2023, and later agent memory systems | The model pages memory in and out | Often scored on conversational QA or MemoryAgentBench-style tasks | A voluntary tool-call memory system has the same adoption confound we see with MCP | Their published scores do not isolate paging quality from the model’s choice to page |

---

## 9. Proposed first benchmark

**Name:** `limiter-handoff`.

One episode family, three scripted sessions, one frozen takeover. It is large enough to move repeated work, constraints, dead ends, and noise, and small enough to audit by hand.

### Workspace the successor sees

A small library, not a famous repository (contamination). Public function `allow(key) -> bool`. A counter map. A failing test: under concurrent calls, accepted count exceeds the limit. The assertion message says the counts diverged. It does not say why, and it does not mention a database or Redis.

The tree has no comments, commits, or docs that state the cause, the constraint, or the discarded ideas. Git history is one squashed import. An unrelated CSV export module exists, with its own passing tests, from the distractor session.

### History only the context arms can see

**Session A.** The worker reproduces the undercount, profiles the database, and records that query time is flat (ruled out: DB contention). They consider a Redis port, then reject it because the deployment is single-process (stale: Redis). They decide the public signature cannot change (constraint). They name the concurrent decrement path as the next unread site and stop mid-file. None of this is written into the repo.

**Session B.** A different task: a CSV export off-by-one. It is fixed and committed into the tree as ordinary code. Its trace is long and full of file dumps, so a raw arm is mostly this session.

**Session C.** Empty conversation. Prompt: “Continue the interrupted work in this repo.” No mention of the limiter, the API, or Redis.

### What each arm should do if the labels are real

- `none` can still fix the race by reading code. It should take longer, and it should sometimes reopen storage or propose a new dependency, because nothing tells it those are closed. If `none` never does that, tighten the dead end until the oracle-versus-none check passes.
- `raw` contains the ruled-out DB work and the early Redis enthusiasm before the rejection. Risk: the agent restarts one of those paths. This arm exists so a dump can lose.
- `summary` will often keep “we were fixing a rate limiter” and drop the signature freeze or the DB exclusion. That is the COMPINT failure mode we need to see at least once, or the summary prompt is accidentally an oracle.
- `oracle` states the bug, the frozen signature, the excluded DB path, the superseded Redis idea, and the decrement path. It should cut turns-to-frontier and forbidden actions versus `none`. If it does not, the episode is invalid.
- `contextd` is scored by the gap to `oracle` on atoms and the gap to `none` on behaviour, under the same token budget as `oracle`.

### Pre-registered success for this episode

Primary, all required:

- Required-atom recall and context precision of the placed packet.
- Forbidden-action rate (DB investigation, Redis port, signature change).
- Turns to first open of the decrement path.
- Constraint check on the final diff (signature and error type unchanged) and the existing fail-to-pass test.

Not sufficient on its own: the fail-to-pass test.

Run `oracle` vs `none` and `stale-only` vs `none` before any contextd build is allowed to claim a result on this episode.

### Restraint twin

Same workspace and prompt. History says the undercount was a test bug, already corrected, and the decrement path must not be “fixed.” Correct behaviour is to leave the limiter alone and report that. An arm that edits the limiter because of an earlier, superseded bug report fails restraint. This stops a system that always acts on the newest code-shaped memory.

---

## 10. Open questions

These block implementation of the harness. They do not block the metric definitions above.

1. **Repetition count.** How many paired runs per arm are required before a behaviour delta is reported? Handoff effects are model-dependent and solved-rate effects are small. Pick a stopping rule (for example paired bootstrap, as in the switch-matrix paper) before looking at contextd numbers.
2. **Atom matcher.** Entailment judge versus normalized predicates written by the episode author. The judge is more scalable and is the easier thing to game. v0 can use predicates only; we need an explicit decision before episode two.
3. **Token budget for `contextd`.** Equal to `oracle`, equal to `summary`, or uncapped? An uncapped arm confounds quality with length. The length-penalty results say the budget should be pre-registered and tight.
4. **Which model is canonical for v0.** One model for all arms, plus a second model only on the frozen `oracle` and `contextd` packets as a handoff-robustness spot check. The spot-check model has to be chosen up front so we do not fish for a pair that flatters the packet.
5. **How hostile session B is.** A short distractor may not move `raw`. A long one may blow the window and make `raw` an unfair truncation test. Set the distractor size relative to the eval model’s window (for example 30–50% of the window) and freeze it.
6. **Voluntary-access protocol.** Protocol 1 (forced placement) should ship first. Whether protocol 2 is in the same harness or a later study is a product decision, because it measures agent habits rather than packet quality.

---

## 11. Sources

Primary sources, most recent first within a topic. arXiv dates are submission or stated revision dates, not venue dates, unless a venue is named.

### Context harms, length, and position

- Liu, Nelson F., et al. “Lost in the Middle: How Language Models Use Long Contexts.” *TACL* 12 (2024). https://aclanthology.org/2024.tacl-1.9/
- Shi, Freda, et al. “Large Language Models Can Be Easily Distracted by Irrelevant Context.” *ICML* 2023. https://proceedings.mlr.press/v202/shi23a.html
- Peng, Hao, et al. “Context Length Alone Hurts LLM Performance Despite Perfect Retrieval.” arXiv:2510.05381, Oct 2025. https://arxiv.org/abs/2510.05381
- Hsieh, Cheng-Ping, et al. “RULER: What’s the Real Context Size of Your Long-Context Language Models?” arXiv:2404.06654, 2024. https://arxiv.org/abs/2404.06654
- Modarressi, Ali, et al. “NoLiMa: Long-Context Evaluation Beyond Literal Matching.” arXiv:2502.05167, 2025. https://arxiv.org/abs/2502.05167
- Kamradt, Gregory. “Needle in a Haystack.” 2023. https://github.com/gkamradt/LLMTest_NeedleInAHaystack

### Coding-agent context and handoff

- Li, Han, et al. “ContextBench: A Benchmark for Context Retrieval in Coding Agents.” arXiv:2602.05892, Feb 2026. https://arxiv.org/abs/2602.05892
- Zhu, Jiayuan, et al. “SWE Context Bench: A Benchmark for Context Learning in Coding.” arXiv:2602.08316, Feb 2026, revised May 2026. https://arxiv.org/abs/2602.08316 — dataset: https://huggingface.co/datasets/jiayuanz3/SWEContextBench
- KC, Dipesh, and Anjila Budathoki. “Handoff Debt: The Rediscovery Cost When Coding Agents Take Over Interrupted Tasks.” arXiv:2606.02875, Jun 2026, revised Aug 2026. https://arxiv.org/abs/2606.02875
- Jimenez, Carlos E., et al. “SWE-bench: Can Language Models Resolve Real-World GitHub Issues?” *ICLR* 2024. https://arxiv.org/abs/2310.06770
- “Context as a Tool: Context Management for Long-Horizon SWE-Agents” (Cat / SWE-Compressor). ACL Findings 2026. https://aclanthology.org/2026.findings-acl.1032/

### Compaction and compression cost

- Wang, Zhiqi, et al. “Lost in Compaction: Evaluating Side-Constraint Loss under Context Compaction.” arXiv:2608.11242, 31 Jul 2026. https://arxiv.org/abs/2608.11242
- “Context Compaction Theory.” arXiv:2608.01326, 2026. https://arxiv.org/abs/2608.01326
- Liu, Shuyu. “What Does Context Compression Cost an Agent? Interaction Costs Unrevealed by Task-Completion Metrics.” arXiv:2608.16370, 17 Aug 2026. https://arxiv.org/abs/2608.16370
- “Control Under Compression: Reliability Frontiers for Tool-Using Agents” (CompressAgent). arXiv:2608.01056, Aug 2026. https://arxiv.org/abs/2608.01056
- Kang, Minki, et al. “Acon: Optimizing Context Compression for Long-horizon LLM Agents.” arXiv:2510.00615, Oct 2025. https://arxiv.org/abs/2510.00615 — code: https://github.com/microsoft/acon

### Agent memory benchmarks

- Wu, Di, et al. “LongMemEval: Benchmarking Chat Assistants on Long-Term Interactive Memory.” *ICLR* 2025. https://arxiv.org/abs/2410.10813
- Hu, Yuanzhe, et al. “Evaluating Memory in LLM Agents via Incremental Multi-Turn Interactions” (MemoryAgentBench). arXiv:2507.05257, Jul 2025; *ICLR* 2026. https://arxiv.org/abs/2507.05257 — code: https://github.com/HUST-AI-HYZ/MemoryAgentBench
- Jiayang, Cheng, et al. “AMemGym: Interactive Memory Benchmarking for Assistants in Long-Horizon Conversations.” *ICLR* 2026. arXiv:2603.01966. https://arxiv.org/abs/2603.01966 — code: https://github.com/AGI-Eval-Official/amemgym
- Tan, Haoran, et al. “MemBench: Towards More Comprehensive Evaluation on the Memory of LLM-based Agents.” ACL Findings 2025. https://aclanthology.org/2025.findings-acl.989/
- Maharana, Adyasha, et al. “Evaluating Very Long-Term Conversational Memory of LLM Agents” (LoCoMo). ACL 2024. https://arxiv.org/abs/2402.17753
- Packer, Charles, et al. “MemGPT: Towards LLMs as Operating Systems.” arXiv:2310.08560, 2023. https://arxiv.org/abs/2310.08560

### Model-switch handoff

- Khraishi, Raad, et al. “Evaluating Performance Drift from Model Switching in Multi-Turn LLM Systems.” arXiv:2603.03111, 3 Mar 2026. https://arxiv.org/abs/2603.03111

### Retrieval evaluation methodology

- Es, Shahul, et al. “RAGAS: Automated Evaluation of Retrieval Augmented Generation.” arXiv:2309.15217, 2023. https://arxiv.org/abs/2309.15217
- Thakur, Nandan, et al. “BEIR: A Heterogeneous Benchmark for Zero-shot Evaluation of Information Retrieval.” arXiv:2104.08663, 2021. https://arxiv.org/abs/2104.08663
- Zheng, Lianmin, et al. “Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena.” *NeurIPS* 2023. https://arxiv.org/abs/2306.05685

### Vendor documentation

- Anthropic. “Effective context engineering for AI agents.” 29 Sep 2025. https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- Anthropic. “Compaction at a token threshold.” Claude platform docs, accessed 2026. https://platform.claude.com/docs/en/build-with-claude/compaction-threshold
- Anthropic. “Context engineering: memory, compaction, and tool clearing.” Cookbook, 20 Mar 2026. https://platform.claude.com/cookbook/tool-use-context-engineering-context-engineering-tools

The Codex warning that long threads and repeated compactions make the model less accurate is quoted and attributed to Codex’s public documentation in arXiv:2608.01326. It is used here as a vendor acknowledgement reported by that paper, not as a number we measured.
