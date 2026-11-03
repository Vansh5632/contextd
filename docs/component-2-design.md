# Component 2 design

Design for the smallest extractor that can feed Component 1. Written 2 November 2026 from `docs/component-2-research.md`. The measurements that follow this design are in `docs/component-2-validation.md`.

## Decision

At a handoff, read the daemon event log. Notes are `source: note`. Command output is `payload.output` on the shell event. Quote a note only when it is a load-bearing speech act, or when it is a conclusion grounded in the immediately preceding shell output. Admit the quote through Component 1's existing claim validator and fold. Render with the existing `context_now` projector.

Do not add a memory store, a retriever, a compactor, or a pager. Do not change `crates/memory/src/project.rs` or `crates/broker/src/snapshot.rs`. The frozen selection contract stays: one task per shell command, the latest exit is the reality, the oldest non-zero exit is focal, a commit does not outrank it, exit 0 supersedes that failure, authored claims survive a rebuild.

The extractor is a separate binary, `project_claims`, and a Python proposer, `eval/component0/harness/extract_claims.py`. Neither runs while a command is executing.

## Why this shape

The required atoms on limiter-handoff are sentences in Session A's notes. They are not in the 31 shell events. An extractor that sees only those events has nothing to quote, which is why Component 1's recall is 0. An LLM rewrite is not required to recover sentences that are already explicit, and it is the step that can add a fact the note did not say. Claimify's result (99% of claims entailed by the source sentence, inferred context bracketed) is the rule: quote, or abstain.

Mem0's paper deletes a contradicted memory by asking a model. contextd already supersedes a claim by status. SeCom's segment summary is a second briefing. ACE's useful idea is the delta merged by a non-LLM curator. The curator here is the projector.

## Pipeline

```text
event log (shell events with output, note events)
  ← import_trajectory (this episode's saved transcript → log)
        │
        ▼
extract_claims.py          quote, or record a skip reason
                           (does not open the saved transcript)
        │
        ▼
project_claims             verbatim check, grounding check,
                           ClaimEvent::validate, then fold + render
        │
        ▼
claim events on the same log
        │
        ▼
Component 1 projector  →  context_now
```

The gold file and the oracle packet are not inputs. A test reads the proposer and the gate and fails if either names `atoms.yaml`, `oracle.txt`, or `gold/`.

## What triggers extraction

Once, at the handoff, on a finished trajectory. Not after every tool call, and not on the shell hook. This episode is one checkpoint: Session A is unfinished because its pytest is still non-zero, and Session B is left off the page because every command there ended at 0.

A later live system can run the same function when a session is interrupted. It should still be off the path that records the command.

## Observation, statement, inference

| What was said or seen | Kind | Origin | Confidence |
|---|---|---|---|
| Shell exit code, written by the projector | reality | projector | observed |
| A note that starts with "Constraint" and states a rule | constraint | user | stated |
| "Decision:" | decision | agent | inferred |
| "Ruled out" or "Rejected" | ruled_out | agent | inferred |
| "Next step" | next_action | agent | inferred |
| "out of scope" | unknown | agent | hypothesis |
| A note immediately after `sed` or `grep`, with at least two content words in that output | reality | agent | inferred |
| "First suspect", "Next suspect", "Idea" | not written |  | open hypothesis |
| Narration ("Picking up", "Fails as reported") | not written |  | no speech act |

The gate refuses `observed` on any extracted claim. The only observed claim in the packet is the projector's `exited 1`. An inferred conclusion is not relabelled as something the tool printed.

Constraints are `user` / `stated` because `validate` in `crates/core/src/claim.rs` rejects an agent constraint and rejects a user claim that is not `stated`. That is the frozen write rule, not the selection contract. The evidence id still points at the note, so the page does not pretend a person typed `contextd claim`. Loosening the rule was not required: the sentence fits the existing slot.

The next-step note uses the frontier subject. Component 1 already keeps one current next action per task, so the quote replaces `Re-run and inspect:` without an edit to the fold. The failing reality stays, and it stays focal. A later non-zero exit will not overwrite a non-projector frontier; `ensure_projector_frontier` already returns in that case. This episode has no later failure of that pytest, so the interaction is not exercised.

## How a fabricated claim is refused

A candidate is admitted only if all of these hold:

1. Its text is a verbatim substring of one note.
2. The text is between 1 and 500 characters.
3. It has at least one evidence id.
4. It is attached to the task subject of the command that is still failing (`cwd` plus the command, the same string the projector hashes).
5. An inferred reality cites the preceding tool output and shares at least two content words of four or more characters with it, after a small stop list.
6. `validate` and `validate_against_projection` accept the origin, kind, and confidence.
7. The same kind and collapsed text have not already been admitted.

Open suspects are skipped, not stored as current. A note in a session whose commands all ended at 0 is skipped. A file-read note that does not overlap the output is skipped. Nothing is truncated to fit the length cap; an overlong sentence would be skipped rather than cut.

There is no model in this path, so the extraction adds no tokens to the predecessor's run.

## Provenance

Every extracted claim's evidence id is the daemon event id. For this episode the importer mints `h_<session>_<unit>`. A grounded reality also cites the shell event whose `payload.output` supports it. The projector's pytest reality keeps the shell event id it already had. Notes are `source: note` events on the log. The test checks that a note id resolves to an event whose text contains the claim, and that a shell id cited by a reality overlaps that event's `payload.output`.

## Duplicates, contradictions, supersession

Duplicates of the same kind and text are dropped. An open hypothesis is not written, so there is no current "first suspect" left beside the later ruling. The Redis idea is not written. The rejection is written, and its text contains "Rejected", which is the episode's superseded marker. The scorer then counts it as marked stale rather than stale-as-current. The projector's own supersession of a shell exit is untouched.

Two current claims can describe different things without being duplicates. The pytest reality and the idle-count finding are both current realities, with different subjects, so the unique slot does not let the finding erase the exit code.

## Cost

The admission binary on this episode finishes in well under a tenth of a second and calls no model. The placed packet is larger than the oracle (1,030 tiktoken tokens against 381) because the quotes are the notes, plus evidence ids, plus the observed pytest line. It is inside the 1,500-token render budget, so the renderer does not trim a constraint or the decision.

## Measurement

Same forced-placement protocol as Component 1. Same model, Codex version, seed, workspace tar, and budgets. Arms:

| Arm in the report | What it is |
|---|---|
| `none` | empty packet |
| `summary` | the existing summary |
| `oracle` | the human handoff |
| `contextd-new` | Component 1, the observation-only claim briefing |
| `contextd-component2` | this extractor's packet |

`contextd-new` is the Component 1 arm. A second copy of that packet was not added under another name.

Score, before any agent runs: required-atom recall and precision, stale-as-current, whether the Redis line is marked, whether the keepalive note is present, and that no extracted claim is `observed`. Then the agent matrix: solved rate, turns to the first on-frontier action, repeated work, forbidden actions, constraint violations, total tokens, and the paired delta against `none`. The gold file is used only by the scorer, after the packet exists.

## Out of scope

- Teaching the shell hook to capture stdout or notes. A later plan can attach `output` at the moment a command runs. This plan records output when the event is written by the importer or by a sender that already has the text.
- An LLM pass for sentences that are not speech acts.
- Session B's finished host-header work.
- Changing focus, retrieval, or what happens to a decision after the task is marked done.

## Unresolved before the run

Whether a packet with recall 1 and about 1,030 tokens would move the successor's tokens toward the oracle, or only move the packet score. That is the validation.
