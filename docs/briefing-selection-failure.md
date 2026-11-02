# Briefing selection failure

Diagnosis of one replay. Session A’s failing pytest was stored and labelled correctly, then left out of the briefing. The agent therefore resumed from Session B’s commit and treated finished work as the current task.

This note records what failed, which component caused it, and what the briefing did as a result. No code has been changed.

## What the run stored

The daemon held 31 events from the episode, all inside working memory (capacity 256, horizon 30 minutes). The failing command was the oldest of those 31. Its stored summary was:

```text
ran `python -m pytest … tests/test_pool_counts.py` — exited 1
```

The failure was recorded. The summary already says the command exited 1. The briefing never selected that row.

## What the briefing did

`context_now` with no query built a 10-line recent list and a related-history list anchored to the newest event, which was the Session B commit.

| What the agent needed | What the briefing contained |
|---|---|
| Session A’s failing pytest, including that it exited 1 | Absent. It was event 31 of 31, outside the newest-10 window |
| The error text (“count 3 diverged from 2”) | Absent. The shell event has an exit code and no stdout or stderr |
| Session A’s conclusion about where the cap check lives | Absent. There is no place to store a finding |
| One picture of an unfinished task | A flat list whose top line is a commit, which reads as work finished |
| History related to the failure | 3 Session B items and one Session A `grep`, because search used the newest event as the question |

Observation recall for the required fact was 0. The fact was in SQLite. It was not in the packet.

## Direct causes

These four sit on the same path. The row survives ingest, and selection never reads the fields that say it matters.

### 1. Snapshot builder cuts to the newest 10 before ranking

**Component.** `crates/broker/src/snapshot.rs` (`RECENT_LIMIT`, `build`) and `crates/memory/src/working.rs` (`recent`).

**What it does.** Working memory keeps the session. The briefing asks that set for the newest 10 events, then `rank()` sorts those 10 by recency and importance. Importance can reorder the window. It cannot pull in an older event. If working memory is empty, `get_recent_events` applies the same limit in SQLite.

**Why it is shaped this way.** The original broker was “10 recent events plus 5 nearest neighbors.” Ranking was added later, on top of that window, so the last ten file saves are not listed in arrival order. The in-RAM set is a separate bound (256 events, 30 minutes) so a build burst does not leak memory and lunch does not look like the present.

**Outcome.** The failing pytest was still in RAM and still in the database. It was dropped before any score was consulted. This is the reason it was missing from this run.

### 2. Importance scorer ignores the exit code

**Component.** `crates/pipeline/src/heuristics.rs` (`process_event`), consumed by `crates/broker/src/relevance.rs`.

**What it does.** On the ingest path, a shell command is scored from its text. `cargo build`, `npm run`, and a command string that contains `error` score 0.9. `cd` and `ls` score 0.1. Every other command, including pytest, scores 0.6. The exit code is not read. File saves score 0.3. Git commits score 0.8.

Ranking then mixes recency (weight 0.35, 20-minute half-life) with that importance (weight 0.20). Similarity and graph proximity are zero when the local model is down.

**Why it is shaped this way.** The scorer has to be fast and infallible, because an unscored event is not stored. It records the kind of action (build, navigation, manifest), not the outcome. Outcome was assigned to the decision module, which runs later.

**Outcome.** The failing pytest scored the same 0.6 as every `sed` and `grep`. All 31 events happened within about 75 seconds, so their recency scores are effectively identical. Equal importance falls back to “newer timestamp wins.” Even after the window in item 1 is widened, a commit at 0.8 still outranks the failure, and the failure ties with ordinary commands. It would appear in a briefing that can hold the whole burst (31 short lines fit in the 1,500-token budget). It would not stand out.

### 3. Enrichment records the failure, then does not use that verdict

**Component.** `crates/pipeline/src/decision.rs` (`decide`, `is_durable`), `crates/pipeline/src/content.rs` (`looks_like_failure`, `summarize_shell`), `crates/background/src/enrichment.rs`, `crates/pipeline/src/classify.rs`, `crates/store/src/db.rs` (`get_prunable_events`), `crates/background/src/pruner.rs`.

**What it does.** `decide()` returns `Promote` for a non-zero exit code. The comment there says failures are the events “have I hit this before?” needs, and that the scorer will not mark them as loud. Enrichment writes use case, memory type, and summary for every verdict except `Drop`. `Drop` deletes the row. `Promote` does not change the score. `Decision::is_durable()` is only called in tests.

Memory type answers a different question: would this still matter in a month? A one-off command is `episodic`. Commits, manifests, and recipe commands (`make`, `npm run`) are the durable kinds. The pruner archives rows older than 7 days when the score is below 0.5 and the memory type is missing or `episodic`. It does not read `Promote`.

**Why it is shaped this way.** Ingest may only drop noise, because working memory and the graph cannot retract a row. Classification is allowed to lag. The verdict was specified as the shared policy for the pruner, the archiver, and the broker. Only the Drop branch was connected.

**Outcome.** The system knew this event was a failure: the summary says “exited 1”, and the verdict is `Promote`. The broker ranks on the ingest score, which stayed 0.6. This pytest is above the pruner’s 0.5 threshold, so this particular row would stay in the live table. A process-exit failure, which the scorer marks 0.2 and `decide()` still promotes, would be archived after 7 days like ordinary episodic noise.

### 4. Semantic history follows the newest event

**Component.** `crates/broker/src/snapshot.rs` (`query_text`, `load_relevant_history`, `related_to_event` anchor) and `crates/memory/src/graph.rs`.

**What it does.** With no query string, the search text is the summary of `recent_events.first()`. That list is still newest-first, because ranking has not run. The same event anchors the knowledge graph. Declared intent is copied into the snapshot and is not used as the search text. The `text` argument of `context_now` is the search query; omitting it means “use whatever just happened.”

**Why it is shaped this way.** The product question is “what is going on?”, and the design treats the latest observation as the statement of that question. Intent is a standing goal that sits beside the observations. The one-shot focus and the standing goal were kept as separate fields so an older goal does not replace the question being asked now. Graph edges are co-occurrence within five minutes, not importance.

**Outcome.** Related history was similar to the Session B commit: three Session B items and one Session A `grep`. A declared intent would have been printed and would not have changed the search.

## What made the ten lines noisy

These did not drop the pytest by themselves. They filled the window the pytest needed, and they made the remaining lines read as finished work.

### 5. File events are stored one kernel notification at a time

**Component.** `crates/sources/src/filesystem.rs`, `crates/sources/src/noise.rs`, `crates/sources/src/git.rs`, `crates/sources/src/install.rs`.

**What it does.** The noise filter drops generated directories (`target/`, `node_modules/`, `.git/`), editor junk, and contextd’s own database. A source file written by `git apply` passes. There is no debounce and no merging of events that share a timestamp. The summary maps the kernel kind straight through, so a notification reported as `Create` becomes “created” even when the file already existed. The git hook and the shell hook are separate sensors: the hook sends hash and subject, the shell sends the typed command.

**Why it is shaped this way.** Filtering at the source avoids paying an embedding and a briefing slot for build output. The watcher records what the kernel reported. The hook exists because a commit message is better information than a change under `.git/`. The shell hook exists so typed commands are noticed. Nothing assigns those two events a shared identity.

**Outcome.** One `git apply` produced 6 file events at the same timestamp, including contradictory “created” lines. Four of them took briefing slots. The commit was logged twice, once by the hook and once by the shell. One patch and one commit occupied 6 of the 10 slots.

### 6. The briefing has no unfinished task

**Component.** `ContextSnapshot` in `crates/broker/src/snapshot.rs`.

**What it does.** The snapshot is one intent, one daemon `session_id`, a ranked list, related history, related entities, and an archive hint. `session_id` identifies the daemon run. There is no grouping by task or command, and no link from a failing run to a later run of the same command.

**Why it is shaped this way.** The product is one shared picture of the current moment, so every assistant reads the same thing. Two pieces of work in one daemon run become one timeline.

**Outcome.** A commit at 0.8 sorts above ordinary commands and reads as the latest milestone. Nothing in the snapshot can say that an earlier command is still failing.

## Observation gaps

These did not cause this replay’s miss. The replay injected events on purpose. They would make a real agent session record less than the replay did.

### 7. The shell hook reports the command and the exit code

**Component.** `crates/sources/src/install.rs`, `crates/sources/src/emit.rs`, with the summary path in `crates/pipeline/src/content.rs`.

**What it does.** The installed snippet runs from an interactive prompt (`PROMPT_COMMAND` on bash, `preexec`/`precmd` on zsh, `fish_postexec` on fish). The payload is command, exit code, and working directory. The send is backgrounded and always returns success, and bash saves `$?` before doing any work. Newlines are stripped so the socket stays one JSON object per line. Bash skips a history line it has already sent.

**Why it is shaped this way.** A snippet that hangs a terminal, or that clobbers the exit status a prompt displays, is worse than a missing event. Capturing stdout would mean wrapping the command. The error extractor in `content.rs` is written for payloads that already contain `stderr` or `output`; when those fields are missing it falls back to “exited N”. The bash history check exists because the prompt redraws even when no new command ran. Zsh does not need it, because `preexec` fires once per command.

**Outcome.**

- The agent sees “exited 1”, not the assertion text.
- A heredoc is flattened onto one line (`python - <<'EOF'import httpcore…`).
- On bash, fail then fix then rerun-the-same-command drops the passing run, because the history line did not change. Zsh and fish send every command.
- Codex and similar agents run non-interactive shells, which never source this hook. A real agent session records no shell events. The replay bypassed that.

### 8. Findings have nowhere to go

**Component.** `set_intent` in `crates/mcp/src/server.rs` and `crates/memory/src/working.rs`. Git payloads in `crates/sources/src/git.rs`.

**What it does.** Intent is the only field the user authors. Everything else is observed. The git hook sends the commit subject, not the body. The editor source exists in the event enum and emits nothing.

**Why it is shaped this way.** contextd is a watcher under the assistants the user already has. A stated goal sits beside observations. Conclusions were expected to be recoverable from error lines, commit subjects, and, later, editor diagnostics.

**Outcome.** Session A’s actual conclusion — the cap check is in `_assign_requests_to_connections`, and why — could not be stored. `set_intent` can hold a goal. It cannot hold a finding.

## Not contextd

These came from the replay scripts and did not cause the miss:

- the `/patches/session_b_fix.patch` path
- the `-p no:cacheprovider` flag
- the compressed timeline, which kept every event inside the 30-minute working-memory horizon

## How the causes chain

```text
shell / filesystem / git
        │
        ▼
heuristics score          exit code is not read; pytest stays 0.6
        │
        ▼
ingest                    row stored; summary later says "exited 1"
        │
        ▼
decide() → Promote        verdict is not written back onto the score
        │
        ▼
recent(10)                oldest of 31 events is discarded
        │
        ▼
rank()                    reorders only the survivors
        │
        ▼
history search            question = newest survivor (Session B commit)
        │
        ▼
briefing                  failure absent; commit reads as current work
```

Items 1–4 are the selection chain. Item 1 is why this packet missed. Items 2 and 3 are why a wider window would still rank the failure like any other command, and why a quieter failure (score under 0.5) would later be archived despite `Promote`. Items 5–8 are why the ten lines were noisy, and why a live agent session would observe less than this replay did.

## Regression episode

This replay is kept as a Component-0 regression in `eval/component0/episodes/limiter-handoff/regressions/briefing-selection/`. `regression.yaml` names the target event, the checks, the contextd build, and the live outcome (contextd 0/5, none 5/5). `baseline/` holds the three replay generations, the packet provenance and ladder, and the scored live trials, each pinned by sha256.

From `eval/component0`:

```text
python -m harness.regression check
    baseline files intact, and the failure still reproduces (stored, not selected)

python -m harness.contextd_replay --no-freeze
python -m harness.regression check --id briefing-selection --candidate build/contextd-replay
    a rebuilt contextd passes when the failing pytest is stored, selected, and marked failed in every generation
```

`validate` runs the first check as gate R1.
