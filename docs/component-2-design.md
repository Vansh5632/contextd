# Component 2 design

Design for the extractor that feeds Component 1, including the live daemon path added on 3 November 2026. The measurements are in `docs/component-2-validation.md`.

## Decision

At a handoff, read the daemon event log. Notes are `source: note`. Command output is `payload.output` on a shell event. Quote a note when it is a load-bearing speech act, when it is a conclusion grounded in a cited tool result, or when a user note states a normative rule. Admit the quote through Component 1's claim validator and fold. Render with the existing `context_now` projector.

Do not add a memory store, a retriever, a compactor, or a pager. Do not change the fold in `crates/memory/src/project.rs` or the renderer in `crates/broker/src/snapshot.rs`. The selection contract stays: one task per shell command, the latest exit is the reality, the oldest non-zero exit is focal, a commit does not outrank it, exit 0 supersedes that failure, authored claims survive a rebuild.

Extraction does not run on the shell hook and does not run while a command is still executing. The daemon runs it at startup, after a note is stored, and before `context_now`.

## Why this shape

The required atoms on limiter-handoff are sentences in Session A's notes. They are not in the 31 shell events. An extractor that sees only those events has nothing to quote, which is why Component 1's recall is 0. An LLM rewrite is not required to recover sentences that are already explicit, and it is the step that can add a fact the note did not say. The rule is: quote, or abstain.

A coding agent does not type its tool calls into an interactive shell. The bash hook in `crates/sources/src/install.rs` still records command, exit code, and directory, and still does not see Codex or Cursor tool calls. Those observations enter through one narrow adapter, `eval/component0/harness/agent_observe.py`, and the `observe` request on the daemon socket. The adapter records a tool result as a shell event that includes output, and the agent's own message as a note. It does not admit claims.

## Pipeline

```text
coding-agent trace                         interactive note / speech-act note
        │                                          │
        ▼                                          ▼
agent_observe.adapt_trace                  contextd note  /  shell hook
        │                                          │
        ▼                                          ▼
observe request on the socket              source:note or source:shell
        │                                          │
        └──────────────┬───────────────────────────┘
                       ▼
              SQLite event log
                       │
                       ▼  startup, after a note, or before context_now
              broker::extract::run_handoff
                       │  quote, or record a skip reason
                       ▼
              ClaimEvent::validate + validate_against_projection
                       │
                       ▼
              source:claim rows on the same log
                       │
                       ▼
              memory::project::fold  →  broker::snapshot::render
                       │
                       ▼
                    context_now
```

The offline episode path is the same gate. `eval/component0/harness/extract_claims.py` still proposes the limiter-handoff quotes, and `project_claims` admits them. `project_claims --extract` runs the Rust quoter, which is what the daemon runs. A parity test requires the two quoters to emit the same kind, text, and confidence on that episode.

The gold file and the oracle packet are not inputs. Tests read the proposer, the Rust gate, and the adapter, and fail if any of them names `atoms.yaml`, `oracle.txt`, or `gold/`.

## What triggers extraction

Not after every tool call, and not on the shell hook. Three moments, all off the path that records a command:

1. Daemon startup, after the projection rebuild, so a crash before the next query still leaves claims in the log once the process is up.
2. After `contextd note` has been stored and acknowledged.
3. At the start of `context_now` and of `contextd handoff`, so a fresh agent cannot read a page that is behind the notes.

`observe` only stores the tool event or the note. The successor's `context_now` is the handoff. Running it again is safe: a note that already supports a claim is not inserted twice.

## Observation, statement, inference

| What was said or seen | Kind | Origin | Confidence |
|---|---|---|---|
| Shell exit code, written by the projector | reality | projector | observed |
| A note that contains "Constraint" and states a rule | constraint | user | stated |
| A user note (`speaker: user`) with a normative sentence, even without the word "Constraint" | constraint | user | stated |
| "Decision:" | decision | agent | inferred |
| "Ruled out" or "Rejected" | ruled_out | agent | inferred |
| "Next step" | next_action | agent | inferred |
| "out of scope" | unknown | agent | hypothesis |
| A note immediately after `sed` or `grep`, with at least two content words in that output | reality | agent | inferred |
| A `role: conclusion` sentence with at least two content words in the cited tool output | reality | agent | inferred |
| A conclusion that says the cited output "is not the cause" (or "not involved" / "not the problem") and overlaps it | ruled_out | agent | inferred |
| A conclusion that starts with "I'll" or "I will" | next_action | agent | inferred |
| A conclusion that says "might", "maybe", "not sure", or "could be" | unknown | agent | hypothesis |
| "First suspect", "Next suspect", "Idea" | not written |  | open hypothesis |
| Narration, or a conclusion that does not overlap the cited output | not written |  | no speech act |

The gate refuses `observed` on any extracted claim. The string is `an extracted claim cannot be marked observed` in `crates/broker/src/extract.rs`. The only observed claim in a limiter-handoff packet is the projector's `exited 1`.

`role: conclusion` is not a speech-act prefix in the sentence. The adapter sets it because the event is the agent's message rather than a scratch note. Limiter-handoff notes do not carry the field, so their quotes stay the speech-act and `sed`/`grep` rules.

Constraints are `user` / `stated` because `validate` in `crates/core/src/claim.rs` rejects an agent constraint and rejects a user claim that is not `stated`. The evidence id still points at the note.

A next-step quote uses the frontier subject. Component 1 keeps one current next action per task, so the quote replaces `Re-run and inspect:` without an edit to the fold. The failing reality stays, and it stays focal.

## How a fabricated claim is refused

A candidate is admitted only if all of these hold:

1. Its text is a verbatim substring of one note, and `source_text` is that whole note.
2. The text is between 1 and 500 characters.
3. It has at least one evidence id, and every id is in the log. The first id is a note.
4. It is attached to the task subject of a command that is still failing (`cwd` plus the command, the same string the projector hashes).
5. An inferred reality cites a shell event. If `support_text` is set, it equals that event's `payload.output`. The claim and the output share at least two content words of four or more characters, after the stop list.
6. `validate` and `validate_against_projection` accept the origin, kind, and confidence.
7. The same kind and collapsed text have not already been admitted.

Open suspects are skipped, not stored as current. A note in a session whose commands all ended at 0 is skipped. A file-read note that does not overlap the output is skipped. A conclusion that does not overlap, and is not an explicit hypothesis or next step, is skipped. Nothing is truncated to fit the length cap.

Tool output stored by `observe` is truncated at 200 lines or 8,000 characters, with an `[output truncated]` mark. That is the same cap as `import_trajectory.truncate_output`. The quote is checked against the stored text, not against a longer buffer the gate never saw.

## Provenance

Every extracted claim's first evidence id is the note. A grounded reality or a grounded ruling also cites the shell event whose `payload.output` supports it. The projector's pytest reality keeps the shell event id it already had. Notes stay `source: note`. They are loaded by `store::db::get_handoff_events` and are not loaded by `get_fold_events`, so a note cannot open a task by itself.

## Duplicates, contradictions, supersession

Duplicates of the same kind and collapsed text are skipped, including on a second handoff.

An open hypothesis is not written. Two conclusions that contradict each other and do not retract each other both stay current, on different subjects. Reality is unique per subject, and an extracted reality's subject is its claim id, so one finding does not erase the other or the exit code.

An explicit retract is a note whose role is `retract` or whose text starts with "Retract". It must contain the current claim's text verbatim. The longest match is superseded. The claim stays in the log with status superseded. A retract does not invent a replacement. An observed projector reality cannot be retracted; the command has to be re-run.

An explicit update is a note whose role is `update` and whose `replaces_text` equals a current claim. The replacement is admitted only if it would pass the same gate. If it would not, the old claim stays. There is no silent overwrite of a claim the new sentence does not name.

The next-action slot is the one place an extracted claim replaces another current claim without naming it. That is the Component 1 frontier rule, and it is how `I'll ...` or `Next step` replaces `Re-run and inspect:`.

## Where the code lives

| Piece | Path |
|---|---|
| Quote, admit, retract, update, handoff | `crates/broker/src/extract.rs` |
| Offline candidate gate and `--extract` | `crates/broker/src/bin/project_claims.rs` |
| Notes excluded from the projector fold | `crates/store/src/db.rs` `get_fold_events`, `get_handoff_events` |
| Socket verbs `observe` and `handoff` | `crates/core/src/protocol.rs`, `bin/contextd/src/main.rs`, `bin/contextd/src/cli.rs` |
| Coding-agent adapter | `eval/component0/harness/agent_observe.py` |
| Speech-act proposer for the frozen episode | `eval/component0/harness/extract_claims.py` |
| Second episode, natural sentences | `eval/component0/episodes/retry-budget/trajectory.jsonl` |

## Cost

The handoff reads the log and writes claim rows. It does not call a model. On the retry-budget trajectory, `project_claims --extract` finished in 0.095s with a warm binary, including process start. The limiter-handoff page is 1,032 tiktoken tokens (`o200k_base`) and the daemon estimate is 1,015, inside the 1,500-token render budget, so the renderer does not trim a constraint or the decision.

## Measurement

Same forced-placement protocol as Component 1 for the agent matrix. Arms:

| Arm in the report | What it is |
|---|---|
| `none` | empty packet |
| `summary` | the existing summary |
| `oracle` | the human handoff |
| `contextd-new` | Component 1, the observation-only claim briefing |
| `contextd-component2` | this extractor's packet |

`contextd-component2.txt` is generated and gitignored. `harness.arms` does not put it in the frozen manifest, so `arms check` does not depend on whether the file exists. Frozen hashes of history, raw, summary, oracle, stale-only, atoms, prompt, wrapper, and `contextd.txt` are unchanged. A trial may still place the generated arm: `build_prompt` accepts it when the arm is in `GENERATED_ARMS` and the packet file is present.

Score, before any agent runs: required-atom recall and precision, stale-as-current, whether a rejected line is marked, whether an unresolved note is present, and that no extracted claim is `observed`. Then the agent matrix: solved rate, turns to the first on-frontier action, repeated work, forbidden actions, constraint violations, total tokens. The gold file is used only by the scorer, after the packet exists.

## Out of scope

- Capturing stdout from the interactive shell hook. Coding-agent tool calls do not travel through that hook. A sender that already has the output uses `observe`.
- An LLM pass for sentences that are neither speech acts nor grounded conclusions.
- Context paging, compaction, or a second briefing.
- Changing focus, retrieval, or what happens to a decision after the task is marked done.
- Session B's finished host-header work on limiter-handoff.

## Limitations

- Dedup is identical kind and collapsed text, not a paraphrase match.
- A conclusion the adapter did not mark, and that does not use a speech-act prefix, is skipped. `contextd note` of an ordinary sentence does not become a claim.
- "I'll" and "might" are the natural next-step and hypothesis cues. Other wordings abstain.
- A handoff is not one SQLite transaction around every sentence of a note. A crash mid-pass can leave the first sentence cited and the rest unwritten until a later note.
- The shell hook still does not store stdout. A grounded reality needs output from `observe` or from the trajectory importer.
