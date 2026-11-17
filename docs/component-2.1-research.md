# Component 2.1 research

> Current investigation record (10 November 2026), not a frozen specification. Measurements below are recorded results, not a fresh run for this documentation refresh. See [current architecture](architecture.md), [status](status.md), and the [documentation index](README.md).

Written 10 November 2026, before the clause change. The baseline is the 9 November checker in `docs/component-2-evidence-validation.md`. Component 1's fold and renderer are not part of this question.

The goal is a page a new agent can use, where a verified claim is licensed by the tool output it cites, a guess stays a guess, and two disagreeing notes do not silently become one fact.

## Where a bad claim still gets in

Admission is `kept` only after `evidence_relation` in `crates/broker/src/extract.rs`. That function already does three things:

- A denial phrase ("not counted", "stays at zero") plus `name += <nonzero>` in the cited output is a contradiction.
- "A before B" when the output shows B then A is a contradiction.
- The sentence is split on "so", "because", "means", and "and". A clause with no content word in the output is insufficient.

A clause with one shared identifier is enough. "The retry_count database pool is exhausted" shares `retry_count` with the listing and has no split marker, so the 9 November checker calls it supported. That is the hole.

The same function drops a whole sentence when any clause has zero overlap. "deliver increments retry_count before open_socket, so the database pool is exhausted" is rejected entirely, including the half the listing does show.

The pool-bug finding is one sentence. Its clauses about keepalive and eviction use nouns that are not in the `sed` output. They stay because each clause also says "idle" or "max_connections". The licensed half, "only counts connections that are not idle", is the half the code shows (`not c.is_idle()` inside the length check).

A `Decision:` line, a user constraint, and an "I'll …" next step never reach this check. A "might" sentence is a hypothesis before the check runs. A retract still removes only the quoted current claim. Opposite decisions of the same kind are demoted to hypotheses; one does not replace the other.

On 9 November the retry-budget page did not contain the false "not counted" sentence. Fresh agents still solved both tasks, reached the edit sooner than with the observation-only page, and broke no constraint. On retry-budget every Component 2 rep also read `timeouts.py` once, inside a command that listed the repo (`cat courier.py host.py timeouts.py`). The empty page and Component 1 did the same. The human note says not to edit that file. The predecessor never wrote that prohibition, and the checker only quotes. A directory listing is not fixed by rejecting a false claim.

## What each approach can actually do

Measured on the sentences below. Gold files were not inputs. "Current" is the 9 November checker. "Clause residue" is the rule implemented after this note: a clause is kept only when every content word is in the cited output or in a fixed list of interpretive words (count, branch, failure, expects, and the same family). A denial is not kept because a sibling clause is true. If every clause passes, the original sentence stays one claim. If only some pass, those verbatim spans are the claims.

| Sentence | What it is | Current | Clause residue |
|---|---|---|---|
| retry_count 2 where the test expects 1 | correct, against the pytest line | kept | kept |
| increments retry_count before open_socket, so the first send is already counted | correct, against the listing | kept | kept |
| timeout path is not the cause because deliver has no timeout handling | correct ruling | kept | kept |
| create branch only counts connections that are not idle, so idle keepalive … and the idle-eviction branch … | correct finding; later clauses name keepalive and eviction, which the listing does not | kept whole | only the licensed clause |
| might be involved | hypothesis | hypothesis | hypothesis |
| The retry_count database pool is exhausted | false, same clause as a real name | kept | rejected |
| retry_count before open_socket means the database pool is exhausted | mixed | rejected whole | true prefix kept |
| increments retry_count before open_socket, so the database pool is exhausted | mixed | rejected whole | true clause kept |
| not counted, so retry_count stays at zero | contradicted | rejected | rejected, and the denial is not salvaged |
| open_socket before retry_count | reversed order | rejected | rejected |
| two opposite Decision lines | contradiction | both hypotheses | unchanged |
| Retract of a quoted claim | retraction | removes that claim | unchanged |

Raising the overlap count does not do this. The 9 November probe already showed a bar of 4 shared words drops the true timeout ruling and still accepts the contradicted sentences.

A model verifier could mark "keepalive" and "eviction" as entailed by `is_idle()` and the following `elif`, which the clause rule drops. It could also accept "database pool" or a fluent denial. One call per conclusion adds a network round trip and a bill at every handoff. The required pool atom still matches the licensed clause, and the decision note is a separate speech act, so that call is not required for the atoms this benchmark scores. The case to give a model later, if a required atom sits only in words the output never uses, is a paraphrase noun such as keepalive. It is not the default.

Deterministic residue does not stop a successor from `cat`-ing every file in a five-file tree. That repeated read is not a claim the predecessor made.

## Measurement

The adversarial set is the table above, plus the two saved trajectories. It is locked by the extractor tests, not by a second model. Scores that matter:

- Unsupported sentences admitted as current realities. Baseline: the same-clause database sentence. After: none of the false sentences in the table.
- Supported sentences retained. The retry-budget page is byte-identical. The pool page keeps required-atom recall 1.00.
- Contradictions and retractions. Unchanged from 9 November, and a denial clause is not reattached.
- Provenance. A kept span is still a verbatim piece of the note, and a reality still cites the shell event.
- Fresh-agent behavior. Retry-budget is not rerun: the placed page did not change, so the 9 November matrix is the comparison. The pool page did change, so that matrix is rerun under the same model, seed, images, and workspace tar.
- Latency and tokens. The check is local. The 9 November handoff was 0.0046 seconds and called no model. This change adds no model call.
