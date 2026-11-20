# Component 2.1: detailed failure analysis

Reviewed 11 November 2026 against the research snapshot of 10 November. This report summarizes 20 application-analysis assignments and 20 research assignments. It describes failures and limitations; it does not claim that a replacement has been implemented.

## How to read the evidence

- **Reproduced:** the saved isolated daemon check exercised `observe -> handoff -> now` and recorded the result. Its tool output was supplied by the test client, not independently obtained from a live source file.
- **Source analysis:** the relevant implementation was inspected. The described end-to-end scenario has not been executed against the daemon in this study.
- **Contract limitation:** an implemented, sometimes intentional boundary prevents the broader behavior we want. Calling it a bug requires deciding the intended contract.
- **Open:** a timing, recovery, or semantic claim still needs a targeted experiment.

Forty completed assignments are not forty independent runtime tests. Most worker examples were static readings or small helper transcriptions. Six public-protocol cases were recorded: one supported control and five problematic outcomes. Those selected cases do not estimate the failure rate in real workloads.

## Reproduced failures

All five cases below used this evidence, with small variations in the submitted conclusion:

```python
def deliver(self, message):
    self.retry_count += 1
    self.open_socket()
```

### 1. A negated finding was admitted as a current reality

**Conclusion:** `retry_count never increments before open_socket`.

**Expected:** identify the conflict with the positive increment in this listing, or decline to admit the statement as supported.

**Recorded result:** admitted as agent-origin, inferred reality.

**Why:** `denies_update` recognizes a fixed list, including `not counted`, `does not increment`, and `stays at zero`. It does not recognize `never increments`. Meanwhile, `never` and `increments` are allowed interpretive words, and the identifiers supply sufficient overlap.

**Effect:** a statement and its negation can receive different treatment because of wording rather than evidence.

Source: [extract.rs](../crates/broker/src/extract.rs), `INTERPRETIVE`, `evidence_relation`, `denies_update`, and `polarity_contradiction`.

### 2. A false numerical magnitude was admitted

**Conclusion:** `retry_count increments by 999 before open_socket`.

**Expected:** the listing licenses an increment of 1, not 999.

**Recorded result:** admitted as agent-origin, inferred reality.

**Why:** content tokens must start like words. Numeric-leading tokens are discarded, even when they have four or more characters. The increment scanner reads `+= 1`, but the contradiction checks primarily use whether that increment is positive; they do not compare the claimed delta against it.

**Effect:** correct identifiers can anchor a claim containing an incorrect number. Static examples also suggest wrong versions, durations and thresholds can escape comparison. Those additional examples were not daemon-tested.

Source: [extract.rs](../crates/broker/src/extract.rs), `keep_token`, `starts_like_word`, `increments`, and `sentence_names_increment`.

### 3. A close paraphrase of the supported finding was rejected

**Conclusion:** `the retry counter is incremented before the socket opens`.

**Expected:** retain the useful inference from this simple listing.

**Recorded result:** skipped as unsupported.

**Why:** the checker licenses exact content tokens or words in a fixed interpretive list. `counter` and `opens` are not equivalent to `count` and `open` in that mechanism.

**Effect:** valid explanations are lost when their language differs from source identifiers. The more technical domains and naming styles we add, the more vocabulary-dependent this behavior becomes.

Source: [extract.rs](../crates/broker/src/extract.rs), `content_tokens`, `clause_has_residue`, and `interpretive`.

### 4. An unsupported non-English explanation was retained

**Conclusion:** `retry_count is incremented before open_socket。数据库连接池耗尽。`

The Chinese sentence says that the database connection pool is exhausted; the supplied listing does not establish that.

**Expected:** preserve the supported finding without granting support to the added explanation.

**Recorded result:** the entire text was admitted as inferred reality.

**Why:** tokenization recognizes ASCII alphanumeric characters and underscores. Non-ASCII words contribute neither overlap nor unsupported vocabulary. The fullwidth sentence punctuation is not an ASCII sentence delimiter. English identifier anchors satisfy the checks while the added text remains unexamined.

**Effect:** parts of a sentence can survive without actually participating in verification. This is a coverage failure, not multilingual support.

Source: [extract.rs](../crates/broker/src/extract.rs), `sentences`, `content_tokens`, and `evidence_clauses`.

### 5. A Unicode case conversion caused query-task panics

**Input:** a conclusion containing `Ω` before a clause marker.

**Expected:** accept, reject or skip the input without breaking the request handler.

**Recorded result:** a byte-boundary panic at `extract.rs:1600`. The tested handoff and subsequent `now` requests returned `query handler unavailable`. The daemon process remained alive.

**Why:** `evidence_clauses` computes positions on a lowercased string, then applies them to the original string. Some Unicode lowercase conversions change UTF-8 byte length. A valid boundary in one string can lie inside a character in the other.

**Effect:** ordinary text can break briefing generation. The study did not test persistence after restart, all other tools, or recovery behavior, so it does not establish a whole-daemon crash.

Source: [extract.rs](../crates/broker/src/extract.rs), `evidence_clauses`.

## Meaning failures found through source analysis

### 6. Text order stands in for execution order

The order checker takes the first matching textual positions around `before` or `after`. It does not analyze call graphs, branches, argument evaluation, or repeated identifier occurrences.

For example, a function definition can contain an increment before another function's definition, while the second function actually opens the socket before calling the first. Comments and string literals can supply the first occurrence too.

**Predicted effect:** a true runtime-order conclusion may be rejected, and a false one may be accepted. The implementation proves neither causal order nor general execution order. Some examples are limitations against a broader contract: the present code explicitly implements identifier order in output.

Evidence: source analysis of `order_contradiction` and `anchored_ident`; not an end-to-end runtime result for these examples.

### 7. Quantifiers and conditions can lose their meaning

Short words such as `not`, `no`, `all`, and `if` do not survive the content-token filter. `only` is a stop word. There is no general representation of conditional scope or quantified statements.

Against a non-idle connection filter, formulations such as `counts connections if idle` and `counts connections if not idle` can therefore have the same content-token representation. Some alternatives such as `unless` instead introduce unsupported vocabulary and are rejected.

**Predicted effect:** a meaningful reversal can disappear from the representation, while a useful equivalent phrase fails closed. Particular increment-denial patterns still have their separate checks; it would be incorrect to say every negation is ignored.

Evidence: source analysis and worker helper transcriptions, not daemon runs for these particular pairs.

### 8. Shared vocabulary does not establish causality

Words such as `cause` and `means` are licensed interpretive vocabulary. If two identifiers occur in evidence, that vocabulary can connect them without a representation of the claimed causal relationship.

**Predicted effect:** both `A is the cause of B` and the reverse may satisfy the same lexical conditions. A listing showing an update followed by a call does not establish every explanation expressed using those names.

Evidence: source analysis of `INTERPRETIVE`, overlap and clause rules. The causal examples were not daemon-tested.

### 9. Clause preservation depends on punctuation and wording

The splitter recognizes five literal markers: `so`, `because`, `means`, `and`, and `but`, with spaces. A semicolon, colon, bare comma, parenthetical expression, or another conjunction can produce a different split. Sentence splitting has its own ASCII punctuation-plus-whitespace rule.

**Predicted effects:**

- A supported prefix can survive before `so` but be discarded when the same unsupported tail is attached with a semicolon.
- A cut inside a parenthetical expression can remove an essential qualification.
- A fragment can pass the first clause-selection threshold of one overlapping token but fail the later reality-admission threshold of two.
- Mixed hedged and unhedged pieces are handled differently from a sentence whose pieces are all hedged.

Evidence: source analysis of `sentences`, `kept_texts`, `keep_clause`, and the second `evidence_relation` checks.

## Evidence and provenance failures

### 10. Summarization can remove evidence required by the checker

The background compactor preserves selected identity keys but omits `output`. The checker later reads the cited row's `output`, not its summary. A classified long event can also be compacted on a subsequent enrichment pass.

**Predicted effects:** a conclusion can cite an existing event whose relevant evidence has been removed; a new reality is then insufficiently supported. An already admitted claim can retain its text and evidence ID even though the original supporting output is no longer there.

Long-note compaction can omit `role`, `speaker`, and `support_event_id` as well. The exact timing of compaction versus handoff, including which notes are processed twice, remains open; this study did not run that race.

Sources: [enrichment.rs](../crates/background/src/enrichment.rs), `compact_payload` and `enrich_one`; [extract.rs](../crates/broker/src/extract.rs), `support_event` and `admit`.

### 11. Truncated evidence is checked without its missing context

Tool observations retain at most 200 lines and 8,000 characters, with a truncation marker. The relation checker does not model the omitted region as a separate uncertainty condition.

**Predicted effect:** a conclusion may depend on a qualification or operation beyond the retained prefix. A first textual mention in the prefix may be misleading once the later code is omitted. The marker's words also enter ordinary tokenization.

This is different from absent output: empty output is explicitly insufficient. The missing-context examples were not daemon-tested.

Source: [extract.rs](../crates/broker/src/extract.rs), `truncate_output` and `evidence_relation`.

### 12. Multiple evidence events are not composed

The observation interface has one `support_event_id`. Candidate admission can retain several evidence IDs, but its relation check selects the first shell event and examines one output string.

**Predicted effect:** a conclusion requiring both a code listing and a test result is not verified against their combination. Concatenating several files into one output does not supply file boundaries or a program model; textual order is still order of printing.

This is a contract limitation of the current checker, not a demonstrated failure of a promised multi-evidence engine.

Source: [extract.rs](../crates/broker/src/extract.rs), `support_event`, `speech_draft`, and `admit`.

### 13. A conclusion can be attached to the wrong task

Task projection keys commands by cwd plus normalized command. Extraction's failing-command lookup groups by payload session and command while omitting cwd. Selection of a task and selection of cited evidence are separate operations.

**Predicted scenarios:**

- Two commands are failing; a conclusion cites the second command's output but attaches to the first still-failing task.
- The same command fails in one directory and passes in another; extraction groups them while the projector keeps separate tasks.
- Missing payload sessions share the empty-string bucket, which can mix hook traffic and default observations.

These are source-derived association risks. They were not reproduced as multi-task daemon tests in this study.

Sources: [extract.rs](../crates/broker/src/extract.rs), `shell_state`, `failing_command`, `note_task`, and `support_event`; [project.rs](../crates/memory/src/project.rs), `shell_subject`.

### 14. Word-based classification can misattribute an agent note to the user

`drafts_for_note` checks for the word `constraint` before checking the note's speaker. It then emits matching normative sentences as user-origin, stated constraints. The normative recognizer itself handles a small vocabulary, including signatures, constructors, named request methods and dependency restrictions.

An agent conclusion or quoted assertion containing `constraint` and `keep the signature unchanged` can therefore take the user-constraint branch. Conversely, a genuine user requirement such as `Keep the public CLI unchanged` may not match that recognizer.

**Predicted effect:** the provenance label can overstate who supplied a requirement, while other real requirements are omitted. Typed `contextd claim constraint` is a different path and does not share this phrase-recognition limitation.

This is a provenance-contract concern, not a claim of an OS authorization boundary being crossed. Runtime examples remain untested.

Source: [extract.rs](../crates/broker/src/extract.rs), `drafts_for_note`, `normative`, and `user_constraints`.

## State maintenance and presentation failures

### 15. A passing command does not invalidate every authored finding

The projector supersedes the latest command-outcome reality for the same command subject. Extracted realities instead have their own subjects and `outcome: None`. They can remain visible as authored continuation after the command passes.

**Risk:** a test can now pass while a related finding that was valid only for the old code remains current. A pass does not logically falsify every old finding, so deleting all of them would also be wrong. The missing capability is explicit knowledge of which claims require revalidation after which changes.

Related source limitation: reopening an existing Done task does not change its status back to Active, even if a later command event updates it. Whether that is incorrect requires deciding the intended completion/reopening policy.

Sources: [project.rs](../crates/memory/src/project.rs), `fold_shell` and `assert_claim`; [claim.rs](../crates/core/src/claim.rs), `open_task`; [snapshot.rs](../crates/broker/src/snapshot.rs), `focal_view` and `open_work`.

### 16. Retraction, deduplication and conflict handling have scope gaps

Automatic retraction selects the longest current claim whose text occurs in a note. Exact-text update lookup and conflict scanning range over the projection rather than being consistently restricted to a note's task. Global deduplication remembers asserted kind/text pairs, including superseded assertions.

**Predicted effects:** text shared across tasks can select an unintended target, conflict handling can affect another task, and a later legitimate restatement can be blocked as a duplicate. Copying a user target's origin into an automatic retraction/update can also misrepresent the authority of the correcting note.

The conflict detector recognizes only narrow count/increment/retry stances. It does not generally detect mutually exclusive decisions. If an existing claim has no note evidence, demotion can leave it unchanged while turning the new claim into a hypothesis.

Evidence: source analysis of `longest_quoted_claim`, `current_with_text`, `seed_seen`, `conflicting_current`, `demote_claim`, `retract_note`, and `update_note`; no cross-task runtime reproduction.

### 17. Artificial timestamps can reverse correction order

Extracted claim events receive `max(existing timestamp) + 1000 ms`. A newly submitted CLI correction receives actual wall-clock time. The projection replays events by timestamp and ID.

**Predicted sequence:** an extracted assertion is dated slightly into the future; a valid immediate correction receives an earlier timestamp; replay applies the correction before the assertion exists; the supersede finds no target; the later assertion remains current.

The timestamp assignments, ordering, and no-op behavior for missing targets are visible in source. This exact timing scenario has not been executed and needs a regression test before being called a reproduced correction failure.

Sources: [extract.rs](../crates/broker/src/extract.rs), `push_event` and `sort_log`; [main.rs](../bin/contextd/src/main.rs), claim timestamp creation; [claim.rs](../crates/core/src/claim.rs), `supersede_claim`.

### 18. Uncertainty and token trimming can discard useful continuation state

Recognized hedges are a short phrase list. `might` can create an unknown while `probably` or `I think` follows a different path. A hypothesis surviving without supporting output is intentional uncertainty preservation; it is not evidence that the hypothesis is true.

Notes starting with `first suspect`, `next suspect`, or `idea` are skipped as a whole, potentially losing a useful later sentence. Separately, the renderer sorts equal-confidence claims by ascending update time and removes from the end when trimming unknowns or extra decisions. Newer items are therefore removed first within those groups.

**Risk:** meaningful differences in uncertainty are reduced to phrase matches, and recent continuation details can be dropped under budget pressure. The estimate is not an exact tokenizer count, and protected content can exceed the nominal budget. These presentation scenarios were source-analyzed, not benchmarked here.

Sources: [extract.rs](../crates/broker/src/extract.rs), `hedge`, `drafts_for_note`, and `classify_span`; [snapshot.rs](../crates/broker/src/snapshot.rs), `focal_view` and `trim_task`.

## System boundaries and evaluation failures

### 19. Some routes never perform this evidence check

Direct MCP `note_claim` validates origin, kind, confidence and replacement rules but does not run Component 2.1 grounding. Several speech-act routes also return before ordinary clause checking. Unmarked source-read extraction recognizes `sed` and `grep`, with different behavior for other readers unless a note is explicitly marked as a conclusion.

**Effect:** a stored inferred finding does not establish that the semantic evidence gate approved it. This is partly an intentional distinction between authored memory and extracted findings; it becomes a failure if consumers interpret all displayed claims as equally verified.

Observation coverage matters too: shell hooks do not capture stdout, and filesystem/Git/process events do not independently supply an agent's reasoning. Those are capture limitations, not errors that a better semantic verifier alone can repair.

Sources: [main.rs](../bin/contextd/src/main.rs), `record_agent_claim`; [extract.rs](../crates/broker/src/extract.rs), `drafts_for_note` and `is_source_read`; [install.rs](../crates/sources/src/install.rs), shell payload generation.

### 20. The current evaluation can miss the errors we care about

The two main episodes belong to a similar family: failing Python tests, short source listings, and recognizable notes. Some recognizers contain domain- and example-specific terms. This limits what current success can establish; it does not experimentally prove that every good result is caused by overfitting.

Packet recall is measured against fixed atom regexes. A unit can count as useful after matching a useful atom. Stale-as-current depends on recognized stale patterns. The scorer does not independently verify the relationship between each claim and its cited output.

**Effect:** a false statement outside the stale patterns can escape that metric. A partially correct unit can receive relevance credit without proving every clause. Perfect required-atom recall does not establish semantic correctness or completeness outside the atom list.

There is also a recorded usefulness tradeoff: the stricter pool page retained required-atom recall 1.00 yet the 10 November report recorded 4.40 mean turns and 120,734 mean tokens, compared with 2.80 and 97,957 for the previous automatic page. Those are historical small-run comparisons, not new performance measurements or a complete causal explanation of the slowdown.

Sources: [packet_score.py](../eval/component0/harness/packet_score.py), `classify_unit` and `score_packet`; [Component 2.1 validation](component-2.1-validation.md).

## Assessment and limits

The strongest demonstrated problems are false admission, false rejection, unchecked text coverage, and query availability. Source analysis expands the concern to evidence preservation, association, authorship, revisions, conflicts and evaluation.

Some issues are local correctness defects. Others require a clearer product contract: what a hypothesis means, when a finding expires, which author is authoritative, whether evidence describes supplied text or live repository truth, and what counts as a task.

Increasing overlap thresholds or adding words to the interpretive list does not resolve all these categories. A learned verifier also would not, by itself, repair evidence deletion, task association, provenance or event ordering.

No new runtime experiments, fixes, full test suites, or candidate-verifier benchmarks were performed while writing this report. Source-derived scenarios remain source-derived until exercised. Runtime prevalence and performance across unseen repositories remain unmeasured.

## Local research records

These generated records exist on the research machine and are Git-ignored; they may be absent from another checkout:

- `docs/superpowers/research/component21-durable-20261110/coverage.md`: 40 primary assignment/session outcomes.
- `docs/superpowers/research/component21-durable-20261110/final-audit.json`: independent artifact and protected-file audit before this documentation promotion.
- `docs/superpowers/research/component21-durable-20261110/problem-specification.md`: first-wave cross-review, corrections and unresolved disagreements.
- `docs/superpowers/research/component21-durable-20261110/research-synthesis.md`: second-wave cross-review.
- `docs/superpowers/research/component21-generalization-20261110T150531Z/selected-public-reproduction.json`: the six recorded daemon cases and stderr.

The detailed report itself is self-contained; source links above identify the implementation behind each mechanism.
