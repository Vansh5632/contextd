# Component 2 research

Research note for the gap Component 1 left open. Written 2 November 2026 from the limiter-handoff episode, the Component 0 and Component 1 measurements, and the papers below.

This note does not change the projector. The design that follows it is `docs/component-2-design.md`. The measurements are `docs/component-2-validation.md`.

## The question

Component 1's claim briefing made a fresh agent solve the pool-cap bug 5/5 and arrive on the failing pytest sooner than an agent with no briefing. Required-atom recall stayed 0. On 30 October 2026 total tokens were 195,493 against 195,765 for no briefing. The human oracle, which names the idle-count decision and the create-branch edit, used about 75,066 tokens. The automatic packet never contained those notes, because they were never events.

The question for this component:

> Can contextd automatically preserve what an agent learned, rather than merely what commands it executed, and does that measurably reduce rediscovery for the next agent?

## What was already true

The fold in `crates/memory/src/project.rs` is a pure function of shell exit codes and authored claim events. A non-zero exit becomes an observed reality, `Re-run and inspect: <command>`, and the focal task when it is the oldest failure. Exit 0 supersedes that reality. File and git rows stay in the log and do not become tasks. Authored claims survive a rebuild. That selection contract is what Component 1 froze. This research does not reopen it.

The 31-event replay (`episodes/limiter-handoff/regressions/briefing-selection/baseline/replay/gen-1/events.jsonl`) stores 24 shell rows, file rows, and one git commit. The only non-zero shell exit is `python -m pytest -q -p no:cacheprovider tests/test_pool_counts.py`. Its reality text is `<command> exited 1`. Stdout is not in the payload. The shell hook (`crates/sources/src/install.rs`, `emit.rs`) sends `command`, `exit_code`, and `cwd` only.

`eval/component0/harness/contextd_replay.py` builds the typed command list with an explicit skip:

```102:107:eval/component0/harness/contextd_replay.py
    """Session A then B. Notes are skipped. The followup commit is appended last."""
    out: list[TypedCommand] = []
    for session, steps in sessions:
        for step in steps:
            if step.kind == "note":
                continue
```

The notes were not dropped by the projector. They were never offered to it.

## Where each gold atom actually is

The predecessor for this episode is the scripted Session A in `history/session_a.script.yaml`, recorded with command output in `history/history.jsonl`. It is not a live model rollout. The notes are the expressed record. The gold file `gold/atoms.yaml` is a matcher over that record. It is not part of the trajectory.

| Atom | Said in a note | In a tool output | In the 31 shell events |
|---|---|---|---|
| `finding_idle_not_counted` | Unit 18, the full conclusion | Unit 17 shows `if not c.is_idle()` and the create-new-connection branch. It does not state the conclusion | The `sed` row, exit 0. No output, no conclusion |
| `decision_idle_counts` | Unit 21 only | Absent. The code implements the opposite | Absent |
| `constraint_signatures` | Unit 8 only | Unit 4 prints the constructor. It does not say to keep it | Absent |
| `constraint_no_new_deps` | Unit 8 only | Absent | Absent |
| `ruled_out_mock_backend` | Unit 12, including "Ruled out as the cause" | Unit 11 prints connections left `IDLE`. It does not say the backend is ruled out | The probe command, exit 0 |
| `ruled_out_lock` | Unit 16, including "ruled out" | Unit 15 prints two `IDLE` connections and one `ACTIVE`. It does not name the lock | The probe command, exit 0 |
| `next_step_create_branch` | Unit 24 only | Absent as an instruction | Absent |
| `stale_redis_counter` | Units 19 and 20. The idea is proposed, then rejected. Nothing was run | Absent | Absent |
| `unresolved_keepalive_comprehension` | Unit 23, including "out of scope" | Unit 22 contains the `len([connection.is_idle() ...])` expression | The `sed` row, exit 0 |
| `optional_exact_counts` | Unit 16 ("3 held with max_connections=2") and unit 3 | Unit 2's pytest output contains `count 3 diverged from 2` | Exit code 1 only. The assertion text is gone |
| `optional_host_header_fix` | Session B notes, and the commit that followed | The diff in unit 39 | The commit subject. Session B is a finished task |

Two distinctions follow.

**Expressed, then dropped.** Every required atom is a sentence in Session A. The replay skipped notes. The shell sensor cannot see them. A projector, however correct, cannot render a sentence that was never an event. Component 1's recall of 0 is this gap.

**Observable in tool output, also dropped.** The pytest assertion, the `IDLE` lines, the `not c.is_idle()` condition, the constructor text, and the keepalive expression are in `history.jsonl`. They are not in the shell payload. The successor can re-run the test or re-read the file. The handoff tax is that re-run. The judgment on top of the output (ruled out, out of scope, the decision) is not in the output at all.

**Never expressed.** For the required atoms, this set is empty. Nothing in `gold/atoms.yaml` that the oracle packet contains was absent from Session A's notes. The gold file was written from those notes. An extractor that reads the gold file would be circular. An extractor that reads the notes is reading the trajectory.

Session B is a different case. Those notes were expressed, the commands passed, and the commit landed. The unfinished task is the pool pytest. Copying the finished host-header investigation onto that task would mix two pieces of work. The tree already contains the commit.

## What a live sensor still does not have

`docs/working-state-representation.md` section 1.2 is unchanged. Shell events have no stdout. Git events have a subject, not a body. File events have a path, not contents. Non-interactive agent shells do not source the hook unless something else injects the command. The Component 0 replay injected commands and dropped notes.

So there are two stores:

| Store | Notes | Command text | Exit code | Stdout |
|---|---|---|---|---|
| `history.jsonl` | yes | yes | yes | yes, truncated at 200 lines |
| Daemon event log | no | yes | yes | no |

The smallest prototype reads the first store, because that is the observable trajectory this benchmark already kept. It does not teach the shell hook to capture stdout, and it does not add a new sensor. Until a live session writes notes and outputs into the log, the daemon still cannot extract them.

## What the papers change, and what they do not

### Claimify

Metropolitansky and Larson, *Towards Effective Extraction and Evaluation of Factual Claims*, ACL 2025, [arXiv:2502.10855](https://arxiv.org/abs/2502.10855). Claimify splits an answer into sentences, drops sentences with no verifiable content, refuses a sentence whose ambiguity the context cannot resolve, and decomposes the rest into standalone claims. They report that 99% of extracted claims are entailed by the source sentence. Inferred context is allowed only inside brackets, so a reader can see it was not in the sentence.

The part this project takes: a claim must be entailed by a source span, unverifiable narration is dropped, and ambiguity is abstained from rather than resolved by guess. The part it does not take for the first prototype: an LLM rewrite. On this episode the load-bearing sentences are already explicit. A rewrite is the step that can add a fact the note did not say.

### Mem0

Chhikara et al., *Mem0*, [arXiv:2504.19413](https://arxiv.org/abs/2504.19413). Extraction runs on a new message pair plus a summary and a recent window, off the reply path. An update step retrieves similar memories and asks a model to ADD, UPDATE, DELETE, or NOOP. The paper's DELETE is how a contradiction is supposed to leave the store. The current product documentation describes the automatic path as additive, with update and delete left to an explicit call ([How Mem0 works](https://docs.mem0.ai/core-concepts/how-it-works)). Those two descriptions are not the same system. contextd already has an explicit status, `current` or `superseded`, on the claim. A vector similarity step that might delete the wrong row is a worse fit than the slot the projector already supersedes.

### SeCom

Pan et al., *On Memory Construction and Retrieval for Personalized Conversational Agents*, ICLR 2025, [arXiv:2502.05589](https://arxiv.org/abs/2502.05589). Turn-level units are too small and session-level units are too noisy. Topical segments, denoised with a prompt compressor, retrieve better on LOCOMO and Long-MT-Bench+. SeCom then concatenates the retrieved segments. It does not assign a type, a confidence, or a status. Segmentation would matter if contextd were searching a long archive. This component renders one focal task. A segment summary would be a second prose briefing, which Component 1 already refused.

### ACE

Zhang et al., *Agentic Context Engineering*, ICLR 2026, [arXiv:2510.04618](https://arxiv.org/abs/2510.04618). Contexts are itemized bullets, not a rewritten summary. A generator produces a trajectory, a reflector proposes lessons, and a curator merges small deltas with non-LLM logic. They report about +10.6 points on agents and +8.6 on finance, and they treat full rewrites as the cause of context collapse. The transferable rule is the delta: append a claim, do not regenerate the page. The reflector is an LLM pass over a whole trajectory. That pass is the latency and the fabrication risk. The curator idea, a deterministic merge into an existing store, is the projector we already have.

### Handoff Debt, again

Dipesh and Budathoki, EMNLP 2026, [arXiv:2606.02875](https://arxiv.org/abs/2606.02875). Structured notes split fields a program can fill (files, validation command, validation outcome, solved versus unfinished) from fields a model writes (what was understood, what failed, what is uncertain, what to do next). Notes are labelled as evidence to verify. The gain is largest after a validation failure. The program half is the shell fold Component 1 already does. The model half is this component, with one change in confidence: a sentence the predecessor wrote is `stated` or `inferred` from that sentence, not `observed`.

COMPINT ([arXiv:2608.11242](https://arxiv.org/abs/2608.11242)) is why a constraint is its own object. A summary retains about 17% of session constraints. A side list retains more than 90%. The renderer already has that side list. The missing piece is a writer that puts the sentence on it.

## Four ways to extract

| Approach | What it would do here | Cost on this episode | Failure mode |
|---|---|---|---|
| Deterministic, from structured events only | Fold exit codes. That is Component 1 | None added | Required-atom recall stays 0. The notes are not events |
| Deterministic, from the trajectory's own speech | Quote a note when it is a constraint, a decision, a ruling, a next step, an explicit "out of scope", or a conclusion grounded in the preceding file read | One local pass. No model call | Misses a conclusion that was never phrased as one of those acts. Abstains on open suspects |
| LLM extraction over the trajectory | Ask a model to emit the claim schema from notes and outputs | One extra call per handoff, plus the fabrication risk Claimify measured | A fluent claim that the note does not entail. The gold file would have to be kept out of the prompt, and the call would still be trusted less than a quote |
| Checkpoint of the last message only | Keep whatever the predecessor said at the end | Small | Unit 8's constraints and unit 12's ruling are not in the last note. COMPINT's recency failure |
| Hybrid | Deterministic quotes now. An LLM only for sentences that are not a known speech act, still required to quote a span, still gated | The LLM runs only when the deterministic pass abstains | Not needed on this episode: the required atoms are speech acts or a grounded file-read note |

The prototype is the second row. The hybrid is the later step, and only for sentences the deterministic pass skips. It is not built here. A checkpoint of the last message would drop the constraints. An LLM rewrite is not required to recover this episode, and it is the step most likely to invent a fact.

## What this does not decide

- Whether a live Codex rollout phrases the same facts as cleanly as Session A's script. This episode cannot answer that.
- Whether stdout should be added to the shell event. The trajectory file already has it. The hook does not.
- Whether the decision should remain after the task is marked done. Component 1's renderer already drops it then. Extraction does not move that rule.
