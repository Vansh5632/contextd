# Working-state representation for contextd

Research note. No code changes.

Question this note answers:

> What should become contextd’s canonical representation of ongoing agent work, and how can the existing event-based system evolve into it without prematurely building retrieval, paging, or automatic context management?

**Decision.** Keep the append-only event log as evidence. Add a materialized **claim state**: typed, task-scoped claims with status, confidence, and pointers back to events. Render that state as the briefing. Do not treat a prose summary, a recency window, or a similarity search as the representation.

The proposed chain `Reality → Intent → Evidence → Decisions → Unknowns → Next action` is a good **reading order for one active task**. It is not a sufficient schema. It is missing task identity, completion, constraints, ruled-out approaches, and an explicit current-vs-superseded status. Evidence should be provenance on claims, not its own narrative bucket.

---

## 1. Current architecture

contextd is an observation log with four projections of the same events. Nothing in the running system is a task, a decision, or a belief.

### 1.1 One event, end to end

The Component 0 regression is a shell pytest that exits 1, then later a commit from a different piece of work. The failure is stored. The briefing shows the commit.

```text
shell hook                         command, exit_code, cwd only
        │                          crates/sources/src/install.rs, emit.rs
        ▼
bus → process_event                score 0.6 (pytest is "any other command")
        │                          crates/pipeline/src/heuristics.rs:4-16
        ▼
should_drop                        false; row is stored
        │                          bin/contextd/src/main.rs:265-298
        ▼
insert_event + WorkingSet::record  SQLite events + RAM deque
+ KnowledgeGraph::observe          co-occurrence edges, not outcome
        │
        ▼
enrich_one → decide()              Promote, because exit ≠ 0
        │                          crates/pipeline/src/decision.rs:53-57
        │                          summary becomes "exited 1"
        │                          score stays 0.6; Promote is not stored
        ▼
context_now → snapshot::build      working.recent(10) BEFORE rank
        │                          crates/broker/src/snapshot.rs:28, 149-159
        ▼
rank()                             reorders only those 10
        │                          similarity and proximity are 0
        │                          crates/broker/src/snapshot.rs:216-261
        ▼
query_text                         newest survivor (the commit), not intent
        │                          snapshot.rs:161-175
        ▼
ContextSnapshot JSON               commit reads as the current milestone
                                   pytest is absent
                                   mcp context_now, crates/mcp/src/server.rs:79-84
```

That packet is what the successor agent receives. There is no later step that turns events into “this command is still failing.”

### 1.2 Observation

| Source | What is sent | What is not sent | Where |
|---|---|---|---|
| Shell hook | `command`, `exit_code`, `cwd` | stdout, stderr, duration | `crates/sources/src/install.rs`, `emit.rs` |
| Git hook | commit subject (`head -n 1`), checkout branch, push remote | body, files, diff, repo path | `crates/sources/src/git.rs` |
| Filesystem | kernel event kind + path | contents, debounce, merge of same-timestamp writes | `crates/sources/src/filesystem.rs`, `noise.rs` |
| Proc poller | start/stop, command, runtime | exit code, cwd, streams | `crates/sources/src/proc_poller.rs` |
| Manifest | “dependencies_updated” + filename | parsed dependency diff | `crates/sources/src/manifest.rs` |
| Editor | enum value only | no producer | `crates/core/src/event.rs` |

`RawEvent` is `{ id, timestamp_ms, source, payload }` with an untyped JSON payload (`crates/core/src/event.rs`). The installed shell snippet is deliberately lossy: it must not hang the prompt or clobber `$?`. Non-interactive agent shells never source the hook, so a live agent session records no shell events unless something else injects them. The Component 0 replay injected them.

### 1.3 Scoring

`process_event` (`crates/pipeline/src/heuristics.rs:4-69`) assigns one float from the command string or path. It does not read `exit_code`.

| Condition | Score |
|---|---|
| Shell contains `cargo build`, `npm run`, or `error` | 0.9 |
| Shell is `cd` / `ls` | 0.1 |
| Any other shell command, including a failing pytest | 0.6 |
| Ordinary file save | 0.3 |
| `Cargo.toml` / `package.json` save | 0.8 |
| Git commit or checkout | 0.8 |
| Manifest | 0.95 |
| `process_end` | 0.2 |

### 1.4 Persistence and enrichment

Hot path (`bin/contextd/src/main.rs:265-298`): score, drop only if `should_drop`, then `insert_event`, `WorkingSet::record`, `graph.observe`, `enrichment.offer`.

`decide` (`crates/pipeline/src/decision.rs:41-74`) returns `Drop | Keep | Summarize | Promote`. The only runtime branch is `Drop` (skip insert, or later delete). `Promote` on failure does not change `score`. `Summarize` does not replace the payload. `Decision::is_durable` is not called outside tests.

Enrichment writes `use_case`, `memory_type`, and a ≤160-character `summary` (`crates/pipeline/src/content.rs`). For the shell hook, the summary can say “exited 1” and cannot quote the assertion, because stderr was never captured.

The pruner (`crates/background/src/pruner.rs`) archives rows older than 7 days when `score < 0.5` and memory type is null or `episodic`. It does not read `Promote`. A quiet failure scored 0.2 can be archived even though `decide` promoted it.

Embeddings (768-d, optional Ollama) are computed from the summary and stored in `vec_events`. They are a search index over events, not a state.

### 1.5 Working memory

`WorkingSet` (`crates/memory/src/working.rs:23-44`) is a deque of `ProcessedEvent` plus one `Option<Intent>`.

- Capacity 256, horizon 30 minutes.
- `recent(limit)` returns newest-first inside the horizon.
- `session_id` is a ULID minted at daemon boot (`bin/contextd/src/main.rs`), stamped on every event. It is not a task id and not an agent id.

### 1.6 Ranking and `context_now`

`ContextSnapshot` (`crates/broker/src/snapshot.rs:39-70`) is:

- one intent
- daemon `session_id`
- `recent_activity` (at most 10, then ranked, then fitted to a shared 1,500-token budget)
- `relevant_history` (at most the semantic neighbours of the query)
- `related` entities
- an archive hint (count and digest, not the archived events)

Rank weights (`crates/broker/src/relevance.rs:23-35`): recency 0.35 (20-minute half-life), importance 0.20, similarity 0.35, proximity 0.10.

On the recent list, similarity is always zero (no per-item embedding is passed) and proximity is always zero (`snapshot.rs:250` passes `edge_weight: None`). With events a few seconds apart, recency is flat, so order collapses to importance, then newer timestamp. A commit at 0.8 beats a pytest at 0.6 even inside a window wide enough to hold both.

Empty `context_now` sets the search string to the newest event’s summary (`snapshot.rs:161-175`). Declared intent is copied into the packet and is not the query (`snapshot.rs:198-208`).

MCP (`crates/mcp/src/server.rs`) is a stateless forwarder. Tools: `context_now`, `search_context`, `recall_similar`, `set_intent`. The only write is intent text.

### 1.7 Graph, archive, intent

The graph (`crates/memory/src/graph.rs`) links File, Command, Repo, and Error entities that co-occur within 5 minutes, edge weight = count. `EntityKind::Intent` exists and is never emitted. Edges are not “supports”, “contradicts”, or “supersedes”.

Archive is a zstd batch of old low-score episodic events. The briefing only says that a batch exists.

Intent (`crates/core/src/event.rs`, `intents` table) is one string plus a timestamp. Newest row wins. It does not group events and does not expire.

### 1.8 Where this is event-centric

| Work-state question | What the code answers instead |
|---|---|
| What task is unfinished? | Newest events in a deque |
| What is true now? | A score frozen at ingest |
| What did we decide? | Nothing. `Decision` is a retention enum |
| What did we rule out? | Nothing |
| Which observation supports that? | The row itself, if it survived the window |
| What replaced an earlier conclusion? | Nothing. Events are insert-only |
| Which session is this? | The daemon process |
| What should the next agent do? | Whatever sorts first after recency × importance |

Component 0 already recorded the consequence (`docs/briefing-selection-failure.md`): 31 events stored, failing pytest outside the newest 10, related history anchored on the Session B commit, observation recall 0, contextd solved 0/5, workspace-only solved 5/5. Widening `RECENT_LIMIT` would have put the pytest in the packet for this burst and would still not say that it is the unfinished task, that the commit is a different task, or that a finding exists. That change is not part of the redesign.

---

## 2. Current architectural limitations

These are representation limits. They are not tuning bugs.

1. **The canonical object is the observation.** A successor has to infer the task from a ranked feed. The feed’s top line is whichever event was loud and recent. In the regression, that line is a finished commit.

2. **Outcome is computed and then discarded.** `decide → Promote` and the summary “exited 1” never become a current claim. Ranking keeps using 0.6.

3. **Selection happens before interpretation.** `recent(10)` runs first. Importance cannot retrieve event 11. Semantic search then asks the newest survivor, so history follows the distractor.

4. **One global intent, no task, no completion bit.** Two pieces of work in one daemon run are one timeline. Nothing can be “done” versus “still failing.”

5. **No authored state except intent.** The conclusion “the cap check is in `_assign_requests_to_connections`” has nowhere to go. That conclusion is the kind of fact the workspace does not contain.

6. **No supersession.** A later passing run, a reversed decision, or a distractor milestone cannot retract an earlier claim. The pruner deletes old low-score rows; it does not mark them stale.

7. **Sensors drop the expensive-to-recompute details and also the impossible-to-recompute ones.** Exit code is kept. Assertion text is not. Rationale, ruled-out paths, and constraints are not observed at all.

8. **Graph and embeddings index events.** They do not project current belief. Proximity is not even an input to item rank.

9. **The briefing is the product.** There is no internal state that could be rendered differently, audited, or checked for contradictions before token packing.

---

## 3. Research findings

Citations are in section 11. Numbers below are from those sources. Where a secondary writeup disagrees with the paper, the paper wins.

### What the studies actually measured

**Handoff Debt** (Dipesh & Budathoki, 2026, [arXiv:2606.02875](https://arxiv.org/abs/2606.02875)) interrupts a coding agent, freezes the repo, and swaps only the handoff view: repository only, raw trace, summary notes, structured notes. Across three successors, context-bearing handoffs cut median agent events 20–59% and prompt tokens 42–63% versus repository-only. Solved-rate gains are smaller and model-dependent (+6.1 to +14.9 points for raw trace; note-based gains significant for one of three successors). Raw trace is the highest-information view and the strongest solved-rate arm, with a median first prompt of ~87k characters versus ~10k for structured notes and ~7.2k for repository only. Structured notes are a bounded contract, not the accuracy winner. Some secondary summaries claim raw traces are “nearly as bad as no context.” That claim is false on this paper.

Structured notes split fields into deterministic and model-written:

- Deterministic: changed source files, non-source artifacts, validation outcome, latest validation command, latest validation evidence, continuation-state label (needs completion vs already solved).
- Model-written: problem understanding, work completed, evidence observed, failures, remaining uncertainty, rollback notes, recommended next action.

The original task prompt is kept outside the note. Notes are labelled as evidence to verify, not as ground truth. Three failure modes: the note omits an exact command, the successor over-trusts the predecessor, or the raw trace is so noisy the successor re-separates dead ends. Context helps most after a validation failure, which is the contextd regression.

**COMPINT / Lost in Compaction** (Wang et al., 2026, [arXiv:2608.11242](https://arxiv.org/abs/2608.11242)) injects session constraints (“do not delete any emails until I confirm”) into long contexts and compacts. Average retention is **17%**. Most compactors do worse than not compacting. An extractor that keeps constraints in a side list, then appends that list to the summary, retains **>90%** without changing the compactor. A prompt that merely says “remember constraints” stays under 40% on one setting. Constraints are a different object from the task summary. Recency-biased compactors keep constraints near the end and drop the ones stated earlier.

**SWE-ContextBench** ([arXiv:2602.08316](https://arxiv.org/abs/2602.08316)) is cross-task experience reuse, not an intra-task handoff. On the related-task slice, oracle summaries (~217 tokens) moved resolution from 26.26% to 34.34%. Oracle full trajectories (~25k tokens) reached only 27.27%. Free summary retrieval fell to about 22%. Compact derived state helps when it is the right state. A pool of summaries plus open retrieval can hurt. This does not license “store a prose summary and retrieve it.”

**LongMemEval** (ICLR 2025, [arXiv:2410.10813](https://arxiv.org/abs/2410.10813)) tests extraction, multi-session reasoning, temporal reasoning, knowledge updates, and abstention. Long-context models drop 30–60% versus shorter evidence. Knowledge-update items are the public test of “prefer the later fact.” Abstention is the negative control. The benchmark scores the answer, not whether the old fact was removed from the store.

**MemoryAgentBench** (ICLR 2026) includes conflict resolution over contradictory facts. The paper reports multi-hop conflict accuracy of at most about **28%**, and says every method fails that setting. The dataset card summarizes multi-hop results as single-digit, at most **7%**, for the methods it highlights. Either figure supports the same design constraint: appending facts and hoping retrieval picks the newest one does not maintain current belief. `docs/component-0-evaluation.md` cites the 7% card figure. Do not treat either number as a precise target for contextd; treat conflict maintenance as unsolved by RAG-style memory.

**Anthropic, context engineering** ([engineering post](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)) defines the goal as the smallest high-signal token set. For long tasks they name compaction, structured notes, and sub-agents. Claude Code’s compaction is described as keeping architectural decisions, unresolved bugs, and implementation details, dropping redundant tool output, and keeping the five most recently accessed files. Just-in-time context means storing identifiers (paths, queries) and loading bodies later. COMPINT later reports that the Anthropic-style compaction prompt still drops session constraints. “Keep decisions in the summary” is not a retention mechanism.

**Letta / MemGPT** ([memory blocks](https://www.letta.com/blog/memory-blocks/)) puts a small set of labelled blocks in every prompt (persona, human, and custom blocks such as `current_task`) and leaves archival memory outside, retrieved only by an explicit tool. Archival is not filled by overflow. Blocks are edited by append, exact-string replace, or full rewrite. There is no justification graph: supersession is overwrite. The useful idea is a small always-present working set with a label per block, separate from searchable history. The missing idea is status and evidence.

**Event sourcing.** The current state is a fold of an append-only log; the log is not queried as the state. [ActiveGraph](https://arxiv.org/abs/2605.21997) (Nakajima, 2026) applies that split to agents: the log is the source of truth, the working graph is a deterministic projection, and behaviors emit new events instead of mutating the graph. contextd already has the log. It does not have the fold.

**Truth maintenance.** A JTMS (Doyle 1979; overview in [LTMS ch. 6](https://pisanuw.github.io/ltms/companion/ch06.html)) separates inference from belief. A claim is IN only while a well-founded justification holds. Retract an assumption and dependents go OUT, then anything with another justification comes back. An ATMS (de Kleer 1986) also stores the contradictions (nogoods). AGM belief revision (Alchourrón, Gärdenfors, Makinson 1985) is the corresponding theory of minimal change. Full TMS is the wrong v1. The transferable rules are: current belief is separate from the history that produced it; every current claim points at its support; retraction is explicit; contradictions are recorded rather than averaged.

**Blackboard.** Specialists write structured hypotheses onto a shared board; a controller decides what is visible. contextd’s product goal — one picture every assistant reads — is a blackboard. The current board is a feed.

### What a successor actually needs

Non-reobservable state is the only state history can uniquely supply. File contents, git diffs, and a test the agent is willing to rerun are in the environment. Handoff Debt’s repository-only arm is that condition, and it is the expensive one. The facts that move behaviour are the ones the tree does not contain: what failed, what was decided, what was ruled out, what constraint is in force, and what the unfinished task is.

A prose summary can carry those facts on a short SWE-bench task (Handoff Debt: summaries are competitive; SWE-ContextBench: the right summary wins). It does not keep them under compaction (COMPINT: 17%), does not mark what they replaced (MemoryAgentBench conflict failure), and cannot be checked by Component 0’s atom matcher without an extra parse.

---

## 4. Validate or change the hypothesis

`Reality → Intent → Evidence → Decisions → Unknowns → Next action` is the right order to **show** one active task. It is the wrong list of **stored kinds**, and two of its names are doing too much work.

| Proposed piece | Verdict | Change |
|---|---|---|
| Reality | Keep, narrow it | Verified claims about the task that the tree does not already state. Not “everything true,” and not the newest event. |
| Intent | Keep, scope it | Per task: objective plus the active subgoal. Not one daemon-global string. |
| Evidence | Demote from a section to a link | Every claim points at event ids and/or artifact refs. A section called Evidence becomes a second log. |
| Decisions | Keep | Choices in force, with rationale, status `current` or `superseded`. |
| Unknowns | Keep | Open questions and live hypotheses. A hypothesis is not a decision. |
| Next action | Keep | One frontier step for the active task. |
| Task + completion | **Add** | Without a task id and a done/active bit, a finished commit is indistinguishable from the frontier. This is the regression. |
| Constraints | **Add** | Standing rules for the rest of the task. COMPINT: they die inside summaries. |
| Ruled out | **Add** | Approaches that must not be repeated. Behaviourally the opposite of a decision in force. Handoff Debt’s rollback notes; Component 0’s forbidden actions. |
| Confidence | **Add as a field** | On the claim, not as a section. Matters for unknowns and for model-written notes, which successors over-trust. |
| Supersession | **Add as a status** | Not a content category. `current` / `superseded` / `hypothesis`. |
| Artifacts | **Add as pointers** | Paths and commands. Do not store file bodies. Anthropic’s just-in-time pattern. |
| Completed work | **Add as task status** | Visible as done, so it is not selected as current. Not a prose chapter. |
| Dependencies | **Defer** | A `depends_on` link is enough later. A justification graph is not v1. |
| Environment / test state | **Fold into reality** | The failing command, exit code, and error excerpt are reality claims. Rerunning the test can reconstruct the excerpt and should not be required to know which task is unfinished. |

### Field-by-field

Columns: why an agent needs it; what fails if it is missing; whether the environment can reconstruct it cheaply; verbatim or derived; how it goes stale; what evidence should support it.

| Field | Why | If missing | Cheap to reconstruct? | Verbatim or derived? | How it goes stale | Evidence |
|---|---|---|---|---|---|---|
| Task id + status (`active` / `blocked` / `done`) | Tells the successor which work is unfinished when several streams share a daemon | Finished work is resumed; the real task is ignored. Observed in the regression | No. The tree does not label tasks | Derived identity; status is asserted | A later completing event, or an explicit close | The events that opened it and the event that closed it |
| Intent (objective + active subgoal) | Stops the newest file save from defining the goal | Successor optimizes whatever is at the top of the feed | No, unless the user repeats it or it is in the issue text | Verbatim from the user or the task prompt. Do not paraphrase the objective | User replaces it; task is marked done | `set_intent`, or the original prompt. Not an inferred summary |
| Reality claims | The verified facts that change the next action (failing command, exit, where the check lives) | Successor re-runs, re-reads, or follows the distractor | Exit code: only by re-running. Error text: by re-running, which is the handoff tax. Location of a check: only if it was written down | Command and exit code verbatim from the event. Interpretation derived and marked as such | A later run of the same command with the opposite outcome; a contradicting observation | Event ids. Model interpretation gets lower confidence than the exit code |
| Decisions in force | The choice currently binding, plus why | Successor relitigates or picks the discarded option | No, once the rationale is not in a commit message | Verbatim statement of the choice. Rationale can be shortened only if the choice text stays | An explicit superseding decision. Age alone must not retract it | The event or claim that recorded it; link to the claim it replaces |
| Constraints | Rules that outlive the conversation (“do not change the public signature”) | Task “succeeds” while violating the rule. COMPINT’ s actual failure | No. They are defined as not part of the task text and not in the repo | Verbatim. Do not summarize | Explicit lift, or task done. Compaction must not drop them | User or agent utterance that stated them, kept as its own claim |
| Ruled out | Stops repeated dead ends | Repeated-work rate goes up; forbidden-action rate goes up | No, if the scratch experiment was deleted from the tree | Verbatim approach name plus the observation that killed it | New evidence reopens it, by an explicit status change | The failing observation. “We tried X” without a result is an unknown, not a ruled-out |
| Unknowns / hypotheses | Tells the successor what is still open so it does not treat a guess as a decision | Guess is followed as fact, or the open question is rediscovered from scratch | Sometimes, by reading the code. The fact that it is unresolved is not in the tree | Verbatim question | Resolved into a decision or a reality claim, or abandoned | Whatever suggested it, at low or medium confidence |
| Next action | The frontier. Component 0 measures turns until the first on-frontier action | Successor explores until it stumbles onto the same step | No. “The next correct step” is not git status | Derived from the open claims. One step, not a plan dump | Any new reality or decision that moves the frontier | The claim it is trying to resolve |
| Artifact refs | Lets the agent open the file or rerun the command without pasting contents | Agent searches the repo for something the predecessor already had open | The bytes are in the tree. The identity of the relevant file often is not | Pointers only | File deleted or task done | The event that touched them |
| Confidence | Separates an exit code from a model’s theory. Handoff Debt: over-trust is a failure mode | Successor treats notes as ground truth | Not reconstructable; it is a property of the claim | Stored on the claim. Discrete: `observed` / `inferred` / `hypothesis` | Replaced when a stronger observation lands | The support link. `observed` requires a sensor or a tool result |
| Supersession link | Makes the old claim unusable as current without deleting history | Stale-as-current rate rises. MemoryAgentBench’s failure mode | No | The link is derived by a rule (same command, later exit) or by an explicit write | The link itself is historical and should not be edited | Both claim ids and the event that caused the change |
| Evidence pointers | Audit and Component 0 placement checks. TMS explanation | Claims cannot be verified or retracted when the support disappears | The raw event is in SQLite if it was stored | Ids, not copies | The event can be archived; the id should still resolve or be marked gone | The event row |
| Dependencies between claims | Needed when retracting a belief should retract what rested on it | Stale conclusions survive their evidence | No | Derived | Support retracted | Justification records. **Not in v1** beyond `evidence_ids` |
| Full test log / stdout | Sometimes the assertion text is the whole bug | Agent knows “exited 1” and not why | Re-running the test reconstructs it. That rerun is a cost, not a free read | If captured, verbatim excerpt, capped. Do not store the full log as the state | The next run of that command | The shell event, which today does not contain it |

**Reconstruction rule for v1.** If a fact is in the frozen workspace (file body, diff), store a pointer. If recomputing it requires a test run or a search the predecessor already did, store the outcome as a reality claim. If it was never written anywhere except the agent’s conclusion, it must be an authored claim or it does not exist.

---

## 5. Candidate working-state models

Four representations. All of them keep the raw event history. None of them is “a better summary.”

### A. Typed claim list

A flat set of claims: `kind`, `status`, `text`, `confidence`, `evidence_ids`, `task_id`.

- Current vs historical: status field.
- Conflicts: two `current` claims of the same kind on one task, detected by a uniqueness rule.
- Fresh handoff: render every `current` claim.
- Weakness: the list does not say how it was produced, so a schema change cannot be replayed. Overwrite loses the superseded text unless status is sticky.

### B. Hierarchical plan

Task → subgoals → claims, stored as a tree.

- Matches “objective then subgoal then next action.”
- Weakness: evidence, contradictions, and superseded decisions are not a tree. Concurrent tasks become a forest that still has to be flattened into a packet. Easy to slide back into prose at each node.

### C. Belief graph

Nodes are claims, edges are `supports`, `contradicts`, `supersedes`, `depends_on`. The graph is what the agent is shown, or what retrieval walks.

- This is the TMS-shaped model and the honest long-term provenance model.
- Weakness: it reintroduces retrieval, which this phase is not supposed to build. MemoryAgentBench says graph-and-retrieve does not resolve conflicts. The agent needs the IN-set, not the graph.

### D. Event log + materialized claim view

SQLite `events` stays append-only. A projector folds the log into the claim table. The claim table is mutable only as a cache of that fold, plus explicit authored events (`claim_asserted`, `claim_superseded`) that are themselves log records. The packet is a pure render of claims where `status = current`, grouped by task.

- Current vs historical: the log is history; the view is current. Superseded claims stay in the view with status, and in the log forever.
- Unfinished vs done: task status is a fold (opened, not closed; or a failing check with no later pass).
- Conflicts: the projector refuses two current decisions on the same subject, or stores both as `hypothesis` until one is superseded.
- Fresh handoff: render the view. The successor never sees the trajectory unless it asks for a claim’s evidence ids.
- This is event sourcing. A and C are views over D, not competitors.

### Comparison

| Need | A. Claims | B. Tree | C. Graph as store | D. Log + view |
|---|---|---|---|---|
| Current vs historical truth | Status field | Awkward | Edge | Native |
| Unfinished vs done | If modelled | Natural | Extra | Task row in the view |
| Provenance | Id list | Weak | Native | Native, via the log |
| Confidence | Field | Field | Field | Field on the view |
| Conflicts | Rule | Weak | Native, then someone must still pick | Projector rule, log keeps both observations |
| Superseded decisions | Status | Easy to orphan | Edge | Status plus the superseding event |
| Open hypotheses | Kind | Node | Node | Kind + status `hypothesis` |
| Concurrent tasks | `task_id` | Forest | Partition | `task_id` on the view |
| Fresh-agent handoff | Render the list | Render the tree | Requires a traversal policy | Render the view |
| Rebuild after a schema change | No | No | Partial | Replay the log |
| Avoids building retrieval now | Yes | Yes | No | Yes |

**Reject B as the canonical store.** A tree is a legal render of tasks and subgoals.

**Reject C as the canonical store for v1.** Keep the option to add `depends_on` later. Do not make the agent query a graph to learn what is in force.

**A is the shape of the view. D is the architecture.** A without D cannot be regenerated when the fold rules improve, which matters because the first projector will be wrong.

---

## 6. Recommended v1 representation

**Canonical store:** the existing append-only event log, plus authored claim-events written into that same log.

**Canonical current state:** a materialized claim view, rebuilt by a deterministic projector. Working memory becomes this view, not a deque of the last 256 events.

**Canonical packet:** a render of that view, not a ranked event list. Reading order inside the focal task:

1. Task status and intent
2. Constraints
3. Reality claims (`observed` first)
4. Decisions in force
5. Ruled out
6. Unknowns
7. Next action
8. Artifact pointers
9. Other active tasks, one line each
10. Done tasks, one line each, marked done

Superseded claims do not appear as current. They appear only in a marked `superseded` group when the episode’s harm test needs them, or when a ruled-out item is the surviving form of an old decision.

### Claim

```text
Claim
  id
  task_id
  kind            reality | intent | decision | constraint | ruled_out
                  | unknown | next_action
  status          current | superseded | hypothesis
  confidence      observed | inferred | hypothesis
  text            the claim, not a transcript
  artifact_refs   paths or commands, optional
  evidence_ids    event ids
  supersedes      claim id, optional
  updated_at_ms
```

```text
Task
  id
  status          active | blocked | done
  title
```

Uniqueness for v1, enforced by the projector:

- At most one `current` intent per task.
- At most one `current` next_action per task.
- At most one `current` reality per (task, subject). Subject for a shell failure is the normalized command. A later exit code supersedes the earlier one.
- Many current constraints, decisions, ruled-out items, and unknowns.
- Two current claims that contradict and have no supersession rule become `hypothesis`, not a merged sentence.

### What the projector may derive without a model

From events that already exist:

- A non-zero shell exit becomes a `reality` claim, confidence `observed`, text from the command and the exit code.
- A later exit 0 of the same normalized command supersedes it.
- A git commit does not become the next action. It can close a task only when that link was authored. Otherwise it is an artifact event on an unknown or separate task.
- File events become artifact refs on the task that was active when they were observed, not their own claims.

No model is required for the regression’s selection failure. The summary “exited 1” is already derivable. The finding about `_assign_requests_to_connections` is not. That remains an authored claim.

### What must be authored

Intent, constraints, decisions, ruled-out approaches, unknowns, and the next action when it is not the obvious “inspect this failing command.” Authorship is an event (`claim_asserted` / `claim_superseded`), so the view can be rebuilt. `set_intent` becomes “assert an intent claim on the focal task,” not a side table with no task.

### What v1 explicitly does not build

- Embedding retrieval, graph walk, or paging as the way to choose state
- Automatic compaction or a summarizer
- A justification engine that retracts dependents
- Stdout capture in the shell hook
- Raising `RECENT_LIMIT`

Search, the graph, and the archive stay available behind their existing tools. They are not on the path that answers “what is in force.”

### Trust

Handoff Debt’s instruction stands: rendered claims are evidence, and `observed` outranks `inferred` outranks `hypothesis`. The packet should carry confidence so the successor can verify the repo instead of obeying a note.

---

## 7. Current → target gap map

| Current | Required | Disposition |
|---|---|---|
| SQLite `events` | Append-only evidence log, including authored claim-events | **Keep.** Add event kinds for claim assert/supersede. Do not store mutable claims only in a side table with no log line. |
| `WorkingSet` deque, 256 / 30 min | Materialized current claims for active tasks | **Replace** the deque as the meaning of “working memory.” Keep an in-RAM cache of the view if reads must stay off disk. |
| Ingest score (`heuristics.rs`) | Loudness is not belief | **Keep** as a noise filter for what is worth storing. **Stop using it** to decide what is currently true or what the briefing leads with. |
| `decide` / `Promote` | Failure should become a reality claim | **Modify.** The failure branch should emit a claim, not only a retention verdict. Drop stays a storage filter. |
| Summaries | Display text for a claim or an evidence line | **Keep** as a rendering aid. **Do not** promote the summary to the state. |
| Knowledge graph | Co-occurrence index | **Keep**, off the handoff path. **Do not** retarget it into a belief graph in v1. |
| Embeddings + `search_context` / `recall_similar` | Optional recall of old evidence | **Keep** as tools. **Do not** use them to build or select the working view. |
| Archive + pruner | Cold evidence | **Keep.** Prune events, not current claims. A current claim whose event was archived must still render, with the evidence id marked archived. |
| Intent table + `set_intent` | Per-task current intent | **Modify.** Write a claim-event. Keep the MCP tool name so agents do not break. |
| `context_now` snapshot of recent events | Render of current claims | **Replace** the packet body. Same tool. |
| Broker ranker and token budget | Fit the rendered view to the budget | **Modify** the budget to trim evidence tails and done-task lines first. It must not trim constraints to fit commits. Ranking events inside a window goes away for this packet. |
| MCP stdio forwarder | Same boundary: daemon owns state, MCP owns nothing | **Keep.** |
| Daemon session ULID | Process id, not task id | **Keep** as diagnostics. **Add** task ids. |
| Findings, decisions, unknowns, constraints, ruled-out, supersession | Claim kinds and status | **Add.** |
| Shell stdout/stderr | Evidence text for reality claims | **Not in this prototype.** Record the gap: exit code is `observed`; assertion text is absent until a sensor change. |
| Component 0 atom labels | The checker for the view | **Keep** the ladder and the metrics. Point them at claims. |

Nothing else needs a rewrite for the first prototype. Sources, the bus, SQLite, enrichment-as-summary, and MCP transport stay.

---

## 8. Smallest prototype

One projector and one renderer, measured on the existing briefing-selection episode. No new retrieval.

**Input.** The episode’s stored events (the 31-row replay) and, if present, authored claim-events.

**Projector rules, only these:**

1. Group shell commands by normalized command line.
2. The latest exit for each command is a `reality` claim. Non-zero → still failing. Zero → supersede the failure.
3. The oldest non-superseded failure is the focal task’s reality, even if it is event 31 of 31.
4. A commit does not outrank that claim and does not mark the task done.
5. If no intent was authored, the task title is the failing command, not the commit subject.
6. `next_action` is “inspect this failing command” with the command string copied verbatim.
7. Authored claims, if any are injected, pass through with their kind and status.

**Renderer.** `context_now` returns the view in the section 6 order, under the existing 1,500-token budget. It may append the evidence ids. It does not append the ranked top-10.

**Out of scope.** Weight changes, `RECENT_LIMIT`, embeddings, graph, stdout capture, a general claim-write UI beyond a single test seam that inserts a `claim_asserted` event so the finding path can be tested once.

**Done when**, on every generation of `briefing-selection`:

- the pytest is a current reality claim and is marked failed
- the Session B commit is not the focal next action
- the packet contains that claim without depending on the event’s position in the deque

That is the selection failure. A second, separate check inserts one authored finding and one constraint and asserts both survive a rebuild of the view from the log. That check exists so the prototype does not “pass” by only special-casing pytest.

---

## 9. How Component 0 evaluates it

Use the ladder and metrics already fixed in `docs/component-0-evaluation.md`. Do not add a composite score. Protocol for this prototype is **forced placement**: the rendered view is inserted into the prompt. Voluntary MCP use is a later adoption test.

The target comparison is `contextd` versus `oracle` on a fresh agent that does not receive the trajectory. `oracle` versus `none` must already separate, or the episode is leaked and cannot rank a representation.

| Question | Metric already defined | How each representation is scored |
|---|---|---|
| Continuation fidelity | Turns to first on-frontier action; handoff tax vs `none` | Same successor, same frozen workspace, packet is the only difference. A view that names the failing pytest should move the first action onto that command. A feed headed by the commit should not. |
| Required-state retention | Required-atom recall | Gold atoms (`reality`, `decision`, `constraint`, `finding`, `next`) matched against the packet. The projector passes only the atoms it can derive or was given. Recall of the pytest failure is the regression gate. |
| Stale-state handling | Stale-as-current rate; supersession accuracy | A later passing run must show the failure as superseded, not as a second current fact. `stale-only` remains the negative control: unmarked old decisions should raise forbidden actions versus `none`. |
| Decision and constraint violations | Constraint-violation rate; forbidden-action rate | Constraints and ruled-out claims are either present and marked current, or the violation is charged to state/selection. This is the COMPINT cut. |
| Repeated work | Repeated-work rate against the dead-end manifest | Ruled-out claims are what should suppress re-enactment. A summary that mentions the dead end in passing is not credited unless the atom matcher finds it as ruled-out, not as a current plan. |
| Task completion | Continuation solved rate, plus constraint checks | Report it. Do not declare a win on solved rate alone. The regression’s `none` arm already solves while contextd does not; a representation that merely copies `none` is not the goal. |
| Active-context tokens | Tokens in the placed packet | The view must fit the same budget class as the oracle packet. A dump of 31 events can win recall and still fail this. |
| Oracle gap | Paired deltas, contextd vs `oracle` vs `summary` vs `structured-notes` | `summary` tests prose compaction. `structured-notes` tests one-shot extraction into the same schema. If the projector ties `structured-notes`, the schema is doing the work. If it beats it, the fold is doing the work. If both lose to `oracle`, the missing atoms were never authored or never observed. |

Representation-specific expectations, not new metrics:

| Representation | Likely pass | Likely fail |
|---|---|---|
| Today’s event feed | Persistence of the pytest row | Selection, stale-as-current (commit looks current), required-atom recall |
| Prose summary of the log | Some recall on short episodes | Constraint retention, supersession, audit against atoms |
| Typed claims without a log | A single handoff | Rebuild, conflicting observations, “why is this in force” |
| Belief graph the agent must traverse | Provenance questions | Fresh handoff under a small budget; conflict resolution if retrieval chooses |
| Log + claim view | Selection of the failing command; marked supersession; constraint survival across rebuild | Facts that were never observed and never authored. That failure is a sensor or a write-API gap, and the ladder should say so |

Stage attribution for the current regression stays: observation and persistence succeeded, state was never built, selection therefore failed. The prototype is a pass only if stage 2 (state) and stage 3 (selection) both flip for the pytest atom, and a planted constraint is still present after the view is rebuilt from the log alone.

---

## 10. Open decisions

These change the prototype. Defaults are what I will assume unless you say otherwise.

1. **Who may write claims?** Default: the projector writes `observed` reality from exit codes; everything else is an authored event. Allowing the agent to write decisions is required for non-reobservable state. It also creates the over-trust failure Handoff Debt measured. Confidence labels are the mitigation, not a human review queue.

2. **What is a task, before anyone names one?** Default: one implicit task per daemon session, split only when an authored intent says so, except that a failing command becomes the focal reality inside that task. Finer automatic clustering is a later project and will guess wrong.

3. **Does `context_now` still return recent events beside the view?** Default: no. Evidence is available by id through the existing search tools. Returning both recreates the regression, because the commit will still look like the headline.

4. **Are superseded claims in the default packet?** Default: no, except ruled-out items, which stay current as negative claims. Superseded decisions are queryable, not shown, until an episode proves the successor repeats them unless it sees the retraction.

5. **Stdout capture.** Default: not part of this work. The hook’s constraint (never block the prompt, never clobber `$?`) is real. The prototype is allowed to hand off “exited 1” without the assertion text.

6. **Focal task when several are active.** Default: the task with a current non-zero reality claim; if several, the one with the newest supporting event; the others render as one line. Tell me if you would rather pin the focal task explicitly and ignore recency entirely.

---

## 11. Primary sources

| Source | Why it is in this note | Link |
|---|---|---|
| Dipesh & Budathoki, *Handoff Debt*, 2026 | Coding-agent takeover. Structured continuation fields. Efficiency gains exceed solved-rate gains. Raw trace wins information and loses boundedness. | https://arxiv.org/abs/2606.02875 |
| Wang et al., *Lost in Compaction* (COMPINT), 2026 | Session constraints retained at ~17% inside summaries; a side list exceeds 90%. | https://arxiv.org/abs/2608.11242 |
| Zhu et al., *SWE-ContextBench*, 2026 | Right compact experience helps; full trajectories help less; free summary retrieval can hurt. Cross-task, not handoff. | https://arxiv.org/abs/2602.08316 |
| Wu et al., *LongMemEval*, ICLR 2025 | Knowledge update and abstention as memory abilities. | https://arxiv.org/abs/2410.10813 |
| *MemoryAgentBench*, ICLR 2026 | Conflict / selective forgetting is unsolved by current memory agents (paper: multi-hop ≤ ~28%; dataset card: ≤ 7% on the highlighted methods). | https://proceedings.iclr.cc/paper_files/paper/2026/file/fd1eff9dd295df50a41f2521942fa31d-Paper-Conference.pdf |
| Anthropic, *Effective context engineering for AI agents* | Smallest high-signal set; compaction target list; just-in-time identifiers; structured notes. | https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents |
| Packer et al., *MemGPT*, 2023; Letta memory blocks | Always-in-context labelled blocks vs archival recall. OS-style paging, which we are not building. | https://arxiv.org/abs/2310.08560 · https://www.letta.com/blog/memory-blocks/ |
| Nakajima, *The Log is the Agent*, 2026 | Event log as source of truth; working graph as a fold. | https://arxiv.org/abs/2605.21997 |
| Doyle, *A Truth Maintenance System*, 1979 | Belief separated from inference; retraction. | https://doi.org/10.1016/0004-3702(79)90008-0 |
| de Kleer, *An Assumption-based TMS*, 1986 | Assumptions, nogoods, multiple contexts. | https://doi.org/10.1016/0004-3702(86)90080-9 |
| Alchourrón, Gärdenfors, Makinson, 1985 | Belief revision: minimal change when a new fact contradicts the current set. | https://doi.org/10.2307/2274239 |
| Nii, *Blackboard Systems*, AI Magazine 1986 | Shared structured board rather than a message feed. | https://ojs.aaai.org/aimagazine/index.php/aimagazine/article/view/537 |
| Fowler, *Event Sourcing* | Log versus materialized view. The architectural pattern, not an agent paper. | https://martinfowler.com/eaaDev/EventSourcing.html |
| contextd, *Component 0 evaluation* | Ladder, arms, and metrics this design must submit to. | `docs/component-0-evaluation.md` |
| contextd, *Briefing selection failure* | The concrete episode: stored failure, selected commit. | `docs/briefing-selection-failure.md` |

---

## Bottom line

contextd already records evidence and already fails when the evidence is older, quieter, or about a different task than the newest commit. The successor does not need a smarter ranking of those events. It needs the current claims: which task is unfinished, which facts and constraints are in force, which approaches are dead, and what to do next, each pointing at the event that supports it.

Build that as a fold over the log you have. Measure it by whether a fresh agent, given only the fold, behaves like an agent given the human oracle handoff.
