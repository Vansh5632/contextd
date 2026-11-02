# Keep the load-bearing handoff

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> Do **not** commit unless the user explicitly asks. Each task ends when its test passes.

**Goal:** The briefing keeps rules, former-failure outcomes, and authored notes, and it leaves out commands that never failed.

**Architecture:** `focal_task` stays the oldest active failure, else the newest user intent. The renderer stops treating "no focal task" as "render nothing." Current constraints always render in a standing list. A current exit 0 renders as "now passes" only when that command previously failed and the task is still active. Decisions, ruled-out lines, unknowns, and non-projector findings render on unfinished work. `other_active_tasks` lists other still-failing commands only. The claims table and the fold do not change.

**Tech Stack:** Rust (`crates/broker` snapshot renderer), Python packet scorer (`eval/component0`). Tests: `cargo test -p broker snapshot::` and `python -m pytest` from `eval/component0`.

**Research:** `docs/research.md`.

---

## What stays frozen

- Do not edit `focal_task` in `crates/memory/src/project.rs`. Oldest active failure stays focal.
- Do not edit `fold_shell`. Exit 0 still supersedes the failure and the projector next action. Exit 0 still does not close the task.
- Do not reopen closed tasks here. That is `docs/superpowers/plans/2026-11-01-reopen-closed-task-on-failure.md`.
- Do not author the gold atoms. If they are not in the log, this renderer cannot show them.
- Do not run the five-seed agent matrix. The packet has to change first. The ablation in `docs/research.md` is a later measurement.
- A superseded constraint stays off the page. Lifting a rule is the existing supersede op.
- Ruled-out lines stay current negative claims. They are not passed checks.

## File map

- Modify: `crates/broker/src/snapshot.rs` — packet shape, render, trim, unit tests.
- Modify: `eval/component0/harness/packet_score.py` — score standing constraints, passed checks, and open work.
- Modify: `eval/component0/harness/contextd_ladder.py` — walk the new fields for evidence ids.
- Modify: `eval/component0/harness/regression.py` — flatten the new fields into briefing items.
- Modify: `eval/component0/tests/test_contextd_packet.py` — one scorer fixture.
- Modify: `docs/connecting-agents.md` — one sentence on what `context_now` returns.

## Packet shape after this plan

`ContextSnapshot` gains three fields. Empty vectors stay absent from JSON via `skip_serializing_if`.

- `constraints` — every current `ClaimKind::Constraint`, any task, including a done task. This is the only place constraints render. `FocalTaskView.constraints` stays on the struct and is always empty, so old fixtures that nest constraints under `focal_task` still parse.
- `passed_checks` — one line per active task whose current reality has outcome 0 and whose same subject has a superseded reality with a non-zero outcome. Text is `{command} now passes`.
- `open_work` — full `FocalTaskView` for each active task that is not focal and that has a current decision, ruled-out line, unknown, user or agent reality, or a next action whose origin is not the projector.

`other_active_tasks` becomes other active failures only.

---

### Task 1: Standing constraints survive a green re-run

**Files:**
- Modify: `crates/broker/src/snapshot.rs` (`ContextSnapshot`, `render`, new `standing_constraints`, tests)

- [ ] **Step 1: Write the failing tests**

Add these tests inside `mod tests` in `crates/broker/src/snapshot.rs`, after `empty_packet_when_there_is_no_focal_task`.

```rust
fn note(id: &str, task_id: &str, kind: ClaimKind, text: &str, at: u64) -> Claim {
    Claim {
        id: id.into(),
        task_id: task_id.into(),
        kind,
        subject: id.into(),
        status: ClaimStatus::Current,
        confidence: ClaimConfidence::Stated,
        origin: ClaimOrigin::User,
        text: text.into(),
        outcome: None,
        artifact_refs: Vec::new(),
        evidence_ids: Vec::new(),
        supersedes: None,
        asserted_by_event_id: id.into(),
        updated_at_ms: at,
    }
}

fn reality(
    id: &str,
    task_id: &str,
    subject: &str,
    text: &str,
    outcome: i64,
    status: ClaimStatus,
    at: u64,
) -> Claim {
    Claim {
        id: id.into(),
        task_id: task_id.into(),
        kind: ClaimKind::Reality,
        subject: subject.into(),
        status,
        confidence: ClaimConfidence::Observed,
        origin: ClaimOrigin::Projector,
        text: text.into(),
        outcome: Some(outcome),
        artifact_refs: Vec::new(),
        evidence_ids: vec![id.into()],
        supersedes: None,
        asserted_by_event_id: id.into(),
        updated_at_ms: at,
    }
}

#[test]
fn green_rerun_keeps_the_constraint() {
    let mut projection = Projection::default();
    projection.tasks.push(task("t_pool", "fail", 1));
    projection.claims.push(reality(
        "c_fail",
        "t_pool",
        "repo\npytest",
        "pytest exited 1",
        1,
        ClaimStatus::Superseded,
        1,
    ));
    projection.claims.push(reality(
        "c_pass",
        "t_pool",
        "repo\npytest",
        "pytest exited 0",
        0,
        ClaimStatus::Current,
        3,
    ));
    projection.claims.push(note(
        "c_rule",
        "t_pool",
        ClaimKind::Constraint,
        "do not change the public signature",
        2,
    ));

    let snapshot = render(&projection, None, 1_500);
    assert!(snapshot.focal_task.is_none());
    assert_eq!(
        snapshot.constraints.len(),
        1,
        "a current constraint keeps the page open"
    );
    assert_eq!(
        snapshot.constraints[0].text,
        "do not change the public signature"
    );
    let json = serde_json::to_string(&snapshot).unwrap();
    assert!(!json.contains("exited 1"));
}

#[test]
fn superseded_constraint_stays_off_the_page() {
    let mut projection = Projection::default();
    projection.tasks.push(task("t_pool", "fail", 1));
    let mut lifted = note(
        "c_rule",
        "t_pool",
        ClaimKind::Constraint,
        "do not change the public signature",
        2,
    );
    lifted.status = ClaimStatus::Superseded;
    projection.claims.push(lifted);

    let snapshot = render(&projection, None, 1_500);
    assert!(snapshot.constraints.is_empty());
    assert!(snapshot.focal_task.is_none());
}

#[test]
fn constraint_on_a_done_task_stays() {
    let mut projection = Projection::default();
    let mut done = task("t_pool", "fail", 1);
    done.status = TaskStatus::Done;
    done.closed_by_event_id = Some("close".into());
    projection.tasks.push(done);
    projection.claims.push(note(
        "c_rule",
        "t_pool",
        ClaimKind::Constraint,
        "do not change the public signature",
        2,
    ));

    let snapshot = render(&projection, None, 1_500);
    assert_eq!(snapshot.constraints.len(), 1);
    assert!(snapshot.focal_task.is_none());
}
```

In `constraint_survives_budget`, replace the constraint assertion:

```rust
assert_eq!(
    snapshot.constraints[0].text,
    "do not change the public signature"
);
assert!(snapshot.focal_task.as_ref().unwrap().constraints.is_empty());
```

Leave the reality, next-action, truncated, and unknowns assertions as they are.

- [ ] **Step 2: Run the tests and confirm the new ones fail**

Run: `cargo test -p broker snapshot::tests::green_rerun_keeps_the_constraint snapshot::tests::superseded_constraint_stays_off_the_page snapshot::tests::constraint_on_a_done_task_stays snapshot::tests::constraint_survives_budget -- --test-threads=1`

Expected: compile failure. `ContextSnapshot` has no field `constraints`.

- [ ] **Step 3: Render every current constraint**

Add the field to `ContextSnapshot` after `notice`:

```rust
#[serde(default, skip_serializing_if = "Vec::is_empty")]
pub constraints: Vec<RenderedClaim>,
```

Change `focal_view` so the constraints slot is always empty:

```rust
constraints: Vec::new(),
```

Delete the `constraints: pick(ClaimKind::Constraint),` line. Leave `pick` in place. The other slots still use it.

Add this function above `focal_view`:

```rust
fn standing_constraints(projection: &Projection) -> Vec<RenderedClaim> {
    let mut claims: Vec<&Claim> = projection
        .claims
        .iter()
        .filter(|claim| {
            claim.status == ClaimStatus::Current && claim.kind == ClaimKind::Constraint
        })
        .collect();
    claims.sort_by(|a, b| {
        a.updated_at_ms
            .cmp(&b.updated_at_ms)
            .then_with(|| a.id.cmp(&b.id))
    });
    claims.into_iter().map(render_claim).collect()
}
```

The early return still fires when `focal_task` is `None`, so `green_rerun_keeps_the_constraint` stays red until the return itself changes. Replace `render` with:

```rust
pub fn render(
    projection: &Projection,
    session_id: Option<String>,
    token_budget: usize,
) -> ContextSnapshot {
    let focal = focal_task(projection);
    let mut snapshot = ContextSnapshot {
        session_id,
        notice: TRUST_NOTICE.to_string(),
        constraints: standing_constraints(projection),
        focal_task: focal.map(|task| focal_view(projection, task)),
        other_active_tasks: focal
            .map(|task| other_tasks(projection, &task.id))
            .unwrap_or_default(),
        truncated: false,
        estimated_tokens: 0,
    };
    if snapshot.constraints.is_empty() && snapshot.focal_task.is_none() {
        snapshot.other_active_tasks.clear();
        snapshot.estimated_tokens = estimate_tokens(TRUST_NOTICE);
        return snapshot;
    }
    trim_to_budget(&mut snapshot, token_budget);
    snapshot
}
```

The `constraints.is_empty() && focal_task.is_none()` guard preserves `empty_packet_when_there_is_no_focal_task`. Later tasks widen the guard. They must not put this early clear back in front of passed checks or open work.

- [ ] **Step 4: Run the snapshot tests**

Run: `cargo test -p broker snapshot::`

Expected: PASS, including `empty_packet_when_there_is_no_focal_task`, `green_rerun_keeps_the_constraint`, `constraint_survives_budget`, and `fold_fixture_does_not_surface_the_commit`.

---

### Task 2: A former failure renders as "now passes"

**Files:**
- Modify: `crates/broker/src/snapshot.rs`

- [ ] **Step 1: Write the failing tests**

Add inside `mod tests`:

```rust
#[test]
fn former_failure_renders_as_now_passes() {
    let mut projection = Projection::default();
    projection.tasks.push(task("t_pool", "fail", 1));
    projection.claims.push(reality(
        "c_fail",
        "t_pool",
        "repo\npytest",
        "python -m pytest tests/test_pool_counts.py exited 1",
        1,
        ClaimStatus::Superseded,
        1,
    ));
    projection.claims.push(reality(
        "c_pass",
        "t_pool",
        "repo\npytest",
        "python -m pytest tests/test_pool_counts.py exited 0",
        0,
        ClaimStatus::Current,
        3,
    ));

    let snapshot = render(&projection, None, 1_500);
    assert!(snapshot.focal_task.is_none());
    assert_eq!(snapshot.passed_checks.len(), 1);
    assert_eq!(
        snapshot.passed_checks[0].text,
        "python -m pytest tests/test_pool_counts.py now passes"
    );
    assert_eq!(
        snapshot.passed_checks[0].evidence_ids,
        vec!["c_pass".to_string()]
    );
    let json = serde_json::to_string(&snapshot).unwrap();
    assert!(!json.contains("exited 1"));
    assert!(!json.contains("Re-run and inspect"));
}

#[test]
fn never_failed_command_has_no_pass_line() {
    let mut projection = Projection::default();
    projection.tasks.push(task("t_sed", "sed", 1));
    projection.claims.push(reality(
        "c_sed",
        "t_sed",
        "repo\nsed",
        "sed -n 1p Cargo.toml exited 0",
        0,
        ClaimStatus::Current,
        1,
    ));

    let snapshot = render(&projection, None, 1_500);
    assert!(snapshot.passed_checks.is_empty());
    assert!(snapshot.focal_task.is_none());
    assert!(snapshot.other_active_tasks.is_empty());
}

#[test]
fn pass_line_leaves_when_the_task_is_done() {
    let mut projection = Projection::default();
    let mut done = task("t_pool", "fail", 1);
    done.status = TaskStatus::Done;
    projection.tasks.push(done);
    projection.claims.push(reality(
        "c_fail",
        "t_pool",
        "repo\npytest",
        "pytest exited 1",
        1,
        ClaimStatus::Superseded,
        1,
    ));
    projection.claims.push(reality(
        "c_pass",
        "t_pool",
        "repo\npytest",
        "pytest exited 0",
        0,
        ClaimStatus::Current,
        3,
    ));

    let snapshot = render(&projection, None, 1_500);
    assert!(snapshot.passed_checks.is_empty());
}
```

- [ ] **Step 2: Run the new tests and confirm they fail**

Run: `cargo test -p broker snapshot::tests::former_failure_renders_as_now_passes snapshot::tests::never_failed_command_has_no_pass_line snapshot::tests::pass_line_leaves_when_the_task_is_done -- --test-threads=1`

Expected: compile failure. `ContextSnapshot` has no field `passed_checks`.

- [ ] **Step 3: Add the pass line**

Add the type next to `OtherTaskLine`:

```rust
#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct PassedCheck {
    pub task_id: String,
    pub text: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub evidence_ids: Vec<String>,
}
```

Add the field to `ContextSnapshot` after `constraints`:

```rust
#[serde(default, skip_serializing_if = "Vec::is_empty")]
pub passed_checks: Vec<PassedCheck>,
```

Set `passed_checks: passed_checks(projection)` in the `render` literal. There is one literal after Task 1.

Replace the empty-page guard with:

```rust
if snapshot.constraints.is_empty()
    && snapshot.focal_task.is_none()
    && snapshot.passed_checks.is_empty()
{
    snapshot.other_active_tasks.clear();
    snapshot.estimated_tokens = estimate_tokens(TRUST_NOTICE);
    return snapshot;
}
```

Add above `standing_constraints`:

```rust
fn passed_checks(projection: &Projection) -> Vec<PassedCheck> {
    let mut checks: Vec<PassedCheck> = projection
        .claims
        .iter()
        .filter(|claim| {
            claim.status == ClaimStatus::Current
                && claim.kind == ClaimKind::Reality
                && claim.outcome == Some(0)
                && previously_failed(projection, claim)
                && projection
                    .task(&claim.task_id)
                    .is_some_and(|task| task.status == TaskStatus::Active)
        })
        .map(|claim| PassedCheck {
            task_id: claim.task_id.clone(),
            text: pass_text(&claim.text),
            evidence_ids: claim.evidence_ids.clone(),
        })
        .collect();
    checks.sort_by(|a, b| a.task_id.cmp(&b.task_id).then_with(|| a.text.cmp(&b.text)));
    checks
}

fn previously_failed(projection: &Projection, current: &Claim) -> bool {
    projection.claims.iter().any(|claim| {
        claim.id != current.id
            && claim.task_id == current.task_id
            && claim.kind == ClaimKind::Reality
            && claim.subject == current.subject
            && claim.status == ClaimStatus::Superseded
            && claim.outcome.is_some_and(|code| code != 0)
    })
}

fn pass_text(reality_text: &str) -> String {
    let command = reality_text
        .strip_suffix(" exited 0")
        .unwrap_or(reality_text);
    format!("{command} now passes")
}
```

`focal_view` already hides outcome 0, so the passing reality does not also appear as a failure.

- [ ] **Step 4: Run the snapshot tests**

Run: `cargo test -p broker snapshot::`

Expected: PASS. `never_failed_command_has_no_pass_line` passes because the empty-page guard clears `other_active_tasks` when nothing else is on the page. Task 3 stops that command from being listed when a focal failure also exists.

---

### Task 3: Other tasks are other failures

**Files:**
- Modify: `crates/broker/src/snapshot.rs` (`other_tasks`)

- [ ] **Step 1: Write the failing test**

```rust
#[test]
fn other_tasks_keep_a_second_failure_and_drop_sed() {
    let mut projection = Projection::default();
    projection.tasks.push(task("t_a_pool", "fail", 1));
    projection.claims.push(failing_reality("t_a_pool"));
    projection.claims.push(next_action("t_a_pool"));

    projection.tasks.push(task("t_z_export", "export-fail", 2));
    projection.claims.push(Claim {
        id: "c_export".into(),
        task_id: "t_z_export".into(),
        kind: ClaimKind::Reality,
        subject: "repo\nexport".into(),
        status: ClaimStatus::Current,
        confidence: ClaimConfidence::Observed,
        origin: ClaimOrigin::Projector,
        text: "python -m pytest tests/test_export.py exited 1".into(),
        outcome: Some(1),
        artifact_refs: Vec::new(),
        evidence_ids: vec!["export-fail".into()],
        supersedes: None,
        asserted_by_event_id: "export-fail".into(),
        updated_at_ms: 2,
    });

    projection.tasks.push(task("t_m_sed", "sed", 3));
    projection.claims.push(reality(
        "c_sed",
        "t_m_sed",
        "repo\nsed",
        "sed -n 1p Cargo.toml exited 0",
        0,
        ClaimStatus::Current,
        3,
    ));

    let snapshot = render(&projection, None, 1_500);
    let focal = snapshot.focal_task.expect("oldest failure stays focal");
    assert_eq!(focal.id, "t_a_pool");
    assert_eq!(snapshot.other_active_tasks.len(), 1);
    assert_eq!(snapshot.other_active_tasks[0].id, "t_z_export");
    let json = serde_json::to_string(&snapshot).unwrap();
    assert!(!json.contains("sed -n"));
}
```

`failing_reality` and `next_action` hardcode `task_id` from their argument and the pool command text. `t_a_pool` sorts before `t_z_export` by `created_at_ms` (1 then 2), so the pool task is focal under the frozen rule.

- [ ] **Step 2: Run the test and confirm it fails**

Run: `cargo test -p broker snapshot::tests::other_tasks_keep_a_second_failure_and_drop_sed -- --test-threads=1`

Expected: FAIL. `other_active_tasks` contains `t_m_sed` as well as `t_z_export`. The assertion on `len() == 1` fails.

- [ ] **Step 3: Filter `other_tasks`**

Replace the filter in `other_tasks`:

```rust
fn other_tasks(projection: &Projection, focal_id: &str) -> Vec<OtherTaskLine> {
    let mut lines: Vec<OtherTaskLine> = projection
        .tasks
        .iter()
        .filter(|task| task.status == TaskStatus::Active && task.id != focal_id)
        .filter(|task| projection.has_failing_reality(&task.id))
        .map(|task| OtherTaskLine {
            id: task.id.clone(),
            status: task.status,
            summary: task_summary(projection, task),
        })
        .collect();
    lines.sort_by(|a, b| a.id.cmp(&b.id));
    lines
}
```

- [ ] **Step 4: Run the snapshot tests**

Run: `cargo test -p broker snapshot::`

Expected: PASS.

---

### Task 4: Authored notes stay on unfinished work

**Files:**
- Modify: `crates/broker/src/snapshot.rs`

A decision on the green task is not a constraint and not a pass line. It renders in `open_work` until the task is done. It does not become the focal task.

- [ ] **Step 1: Write the failing tests**

```rust
#[test]
fn decision_on_a_green_task_stays_visible() {
    let mut projection = Projection::default();
    projection.tasks.push(task("t_pool", "fail", 1));
    projection.claims.push(reality(
        "c_fail",
        "t_pool",
        "repo\npytest",
        "pytest exited 1",
        1,
        ClaimStatus::Superseded,
        1,
    ));
    projection.claims.push(reality(
        "c_pass",
        "t_pool",
        "repo\npytest",
        "pytest exited 0",
        0,
        ClaimStatus::Current,
        3,
    ));
    projection.claims.push(note(
        "c_decision",
        "t_pool",
        ClaimKind::Decision,
        "Idle connections should count toward the pool cap",
        2,
    ));
    projection.claims.push(note(
        "c_ruled",
        "t_pool",
        ClaimKind::RuledOut,
        "mocking the backend does not reproduce the cap",
        2,
    ));

    let snapshot = render(&projection, None, 1_500);
    assert!(snapshot.focal_task.is_none());
    assert_eq!(snapshot.open_work.len(), 1);
    assert_eq!(snapshot.open_work[0].id, "t_pool");
    assert_eq!(
        snapshot.open_work[0].decisions[0].text,
        "Idle connections should count toward the pool cap"
    );
    assert_eq!(
        snapshot.open_work[0].ruled_out[0].text,
        "mocking the backend does not reproduce the cap"
    );
    assert!(snapshot.open_work[0].reality.is_empty());
    assert_eq!(snapshot.passed_checks.len(), 1);
}

#[test]
fn decision_leaves_when_the_work_is_done() {
    let mut projection = Projection::default();
    let mut done = task("t_pool", "fail", 1);
    done.status = TaskStatus::Done;
    projection.tasks.push(done);
    projection.claims.push(note(
        "c_decision",
        "t_pool",
        ClaimKind::Decision,
        "Idle connections should count toward the pool cap",
        2,
    ));
    projection.claims.push(note(
        "c_rule",
        "t_pool",
        ClaimKind::Constraint,
        "do not change the public signature",
        2,
    ));

    let snapshot = render(&projection, None, 1_500);
    assert!(snapshot.open_work.is_empty());
    assert_eq!(snapshot.constraints.len(), 1);
}
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cargo test -p broker snapshot::tests::decision_on_a_green_task_stays_visible snapshot::tests::decision_leaves_when_the_work_is_done -- --test-threads=1`

Expected: compile failure. `ContextSnapshot` has no field `open_work`.

- [ ] **Step 3: Render open work**

Add the field to `ContextSnapshot` after `passed_checks`:

```rust
#[serde(default, skip_serializing_if = "Vec::is_empty")]
pub open_work: Vec<FocalTaskView>,
```

Set `open_work: open_work(projection, focal.map(|task| task.id.as_str()))` in the `render` literal.

Widen the empty-page guard so a decision alone keeps the page:

```rust
if snapshot.constraints.is_empty()
    && snapshot.focal_task.is_none()
    && snapshot.passed_checks.is_empty()
    && snapshot.open_work.is_empty()
{
    snapshot.other_active_tasks.clear();
    snapshot.estimated_tokens = estimate_tokens(TRUST_NOTICE);
    return snapshot;
}
```

Add:

```rust
fn open_work(projection: &Projection, focal_id: Option<&str>) -> Vec<FocalTaskView> {
    let mut tasks: Vec<&Task> = projection
        .tasks
        .iter()
        .filter(|task| task.status == TaskStatus::Active)
        .filter(|task| focal_id != Some(task.id.as_str()))
        .filter(|task| has_authored_continuation(projection, &task.id))
        .collect();
    tasks.sort_by(|a, b| {
        a.created_at_ms
            .cmp(&b.created_at_ms)
            .then_with(|| a.id.cmp(&b.id))
    });
    tasks
        .into_iter()
        .map(|task| focal_view(projection, task))
        .collect()
}

fn has_authored_continuation(projection: &Projection, task_id: &str) -> bool {
    projection.current_for_task(task_id).any(|claim| match claim.kind {
        ClaimKind::Decision | ClaimKind::RuledOut | ClaimKind::Unknown => true,
        ClaimKind::Intent => true,
        ClaimKind::NextAction => claim.origin != ClaimOrigin::Projector,
        ClaimKind::Reality => claim.origin != ClaimOrigin::Projector,
        ClaimKind::Constraint => false,
    })
}
```

`focal_view` hides outcome 0, so the passing reality stays out of `open_work.reality`. The pass line remains `passed_checks`. Constraints stay only in `snapshot.constraints`.

- [ ] **Step 4: Run the snapshot tests**

Run: `cargo test -p broker snapshot::`

Expected: PASS.

---

### Task 5: The budget trim does not drop a rule or a pass line

**Files:**
- Modify: `crates/broker/src/snapshot.rs` (`trim_to_budget`)

Trim order, first dropped first: artifacts, unknowns, extra decisions beyond one, the same three inside `open_work`, then `other_active_tasks`. Standing constraints, the focal failure, the single next action, ruled-out lines, the last decision, and `passed_checks` stay. If the remainder is still over budget, return the oversized packet. That matches the existing function's last return.

- [ ] **Step 1: Write the failing test**

```rust
#[test]
fn budget_keeps_the_rule_and_the_pass_line() {
    let mut projection = Projection::default();
    projection.tasks.push(task("t_pool", "fail", 1));
    projection.claims.push(reality(
        "c_fail",
        "t_pool",
        "repo\npytest",
        "pytest exited 1",
        1,
        ClaimStatus::Superseded,
        1,
    ));
    projection.claims.push(reality(
        "c_pass",
        "t_pool",
        "repo\npytest",
        "pytest exited 0",
        0,
        ClaimStatus::Current,
        2,
    ));
    projection.claims.push(note(
        "c_rule",
        "t_pool",
        ClaimKind::Constraint,
        "do not change the public signature",
        2,
    ));
    projection.claims.push(note(
        "c_decision",
        "t_pool",
        ClaimKind::Decision,
        "Idle connections should count toward the pool cap",
        2,
    ));
    for i in 0..20 {
        projection.claims.push(Claim {
            id: format!("c_unk{i}"),
            task_id: "t_pool".into(),
            kind: ClaimKind::Unknown,
            subject: format!("c_unk{i}"),
            status: ClaimStatus::Current,
            confidence: ClaimConfidence::Hypothesis,
            origin: ClaimOrigin::Agent,
            text: format!("unresolved question number {i} with extra padding words"),
            outcome: None,
            artifact_refs: Vec::new(),
            evidence_ids: Vec::new(),
            supersedes: None,
            asserted_by_event_id: format!("e{i}"),
            updated_at_ms: 3 + i,
        });
    }

    let snapshot = render(&projection, None, 80);
    assert!(snapshot.truncated);
    assert_eq!(
        snapshot.constraints[0].text,
        "do not change the public signature"
    );
    assert_eq!(snapshot.passed_checks.len(), 1);
    assert_eq!(snapshot.open_work[0].decisions.len(), 1);
    assert!(snapshot.open_work[0].unknowns.len() < 20);
}
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `cargo test -p broker snapshot::tests::budget_keeps_the_rule_and_the_pass_line -- --test-threads=1`

Expected: FAIL. Unknowns live on `open_work`, and `trim_to_budget` only pops unknowns on `focal_task`. With budget 80 the packet stays oversized and `unknowns.len()` stays 20, or the assertion `truncated` fails if the cost happens to fit. Either way the unknowns are not trimmed. If the test passes on the first run, lower the budget literal to `40` and run it again. The expected red result is `unknowns.len() < 20` failing because the count is 20.

- [ ] **Step 3: Trim open-work unknowns before other failures**

Replace `trim_to_budget` with:

```rust
fn trim_to_budget(snapshot: &mut ContextSnapshot, budget: usize) {
    loop {
        let cost = packet_cost(snapshot);
        if cost <= budget {
            snapshot.estimated_tokens = cost;
            return;
        }
        snapshot.truncated = true;
        if trim_task(snapshot.focal_task.as_mut()) {
            continue;
        }
        if trim_open_work(snapshot) {
            continue;
        }
        if !snapshot.other_active_tasks.is_empty() {
            snapshot.other_active_tasks.pop();
            continue;
        }
        snapshot.estimated_tokens = packet_cost(snapshot);
        return;
    }
}

fn trim_open_work(snapshot: &mut ContextSnapshot) -> bool {
    for work in snapshot.open_work.iter_mut().rev() {
        if trim_task(Some(work)) {
            return true;
        }
    }
    false
}

fn trim_task(task: Option<&mut FocalTaskView>) -> bool {
    let Some(task) = task else {
        return false;
    };
    if !task.artifacts.is_empty() {
        task.artifacts.pop();
        return true;
    }
    if !task.unknowns.is_empty() {
        task.unknowns.pop();
        return true;
    }
    if task.decisions.len() > 1 {
        task.decisions.pop();
        return true;
    }
    false
}
```

`trim_task` does not pop constraints, ruled-out lines, reality, intent, or the last decision. `passed_checks` and `snapshot.constraints` have no pop path. `trim_open_work` walks from the end and removes one trimmable item, then the loop recomputes the cost.

- [ ] **Step 4: Run the snapshot tests**

Run: `cargo test -p broker snapshot::`

Expected: PASS. `constraint_survives_budget` still trims focal unknowns. `budget_keeps_the_rule_and_the_pass_line` trims open-work unknowns and keeps the rule, the pass line, and the one decision.

---

### Task 6: The scorer can see the standing list

**Files:**
- Modify: `eval/component0/harness/packet_score.py` (`_claim_view_units`)
- Modify: `eval/component0/harness/contextd_ladder.py` (`briefing_ids`)
- Modify: `eval/component0/harness/regression.py` (`_briefing_items`)
- Modify: `eval/component0/tests/test_contextd_packet.py`
- Modify: `docs/connecting-agents.md` (the `context_now` row)

Old packets keep constraints nested under `focal_task`. New packets put them at the top level. The scorer reads both. A new packet does not also nest them, so a constraint is not counted twice.

- [ ] **Step 1: Write the failing test**

Add to `eval/component0/tests/test_contextd_packet.py`:

```python
def test_split_units_standing_fields() -> None:
    text = json.dumps(
        {
            "constraints": [{"text": "do not change the public signature"}],
            "passed_checks": [
                {"text": "pytest now passes", "evidence_ids": ["pass-1"]}
            ],
            "open_work": [
                {
                    "decisions": [
                        {"text": "Idle connections should count toward the pool cap"}
                    ],
                    "ruled_out": [
                        {"text": "mocking the backend does not reproduce the cap"}
                    ],
                }
            ],
            "other_active_tasks": [
                {"summary": "python -m pytest tests/test_export.py exited 1"}
            ],
        }
    )
    assert split_units(text, "contextd") == [
        "[constraint] do not change the public signature",
        "[passed] pytest now passes",
        "[decision] Idle connections should count toward the pool cap",
        "[ruled_out] mocking the backend does not reproduce the cap",
        "[other] python -m pytest tests/test_export.py exited 1",
    ]
```

- [ ] **Step 2: Run the test and confirm it fails**

Run from `eval/component0`:

`python -m pytest tests/test_contextd_packet.py::test_split_units_standing_fields -q`

Expected: FAIL. The units list is only the `[other]` line. Top-level constraints, passed checks, and open work are ignored.

- [ ] **Step 3: Read the new fields**

In `_claim_view_units`, after the focal-task next-action block and before `other_active_tasks`, add:

```python
    for item in _claim_list(data.get("constraints"), "constraints"):
        units.append(_claim_text(item, "constraint"))
    for item in _claim_list(data.get("passed_checks"), "passed_checks"):
        units.append(_claim_text(item, "passed"))
    open_work = data.get("open_work")
    if open_work is not None:
        if not isinstance(open_work, list):
            raise ValueError("contextd open_work must be a list")
        for work in open_work:
            if not isinstance(work, dict):
                raise ValueError("contextd open_work items must be objects")
            for key, label in (
                ("decisions", "decision"),
                ("ruled_out", "ruled_out"),
                ("unknowns", "unknown"),
                ("reality", "reality"),
            ):
                for item in _claim_list(work.get(key), f"open_work.{key}"):
                    units.append(_claim_text(item, label))
            intent = work.get("intent")
            if intent is not None:
                units.append(_claim_text(intent, "intent"))
            nxt = work.get("next_action")
            if nxt is not None:
                units.append(_claim_text(nxt, "next"))
```

Add the helper next to `_claim_text`:

```python
def _claim_list(value: Any, label: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"contextd {label} must be a list")
    return value
```

Use `_claim_list` for the existing focal-task lists too, replacing the inline `isinstance` check inside the focal loop. The error text becomes `contextd focal_task.constraints must be a list` if `label` is passed as `f"focal_task.{key}"`. Do that so there is one list check.

In `briefing_ids` in `contextd_ladder.py`, after the two existing `walk(...)` calls:

```python
    walk(data.get("constraints"))
    walk(data.get("passed_checks"))
    walk(data.get("open_work"))
```

In `_briefing_items` in `regression.py`, after the focal-task extend:

```python
    items.extend(_claim_items(data.get("constraints")))
    items.extend(_claim_items(data.get("passed_checks")))
    items.extend(_claim_items(data.get("open_work")))
```

`_claim_items` already recurses into dicts and lists and emits one row per evidence id. A passed check with `evidence_ids` becomes selectable. A constraint with no evidence id and no claim id does not. That matches authored constraints today.

In `docs/connecting-agents.md`, replace the `context_now` cell with:

`Before asking the user what they are working on. Returns standing constraints, the focal task when one exists, a short line for each check that used to fail and now passes, authored notes on other unfinished work, and one line per other failing command. Not a recency feed.`

- [ ] **Step 4: Run the scorer tests and the snapshot tests**

Run from `eval/component0`:

`python -m pytest tests/test_contextd_packet.py -q`

Expected: PASS, including `test_split_units_claim_view` and `test_split_units_standing_fields`.

Run from the repo root:

`cargo test -p broker snapshot::`

Expected: PASS.

---

## Plan self-check

- Standing constraints, including on a done task: Task 1.
- Lifted constraint absent: Task 1.
- Former failure shows "now passes" and does not show "exited 1" or "Re-run and inspect": Task 2.
- Pass line leaves when the task is done: Task 2.
- Never-failed command omitted: Tasks 2 and 3.
- Second failure kept, `sed` dropped, oldest failure still focal: Task 3.
- Decision and ruled-out stay until the work is done, and do not steal focus: Task 4.
- Budget cannot trim the rule, the pass line, or the last decision: Task 5.
- Scorer and regression walk the new fields: Task 6.
- `focal_task` and `fold_shell` are not in the file map.
- Gold atoms that were never events are not invented.
- The five-seed matrix is not a step.

## Execution handoff

Plan saved to `docs/superpowers/plans/2026-11-01-keep-load-bearing-handoff.md`. Research saved to `docs/research.md`.

Two ways to implement it:

1. **Subagent-driven** — a fresh worker per task, with review between tasks.
2. **Inline** — implement the tasks in this session, stopping after each task's tests pass.

Say which one you want. Nothing has been committed.
