# Component 2.1 design

> Current investigation record (10 November 2026), not a frozen specification. Measurements below are recorded results, not a fresh run for this documentation refresh. See [current architecture](architecture.md), [status](status.md), and the [documentation index](README.md).

Written 10 November 2026 from `docs/component-2.1-research.md`. The smallest change that rejects a new cause sharing a real identifier, and that keeps the true half of a mixed sentence.

## Decision

Validate clause by clause. Keep a clause only when all of these hold:

1. It is not a question.
2. It does not deny an increment the cited output shows. A denial in one clause is not saved because the sentence also contains the counter's name.
3. "Before" and "after" agree with the order of those identifiers in the output.
4. Every content word is either in the cited output or in the fixed interpretive list in `extract.rs` (`count`, `branch`, `failure`, `expects`, `never`, `toward`, and the rest of that list). One word outside both drops the clause.
5. The clause shares at least one content word with the output, and the whole sentence shares at least two. The two-word bar is still only an anchor.

If every clause passes, the original sentence stays one claim. If only some pass, each passing span is its own claim. The span is a verbatim substring. Nothing is rewritten.

A hedge ("might", "maybe", "could be", "not sure") stays a hypothesis and does not have to pass the residue check. An "I'll …" line stays a next action. A `Decision:` line, a user constraint, and an explicit "Ruled out" or "Next step" prefix stay on the speech-act path. A retract still quotes one current claim. Opposite claims of the same kind are still demoted to hypotheses instead of one replacing the other.

`admit` uses the same residue rule, so a hand-built full sentence cannot bypass the splitter. `crates/memory/src/project.rs` and `crates/broker/src/snapshot.rs` are not edited. There is no model call.

## Why the interpretive list is closed

The pool finding says "branch" and "counts" where the code says `elif` and `len(...)`. Those words are how a person describes control flow. "Database", "pool", "exhausted", "keepalive", and "eviction" are not in the list. Keepalive and eviction are dropped even though a reader can see them in the control flow. The licensed clause, "only counts connections that are not idle", still matches the required atom, and the decision note is a separate sentence. Growing the list until keepalive fits would also be how "pool" gets in. A model is the tool for that paraphrase, and the research note says when it would be warranted. It is not added here.

## What this does not do

It does not add "do not open timeouts.py" to the page. The predecessor did not write that sentence. The 9 November repeated read was a directory listing on the empty page as well. Quoting a ruling is not an instruction to skip a file the agent discovers with `rg --files`.

It does not merge two contradictory notes into one text. It does not mark an extracted claim `observed`.

## Cost

No model call. The handoff stays a local pass over the notes. The retry-budget page does not change. The pool page loses the keepalive and eviction clauses and keeps the licensed clause, the decision, the constraints, the rulings, and the next step.
