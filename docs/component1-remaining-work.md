# Component 1 remaining work

Audit of the working tree on 30 October 2026, after the validation in `docs/component-1-validation.md`.

The tree is `bf1826c` plus the uncommitted Component 1 changes. No source file under `bin/contextd`, `crates/memory`, `crates/broker`, `crates/core`, `crates/store`, `crates/pipeline`, or `crates/sources` is newer than `target/debug/contextd` (built 2026-10-30 19:36:47 +0530, the binary the validation measured). This note reads that code and the validation artifacts. It does not change the projector, the renderer, or the selection rule.

The briefing-selection failure is closed. The oldest stored non-zero shell exit stays focal, and the Session B commit does not. That result is not reopened here.

---

## 1. Executive summary

Component 1 does what the 30 October validation measured. A fresh agent with the automatic claim briefing solved the pool-cap bug 5/5 and reached the frontier in a median of 5 turns. The old event briefing solved 0/5. Total tokens stayed level with no context. Required gold-atom recall stayed 0 because those notes were never events.

What remains is not “make the pytest focal.” That already holds. What remains is the lifecycle around that rule, the packet that rule produces, and a few paths the validation did not exercise.

Confirmed and still open:

- A task closed with `claim done` stays done when the same command fails again. The new failure is `current` in SQLite and absent from the briefing. Reproduced on the validation binary.
- After the focal command exits 0, supersession in the claims table is correct, and the renderer then emits only the trust notice. A constraint that is still `current` disappears with it. Reproduced on the validation binary. The validation had already shown the empty packet, without an authored claim on that task.
- Every successful shell command stays an active task. That is the frozen rule. It is why the placed packet is large.
- `context_now` text does not select a task. `contextd intent` while a failure is focal writes the intent onto that failure. Both match the frozen rule. There is still no focus switch.
- The six `file_system` rows missing from the validation database are not a confirmed persistence defect. A replay of the same 31 events through the same binary stored all six, enriched them, and did not delete them.

Not Component 1: gold-atom recall of 0, stdout capture, and non-interactive shells that never emit events. Those are missing observations, not a wrong fold of the observations that exist.

No P0 data-loss defect was established. The highest confirmed continuation bugs are the closed-task failure and the empty render of claims that are still in force.

---

## 2. Frozen and validated behavior

Do not treat the following as open defects. They were checked against a live daemon and, where noted, against the agent harness. Evidence: `docs/component-1-validation.md`, `eval/component0/runs/component1-validation/report.json`, `eval/component0/runs/component1-agents/report.md`.

| Contract | Result | Where it is implemented |
|---|---|---|
| One task per normalized shell command (`cwd` + collapsed command line) | Held | `crates/memory/src/project.rs` `shell_subject`, `fold_shell` |
| Latest exit is the reality for that command. Non-zero stays current and adds `Re-run and inspect:` | Held | `fold_shell`, `ensure_projector_frontier` |
| Exit 0 of that same command supersedes that reality and the projector next action | Held in the claims table | `exit_zero_supersedes` unit test; `supersede` in `report.json` |
| Oldest active task that still has a non-zero reality is focal | Held | `focal_task`; `multi_focus.two_failures` |
| A git commit does not become a task and does not outrank the failure | Held | `fold` ignores `EventSource::Git`; agent arm 5/5 vs old briefing 0/5 |
| `context_now` query text does not move focus | Held | `multi_focus` / `query_text_same_focal: true`. Briefing JSON before the query and the query reply are identical |
| Authored constraint, decision, ruled-out, and agent reality survive restart | Held | `authored` vs `rebuilt` in `report.json`. The script flag `authored_matches_rebuilt: false` is only `session_id` |
| Agent continuation | 5/5 solved, median 5 turns to the frontier, mean tokens 195,493 vs `none` 195,765 | `eval/component0/runs/component1-agents/report.md` |

`focal_task` (`crates/memory/src/project.rs`) sorts active tasks that have a current non-zero reality by `created_at_ms`, then task id. If none exist, it selects the active task whose current user intent has the newest `updated_at_ms`. Unit tests cover the oldest of two failures, an exact command key, exit 0 supersession, a commit that opens no task, and authored claims across two rebuilds.

The agent packet was the observation-only view. Authored claims were not placed. Required-atom recall 0 is the expected score for that packet.

---

## 3. Confirmed unresolved defects

### C1-01. A closed task does not reopen when the same command fails again

**Observed.** `claim done` on `python -m pytest -q tests/test_export.py` marks that task `done`. Sending the same command with exit 1 again inserts a new `current` reality (`exited 1`) and a new projector next action on that same task. `context_now` returns no focal task. The failure is in SQLite and not in the packet.

**Expected.** A new non-zero exit is an active failure. The task should be active again, and the oldest such failure should be focal under the frozen rule.

**Code.** `Projection::open_task` updates `updated_at_ms` and returns when the id already exists. It does not change `status`. `ClaimOp::CloseTask` is the only transition to `done`. `focal_task` and `other_tasks` both require `TaskStatus::Active`, so a `current` failing reality on a done task is invisible.

```264:279:crates/core/src/claim.rs
    pub fn open_task(&mut self, id: impl Into<String>, event_id: impl Into<String>, at_ms: u64) {
        let id = id.into();
        if let Some(task) = self.task_mut(&id) {
            task.updated_at_ms = at_ms;
            return;
        }
        let event_id = event_id.into();
        self.tasks.push(Task {
            id,
            status: TaskStatus::Active,
            opened_by_event_id: event_id,
            closed_by_event_id: None,
            created_at_ms: at_ms,
            updated_at_ms: at_ms,
        });
    }
```

```40:45:crates/memory/src/project.rs
    let mut failing: Vec<&contextd_core::claim::Task> = projection
        .tasks
        .iter()
        .filter(|task| task.status == TaskStatus::Active)
        .filter(|task| projection.has_failing_reality(&task.id))
        .collect();
```

**Evidence.** Isolated daemon, validation binary, 30 October 2026. Sequence: export pytest exit 1, `contextd claim done`, the same pytest exit 1. Task status stayed `done` with `closed_by_event_id` set. Claims for that command: the first reality `superseded`, the second reality `current` with outcome 1. Snapshot `focal_task` was null. Not part of the validation script. No unit test covers close-then-fail.

**Reproduction.**

1. Ingest a shell event for command C, exit 1.
2. `contextd claim done` while that task is focal.
3. Ingest command C again, exit 1.
4. `context_now` is empty. `claims` has a current non-zero reality. `tasks.status` is `done`.

**Impact.** A successor told the task is finished, then faced with the same failure, receives the trust notice and no next action. This is the same shape as the old selection miss: the failure is stored and the briefing does not show it. It is a different trigger. The original pytest-versus-commit case does not hit it.

**Root cause.** Task status is not a fold of the latest exit. Close is sticky. The new shell event refreshes claims and leaves status alone.

**Confidence.** High. Reproduced.

**Priority.** P1. Continuation. The row is not deleted, so this is not P0 corruption.

**Ownership.** Deterministic task projection.

**Experiment.** Add the sequence above to the live replay and to `crates/memory/src/project.rs` tests. Assert the task is `active` and focal when it is the only failure. Assert an older still-failing command stays focal if it is still active.

### C1-02. With no focal task, the renderer drops claims that are still current

**Observed.** Pool pytest exit 1, then `contextd claim constraint "do not change the public signature"`, then the same pytest exit 0. The claims table is consistent: exit 1 and the projector next action are `superseded`, exit 0 is `current`, the constraint is `current`, both tasks stay `active`. The snapshot is the trust notice only (`estimated_tokens` 27, `focal_task` null, no other tasks).

The validation `supersede` scenario is the same empty packet without the constraint (`report.json`, `estimated_tokens` 27 before and after restart). `after_closing_second` is also empty while the exit-0 commit task is still active. A later intent makes that commit task visible again as `other_count: 1`. So the commit task was never deleted. It was omitted because nothing was focal.

**Expected.** Supersession of the failure is correct and should stay. Current constraints, decisions, ruled-out lines, and agent findings on that task should still render. Other active tasks should still render as one line. The design note’s reading order (`docs/working-state-representation.md` section 6, items 9 and 10) includes other active tasks and done tasks even when describing one focal task. The implemented early return omits all of them.

**Code.**

```137:146:crates/broker/src/snapshot.rs
    let Some(focal) = focal_task(projection) else {
        return ContextSnapshot {
            session_id,
            notice: TRUST_NOTICE.to_string(),
            focal_task: None,
            other_active_tasks: Vec::new(),
            truncated: false,
            estimated_tokens: estimate_tokens(TRUST_NOTICE),
        };
    };
```

`focal_task` is non-empty only for a failing reality or a user intent. A current constraint, decision, ruled-out line, agent reality, or agent next action does not keep a task focal. Exit 0 does not mark the task done. It only removes the failing reality, which removes the only reason the task was focal.

`focal_view` also hides realities whose outcome is 0, including on a task that is focal for some other reason. A passing command is stored and not shown as reality.

**Evidence.** Validation `supersede.live` / `supersede.rebuilt` and `multi_focus.after_closing_second`. Constraint case reproduced on the validation binary the same day: constraint row `current` after the green re-run, packet empty. Unit test `empty_packet_when_there_is_no_focal_task` locks the empty packet for a default projection. It does not cover “current constraint, no failing reality.”

**Reproduction.** Ingest command C exit 1, `contextd claim constraint "..."`, ingest command C exit 0, call `context_now`. Compare the snapshot to `SELECT status, kind, text FROM claims`.

**Impact.** The successor after a green re-run loses the constraint and any finding that was authored on that task. Those rows are still in force. A later agent can violate the constraint with no packet to cite. Other successful commands, including a real second task, vanish from the same packet and reappear only after a new intent or a new failure.

**Root cause.** Rendering is defined as “render the focal task, else render nothing.” Emptiness is not a failed supersession. The claims transition is the one the contract asked for.

**Confidence.** High for the empty packet and for the hidden constraint. High that this is a renderer rule rather than a lost write.

**Priority.** P1. Continuation and constraint retention. Not P0: the log and the claims table keep the rows, and restart rebuilds the same empty packet.

**Ownership.** Briefing rendering, together with the focal-task predicate. Do not change which failing command is focal.

**Experiment.** The sequence above, plus the validation supersede scenario with one constraint and one decision authored before the exit 0. Assert those two texts are in the packet, the exit-1 reality is not current, and the exit-0 reality is not rendered as a failure. Repeat after a daemon restart.

---

## 4. Design limitations and missing capabilities

These match the code and the validation. They are not accidental misses of the frozen rule. Several are the cost of that rule.

### C1-03. Every successful command is an active task

**Observed.** The 31-event replay produced 24 shell tasks, all `active`. One is the failing pytest. The other 23 are exit 0 (`sed`, `grep`, `git apply`, `git diff`, passing pytests, the commit command, two flattened Python probes). The placed packet kept 20 of them.

**Expected under the frozen contract.** One task per command, and exit 0 does not close it. Only `claim done` closes a task. This is intentional (`project.rs` module comment: file, git, and proc events never become tasks; shell events do).

**Why it misleads.** `git apply` and a passing `tests/test_models.py` read as open work. They are finished commands. A passing test does not complete the task that ran it, and it does not complete any other task. `grep_error_is_not_a_failure` confirms a command whose text contains `error` is not a failure when exit code is 0. Outcome comes from the exit code only.

**Impact.** The agent arm still solved 5/5. The noise did not retarget those five trials. It did spend the render budget (C1-06, C1-07). A successor can also treat a green command as the thing still in progress when the real failure has been superseded and the packet has gone empty (C1-02).

**Priority.** P2 as a product limit of the frozen rule. Changing “one task per command” or “oldest failure is focal” is a contract change, not a bugfix. The continuation bug to fix first is C1-02 (stop listing them as if they were the whole briefing, and stop hiding real claims when the failure clears).

**Ownership.** Deterministic task projection for identity. Briefing rendering for how many exit-0 lines are printed.

**Experiment.** Do not change the focal pytest. Measure a packet variant that omits exit-0 other-task lines, on the same five seeds, and compare turns, tokens, and solved rate with `contextd-new`.

### C1-04. Command identity is the collapsed command line plus cwd

**Observed rule.** `shell_subject` is `cwd.trim()` plus a newline plus the command with whitespace collapsed. The task id is `t_` plus 32 hex characters of BLAKE3 of that subject. The same command in the same directory is one task across events, restarts, and sessions. A later event supersedes the earlier reality.

**What that gets right.** Retries of the exact command collapse. `command_key_is_exact` keeps `tests/test_pool_counts.py` failing when a different pytest exits 0. Order of arrival does not matter on rebuild: `get_fold_events` sorts by `(timestamp_ms, id)`.

**What it does not represent.** An engineering task. These stay separate tasks, and a green run of one does not finish another:

- the same test with a different flag (`-q`, `-p no:cacheprovider`, an extra file)
- the same command in a different `cwd`
- `git add && git commit` (one shell string) versus the git hook’s commit event (not a task at all)

Whitespace-only differences collapse (`pytest  -q` and `pytest -q` are one subject). Concurrent runs of one subject become one task. The later `(timestamp_ms, id)` wins, which is not “the run that finished last” when timestamps tie. ULID order is a tie-break, not a concurrency model.

**A successful test does not mean the task is done** in all of these cases:

| Situation | Task status | Briefing |
|---|---|---|
| Same command, exit 0 | Stays `active`. Failure reality superseded | Empty, if that was the only failure and there is no user intent (C1-02) |
| Same test, different flags, exit 0 | Old task still failing and focal. New task active and successful | Old failure remains the next action |
| A different test passes | Unrelated | Focal failure unchanged |
| `claim done`, then the same command fails | Stays `done` (C1-01) | Empty |

**Priority.** P2 for flag/cwd splits that leave a stale failure focal after the user has already rerun the test. P3 for whitespace collapse and timestamp ties. Not a reason to reopen pytest-versus-commit selection.

**Ownership.** Deterministic task projection.

**Experiment.** Fail `pytest tests/test_pool_counts.py`, then pass `pytest -q tests/test_pool_counts.py`. The first task should stay focal. Record whether that is acceptable before any normalization change. A normalization change is a contract change.

### C1-05. No focus switch, and intent attaches to the current failure

**Observed.** Two failures: the pool pytest stays focal. `contextd intent "fix the export tests"` does not move focus (`focal_unchanged: true`). The intent text is stored on the pool task. `context_now` with text `models: fix default port check in host header` returns the same snapshot as an empty query, including `session_id`.

**Code.** Two independent drops:

- `answer_query` matches `ContextRequest::Now { text: _ }` and calls `SnapshotRequest::new(store, "")`. `build` never reads `request.query`.
- `record_user_intent` sets `task_id` from `focal_task`. If a failure exists, the new intent supersedes the intent slot on that task. It does not open a task for the words the user typed.

MCP `context_now` accepts `text` and forwards it. The daemon discards it. `crates/mcp/src/server.rs` `context_now` and `bin/contextd/src/main.rs` `answer_query`.

There is no command that takes a task id. `claim done` is the only focus change, and it closes the oldest failure so the next oldest becomes focal. That part was validated (`after_closing_oldest` moves to the export pytest).

**Expected.** The frozen rule says the oldest failure wins and that query text does not retarget. The validation marked the missing focus command as a capability gap, not a broken rule.

**Impact.** A user who names a second piece of work while something is failing writes that sentence onto the failure. The second failure stays a one-line other task. After C1-06 trims other tasks, that line can disappear.

**Priority.** P1 as a missing capability for multi-task sessions. Do not implement it by letting query text override the oldest failure until that contract is explicitly revised. The validated behavior is that the commit subject as query text does nothing.

**Ownership.** Focus selection and the `context_now` request path.

**Experiment.** Two failing commands. Issue an intent that names only the newer command. Record which task holds the intent and which task is focal. That is the regression fixture for any future focus command. A new command should have to pass this fixture and the existing “query text does not move focus” check.

### C1-06. Budget trim removes other tasks by id order

**Observed.** Internal estimate 1,396 against a 1,500 budget, `truncated: true`, 20 of 23 non-focal tasks kept. The three removed ids are the lexicographically last:

- `t_e2448788…` a long passing Python probe
- `t_f6ca8fa2…` `grep -rn "b\"host\"…"`
- `t_fae3f688…` the Session B commit command

`sed` lines with smaller ids stayed. The commit left the packet because its task id sorted last, not because commits are ranked below failures.

**Code.** `other_tasks` sorts by task id. `trim_to_budget` pops artifacts, then unknowns, then extra decisions, then `other_active_tasks` from the end. It does not prefer a failing summary over `sed … exited 0`. Focal reality, the single next action, constraints, and ruled-out lines are not popped. If the remainder is still over budget, the function returns the oversized packet.

**Impact.** In this episode the dropped commit was a distractor, so the accident helped. A second failing command whose id sorts late can be dropped while exit-0 lines remain. That case was not in the measured 31-event packet (only one failure, and it was focal). The mechanism is the same one that dropped the commit.

**Priority.** P2. The focal pytest is protected. Non-focal failures and any future “other task” signal are not.

**Ownership.** Briefing rendering.

**Experiment.** One old failure plus one newer failure plus enough short exit-0 commands to exceed 1,500 estimated tokens. Assert the newer failure’s line survives a `git diff exited 0` line. Use task ids that sort the failure last, which is the case that fails today.

### C1-07. The placed packet is 1,775 tokens because of exit-0 lines and pretty printing

**Observed.**

| Form | Tokens |
|---|---|
| Daemon `estimated_tokens` (compact JSON, 3 characters per token) | 1,396 |
| o200k on a compact JSON encoding of the same value | 1,443 |
| o200k on the placed file `packets/contextd_new.txt` | 1,775 |

The placed file is byte-identical to `replay-31/briefing-before-restart.json`, which the replay script wrote with `json.dumps(..., indent=2)`. The daemon’s budget counts `serde_json::to_string`, which is compact. Pretty printing is about 332 of the 1,775 tokens. The estimator is mildly low on compact JSON (1,396 vs 1,443), not low by 379.

Of the pretty packet, the other-task array is about 1,443 tokens and the focal task about 255. Twenty task ids alone are about 425 tokens. The command summaries are about 585. Two flattened heredoc probes are about 239 tokens of summary text. Focal reality and next action repeat the same pytest command.

Truncation did not drop the focal failure, the next action, or (in the authored snapshot) the constraint. `constraint_survives_budget` trims unknowns first. The authored live snapshot is estimate 1,498, still truncated, `other_count` 17, with the constraint and both reality lines present.

**Impact.** The automatic packet is larger than the oracle (381) and the summary (170) mostly because exit-0 tasks are rendered in full, with ids. The agent then spends the same total tokens as `none`, because the packet does not contain the idle-count decision. That second fact is C1-12, not a trim bug.

**Priority.** P2.

**Ownership.** Briefing rendering for what is included. The harness for scoring a pretty dump as if it were the socket payload. Report both numbers in the next run.

**Experiment.** Score `serde_json::to_string` of the snapshot and the pretty file separately. Then score a packet that keeps the focal task and drops exit-0 other lines. Compare o200k to `estimated_tokens`.

### C1-08. Reality text is the exit code, not the diagnostic

**Observed.** Claim text is `{command} exited {code}`. The pool assertion (“count 3 diverged from 2”) is not in the 31 events and not in the claim. The agent re-ran the test. Evidence ids on the projector reality point at the shell event. That event’s payload is `command`, `exit_code`, and `cwd`.

**Upstream.** The installed hook sends only those three fields (`crates/sources/src/install.rs`, bash, zsh, and fish snippets). `summarize_shell` in `crates/pipeline/src/content.rs` will quote `stderr`, `output`, or `error` when those fields exist. They do not exist on this path. The projector does not read them even if they did: `fold_shell` formats the exit code and stores the event id.

**Downstream.** Nothing in the claim view recovers output that was never stored. The enrichment summary on the row (“ran `…` — exited 1”) is also unused by `context_now`.

**Priority.** P2. Explicitly out of the validated prototype (`docs/working-state-representation.md` section 6, stdout capture). The gap is real. It is not why the pytest was missing from the old briefing, and it is not why gold-atom recall is 0.

**Ownership.** Event capture for the missing bytes. Claim rendering only if a payload later grows an excerpt and the projector still ignores it.

**Experiment.** None inside the projector until a sensor actually stores an excerpt. Do not block Component 1 lifecycle fixes on hook capture.

### C1-09. Authored claims do not link to a source event

**Observed.** Projector realities have `evidence_ids: [shell event id]` and `asserted_by_event_id` set to that same event. The four authored rows in the validation database have `evidence_ids` `[]`. Their `asserted_by_event_id` is the claim event, which is in the log and is not pruned (`source != 'claim'` in `get_prunable_events`).

**Code.** `record_user_intent`, `record_user_claim`, and `record_agent_claim` set `evidence_ids: Vec::new()`. The agent protocol has `replaces` and no evidence field. `validate` checks role and kind. It does not require an evidence id.

Supersession is durable for slotted kinds: the new claim’s `supersedes` points at the previous id, and the old row stays `superseded`. Decisions are not slotted. Two current decisions on one task both remain (`decisions_do_not_merge`). There is no `hypothesis` status. `ClaimStatus` is `current` or `superseded`. Contradictions are not detected. A user cannot retract an observed projector reality (`validate_against_projection`); a new exit code is the only retraction. An agent can replace only its own claims.

Restart rebuilds these rows from the claim events. Provenance of the text is the claim event. Provenance of “which observation justified this finding” is empty unless a caller that does not exist today supplies ids.

**Priority.** P2. Inspectable in SQLite. Not shown in the default packet. Not a lost claim.

**Ownership.** Claim write API and claim lifecycle.

**Experiment.** Author a finding with an explicit shell event id, restart, and read `evidence_ids` back. Today the public CLI cannot express that id. The experiment is the specification for a field, not evidence that the current field is dropped after a write.

### C1-10. File and git events are evidence in the log and invisible in the view

**Observed.** When file events are stored, `fold` and `get_fold_events` skip them. They do not become tasks, artifact refs, or briefing lines. The git hook commit is the same. The shell `git commit` command is a normal exit-0 task. The validation briefing’s focal claim does not depend on the six file rows. `regression_new` still has `selected: true` and `marked_failed: true` on the database that lacked them.

The fuller design note said file events should become artifact refs on the active task. The prototype that was validated said the opposite, in the `project.rs` module comment. This audit follows the prototype.

**Priority.** P3 relative to the frozen prototype. Not a selection defect.

**Ownership.** Deterministic task projection, if artifact refs are added later. Out of the frozen selection contract.

---

## 5. Unverified risks requiring experiments

These were not shown to fail. The code path is identified so a later run can target it. Do not treat them as defects.

### C1-11. The six missing file events in the validation database

**What the database shows.** `replay-31/data/contextd/events.db` has 24 `shell`, 1 `git`, and 4 `claim` rows. The source log has 24 shell, 6 `file_system`, and 1 git. The six missing ids are exactly the `file_system` rows, timestamps all `1790923130153`, paths under `/work` for `httpcore/_models.py` and `tests/test_models.py`. Shell events immediately before and after those rows are present and enriched. The git row after them is present and enriched. `daemon-1.log` contains only startup lines. Tracing goes to stderr, the replay redirects stderr to a file, and the process is `SIGTERM`’d, so a later `error!` line can be lost in a block buffer. Absence of a log line is not evidence of a clean ingest.

**Path.**

| Stage | What happens to a socket `file_system` event |
|---|---|
| Source | `events.jsonl` row. Payload `action` + `path`. Historical score on that dump is 0.3 |
| Socket | `parse_inbound_line` accepts any source except `claim`. The replay sends `id`, `timestamp_ms`, `source`, `payload` |
| Noise filter | Applied only inside the filesystem watcher (`crates/sources/src/filesystem.rs`). Not on the socket |
| `should_drop` | Score 0.3 is above `NOISE_SCORE` (0.25), so the event is kept (`crates/pipeline/src/decision.rs`) |
| Insert | `insert_event`. A write error is logged and that event is skipped |
| Enrichment | `decide` at score 0.3 returns `Keep`, not `Drop`. `Drop` is what deletes a row |
| Projection | `get_fold_events` reads `source IN ('shell', 'claim')` only |
| Briefing | Unchanged by these rows |

**Reproducibility.** Not reproduced. The same binary, same 31-event payload, same isolated XDG layout, stored 6 `file_system` rows at score 0.3 with summaries (`deleted` / `created` / `edited`). Enrichment ran (`use_case` set) and did not delete them. One replay is not a flake hunt. It is enough to refuse a “confirmed persistence defect” label.

The ladder field `stored_events: 31` in `packet-scores.json` counts the source jsonl, not this SQLite file.

**Priority.** Unverified. P2 only if a second isolated replay drops them again. No projector change is justified.

**Experiment.** Replay `gen-1/events.jsonl` ten times on a fresh database with `RUST_LOG=debug` and line-buffered logs. Count `file_system` after the daemon exits. If every run stores six, close C1-11. If a run stores fewer, the missed ids and the `skipped` / `Failed to write` / `Missed` lines identify the stage.

### C1-12. Reliability cases with no failing test

| Case | What the code does | Coverage | Risk |
|---|---|---|---|
| Restart | `run_daemon` calls `rebuild` on startup. Live authored view matched except `session_id` | Validated | Low. Done |
| Rebuild after every shell exit | Full fold inside the writer lock | Code | A long log makes ingest slower. Not timed |
| Duplicate shell id | `INSERT` primary key fails, event skipped, previous row kept | No test | Low. Second delivery does not double the claim |
| Duplicate authored claim id | `replace_projection` inserts inside one transaction. A second row with the same id rolls the rebuild back. Later rebuilds fail the same way and the previous view stays | No test. CLI ids are ULIDs | Low unless a client replays a claim event with the same id |
| Out-of-order delivery | Fold input is `ORDER BY timestamp_ms, id` | No test that inserts in reverse | Low. Mechanism is the sort |
| Concurrent socket clients | One writer mutex. Readers use WAL | No test | Unverified. Busy timeout is 5 seconds (`crates/store/src/store.rs`). A failed insert is skipped |
| Partial write | Event insert autocommits, then `replace_projection` runs in its own transaction. Crash between them leaves the event stored and the view stale until the next rebuild or restart | No crash test | Low for the claims view, because restart rebuilds |
| Power loss | `synchronous=NORMAL` can drop the last commits | No test | Pre-existing store setting. Not observed |
| Task id collision | 128-bit BLAKE3 prefix | No test | Negligible beside subject aliasing (C1-04) |
| In-memory vs SQLite | `context_now` reads `load_projection`. `WorkingSet` is still updated and is not the briefing. Restart empties the deque and rebuilds the table. The live diff was `session_id` only | Validated for the packet | The deque can diverge and the packet still matches the table |
| Corrupt claim payload | `fold_claim` returns on JSON error and skips that event | No test | A bad claim line is ignored. The rest of the log still folds |
| Prune of evidence | Current claim `evidence_ids`, claim events, and openers of active tasks are excluded. Superseded evidence is not, unless it opened an active task. Stored shell commands score 0.6, above the 0.5 prune threshold | Unit test `pruner_skips_claim_events_and_current_evidence`. Pruner did not run during validation (hourly, 7-day retention) | Low for today’s shell scores. Worth re-checking if shell scores drop under 0.5 |

**Priority.** P3 until an experiment fails. The closed-task and empty-render issues are the ones already reproduced.

**Experiment.** Reverse-order insert of fail-then-pass, rebuild, assert one current exit-0 reality. Second, insert two claim events with the same `claim_id` and assert `rebuild` returns an error and the previous projection is unchanged. Third, two overlapping clients ingesting different commands, then one `context_now`, assert both tasks exist.

---

## 6. Component 2 dependencies and boundaries

Component 2 was not started. These are the boundaries for the work that remains.

| Boundary | What Component 1 already does | What is still outside it |
|---|---|---|
| 1. Event capture and persistence | Socket ingest stores shell and git events. A clean replay also stores file events. The fold reads shell and claim rows | Shell hook payload has no stdout or stderr (C1-08). Bash skips a second run of an unchanged history line (`install.rs`, `__contextd_prompt`), so a fail-then-pass of the same command can never supersede. Zsh and fish send every command. Non-interactive agent shells do not source the hook, which is why the eval injects events. None of that is a projector bug |
| 2. Deterministic task projection | Exit codes become realities. Exact command key. Oldest active failure is focal. Commits are not tasks | Closed tasks ignore a later failure (C1-01). Success stays active (C1-03). Flag and cwd splits (C1-04). File and git rows are not artifact refs (C1-10) |
| 3. Claim lifecycle | Authored kinds survive a rebuild. Slotted kinds supersede. Observed realities are retracted by a new exit, not by the user | No evidence ids on authored claims (C1-09). No contradiction pass. `hypothesis` is a confidence, not a status. `blocked` does not exist |
| 4. Focus selection | Oldest failing active task, else newest user intent | Query text ignored. Intent sticks to the current failure (C1-05). No select-by-id |
| 5. Briefing rendering | Focal task in the section-6 order. Exit-0 realities hidden. Budget trims unknowns and other tasks before constraints | Empty packet when nothing is focal (C1-02). Id-order trim (C1-06). Exit-0 lines and ids dominate tokens (C1-07). Done tasks are never listed |
| 6. Semantic notes that were never observed | The projector does not invent them. The write path can store them if something authors them | Gold atoms below. Authoring them is a capture or extraction problem. The agent arm did not receive the authored rows the daemon test wrote |

Gold atoms absent from the 31 shell events, and therefore from the automatic packet (`packet-scores.json`, `contextd-new`, recall 0, precision 0):

- `finding_idle_not_counted`
- `decision_idle_counts`
- `constraint_signatures`
- `constraint_no_new_deps`
- `ruled_out_mock_backend`
- `ruled_out_lock`
- `next_step_create_branch`

The oracle packet contains them (recall 1.0, 381 tokens, mean trial tokens 75,066). The automatic packet tells the agent to re-run the pytest. The agent then reads about as much of the pool as the no-context arm. Mean tokens after the frontier are about 179k versus about 132k for `none`. That gap is the unobserved decision and the create-branch note. It is not a failed fold of the pytest event.

`stale_redis_counter` and `unresolved_keepalive_comprehension` are also absent. They are not in the required set. `optional_host_header_fix` is the distractor the new packet correctly omits.

A Component 2 extractor that writes claim events would use the path the validation already checked: those events survive restart, and they show up in the focal task while a failure is focal. It would still be subject to C1-02 once the failure goes green, and to C1-05 if it attaches intent to the wrong task.

---

## 7. Prioritized investigation backlog

| ID | Title | Priority | Owner | Confidence |
|---|---|---|---|---|
| C1-01 | Closed task ignores a later failure of the same command | P1 | Projection | High, reproduced |
| C1-02 | No-focal render hides current claims and other active tasks | P1 | Rendering | High, reproduced |
| C1-05 | No focus switch; intent binds to the oldest failure; query text ignored | P1 capability, rule is frozen | Focus | High, validated |
| C1-04 | Different flags or cwd do not retire a failing command | P2 | Projection | High for the rule. Product cost not measured on agents |
| C1-06 | Other-task trim is lexicographic id order | P2 | Rendering | High for this packet. Second-failure loss not in the packet |
| C1-07 | Placed packet is 1,775 tokens; estimate is 1,396 compact | P2 | Rendering, harness accounting | High |
| C1-03 | Exit-0 commands stay active and are listed | P2, frozen rule | Projection and rendering | High, validated |
| C1-08 | No diagnostic text in the shell event or the claim | P2, deferred sensor | Capture | High |
| C1-09 | Authored claims store no supporting event id | P2 | Claim API | High |
| C1-11 | Six file events missing from the validation DB | Unverified | Ingest, only if it repeats | The miss is real in that file. The defect is not confirmed |
| C1-12 in section 5 | Duplicate claim id, busy timeout, crash between insert and rebuild, reverse-order insert | P3 until a test fails | Projection and store | Mechanism only |
| C1-10 | File and git events are not artifact refs | P3, excluded by the prototype | Projection | High |

There is no confirmed P0. The claims table and the event log matched the fold in every reproduced case. The failures above hide or mis-label state. They do not rewrite it.

Bash history deduplication is a P1 for live bash sessions and is not in this table as Component 1 work. It sits on boundary 1. If a pass is never ingested, C1’s supersession never runs. Track it with the shell hook, not with `focal_task`.

---

## 8. Recommended validation experiments

Run these before changing the focal-task rule. Keep the 31-event “pytest stays focal, commit does not” check as a gate on every build.

1. **Close, then fail the same command (C1-01).** Isolated daemon. Expectation to record, not to force a fix in this note: task status, whether the new reality is `current`, and whether `context_now` shows it. Today it does not show it.
2. **Green re-run with a constraint still current (C1-02).** Author a constraint and a decision on the failing command, then exit 0, then restart. Expectation: both texts still in the packet, exit 1 not current, packet not reduced to the trust notice. Today the texts survive in SQLite and the packet is the trust notice.
3. **Flag-mismatched pass (C1-04).** Fail the exact pool command from the episode, then pass it with `-q` removed or added. Record which task is focal. This is the case a successor actually runs.
4. **Trim with two failures (C1-06).** Oldest failure focal, newer failure’s task id sorting last, enough exit-0 lines to set `truncated`. Assert the newer failure is still in `other_active_tasks`.
5. **Token accounting (C1-07).** Report daemon `estimated_tokens`, o200k of the compact snapshot, and o200k of the file the harness places. Do not compare 1,396 to 1,775 as if they were the same string.
6. **File-event replay (C1-11).** Ten fresh databases, debug logs flushed on exit. Pass condition: 6 `file_system` rows each time. The fold gate must pass either way.
7. **Do not rerun the five-seed agent matrix** until 1–4 change a packet the agent sees. The selection result is already measured. A rendering change (C1-02 or dropping exit-0 lines) is the one that justifies a new `contextd-new` arm.

---

## 9. Explicitly excluded or already fixed

**Already fixed, and checked on the validation binary.**

- `context_now` is not a newest-10 event list. `removed_event_feed_keys_are_absent` and the live briefing have no `recent_activity`.
- The failing pool pytest is the focal reality and the next action, including when it is the oldest shell event.
- The Session B commit is not the focal task. Query text set to the commit subject does not change the snapshot.
- Exit 0 supersedes that command’s failure reality and the projector next action in the claims table.
- Authored constraint, decision, ruled-out, and agent reality survive a second process and a second rebuild.
- Closing the oldest failure moves focus to the next oldest failure.
- The ingest score still ignores exit codes (`crates/pipeline/src/heuristics.rs`). The claim briefing does not use that score. Fixing the scorer is not required for the selection result.

**Excluded.**

- Reopening whether this episode selects the pytest. It does. `regression_new` is `stored`, `selected`, and `marked_failed`.
- Treating required-atom recall 0 as a Component 1 defect. The atoms are not in the log. See section 6.
- Calling the validation file-event gap a confirmed persistence bug. Section 5, C1-11.
- Raising `RECENT_LIMIT`, retuning rank weights, or putting the event feed back beside the claim view. The design note rejected that, and the agent result agrees: the old feed is what made 5/5 agents stop on the commit.
- Stdout capture, embedding search, and the co-occurrence graph as the handoff path. They stay off `context_now`.
- Implementing Component 2 extraction in this tree. The write path for a human or an agent claim already exists and already survives rebuild.

**Pre-existing capture limits that still affect a live session, and are not regressions of the fold.**

- Bash does not emit a command whose history line did not change, so the passing rerun can be invisible and the failure stays current.
- Non-interactive shells do not load the hook. The Component 0 and Component 1 replays inject events for that reason.
- The hook still does not send stdout or stderr.
