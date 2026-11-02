# Reopen a closed task when the same command fails again

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> Do **not** commit unless the user explicitly asks.

**Goal:** After `claim done`, a later non-zero exit of that same command makes the task active again and visible to `focal_task`.

**Architecture:** The fold already writes a current failing reality on the closed task. `focal_task` only looks at `TaskStatus::Active`. Add one transition in `fold_shell`: a non-zero exit reopens a `Done` task. Leave `open_task`, `focal_task`, and exit 0 alone.

**Tech Stack:** Rust, `crates/core` (`Projection`), `crates/memory` (`fold` / `fold_shell`). Test with `cargo test -p memory close_then_fail`.

---

## What stays frozen

- Oldest active failing task stays focal. Do not edit `focal_task`.
- `claim done` is still the only way to close a task.
- Exit 0 still supersedes the failure and does not close or reopen a task.
- A later authored claim on a done task does not reopen it. `fold_claim` calls `open_task`, so the reopen must not live inside `open_task`.
- Task id, `created_at_ms`, and `opened_by_event_id` stay as they were. Reopen clears `closed_by_event_id` and sets `status` back to `Active`.

---

### Task 1: Reopen on a later non-zero exit

**Files:**
- Modify: `crates/core/src/claim.rs` (`Projection::reopen_task`, next to `close_task` around line 281)
- Modify: `crates/memory/src/project.rs` (`fold_shell` non-zero branch, around line 144; tests at the bottom of the module)

- [ ] **Step 1: Write the failing tests**

Add these three tests inside `mod tests` in `crates/memory/src/project.rs`, next to `exit_zero_supersedes`.

```rust
fn close(id: &str, ts: u64, task_id: &str) -> RawEvent {
    authored(
        id,
        ts,
        ClaimEvent {
            op: ClaimOp::CloseTask,
            claim_id: id.into(),
            task_id: task_id.to_string(),
            kind: None,
            subject: None,
            text: None,
            confidence: None,
            origin: ClaimOrigin::User,
            outcome: None,
            artifact_refs: Vec::new(),
            evidence_ids: Vec::new(),
            supersedes: None,
        },
    )
}

#[test]
fn close_then_fail_reopens_and_is_focal() {
    let task_id = task_id_for_subject(&shell_subject(CWD, PYTEST));
    let projection = fold(&[
        shell("fail", 1, PYTEST, 1),
        close("done", 2, &task_id),
        shell("fail-again", 3, PYTEST, 1),
    ]);
    let task = projection.task(&task_id).unwrap();
    assert_eq!(task.status, TaskStatus::Active);
    assert!(task.closed_by_event_id.is_none());
    assert_eq!(task.created_at_ms, 1);
    let reality = current(&projection, ClaimKind::Reality)
        .into_iter()
        .find(|claim| claim.task_id == task_id)
        .unwrap();
    assert_eq!(reality.outcome, Some(1));
    assert_eq!(reality.asserted_by_event_id, "fail-again");
    assert_eq!(focal_task(&projection).unwrap().id, task_id);
}

#[test]
fn close_then_fail_does_not_steal_focus_from_an_older_failure() {
    let newer = "python -m pytest tests/test_export.py";
    let newer_id = task_id_for_subject(&shell_subject(CWD, newer));
    let projection = fold(&[
        shell("old-fail", 1, PYTEST, 1),
        shell("new-fail", 2, newer, 1),
        close("done-new", 3, &newer_id),
        shell("new-fail-again", 4, newer, 1),
    ]);
    let older_id = task_id_for_subject(&shell_subject(CWD, PYTEST));
    assert_eq!(focal_task(&projection).unwrap().id, older_id);
    assert_eq!(
        projection.task(&newer_id).unwrap().status,
        TaskStatus::Active
    );
}

#[test]
fn close_then_pass_stays_done() {
    let task_id = task_id_for_subject(&shell_subject(CWD, PYTEST));
    let projection = fold(&[
        shell("fail", 1, PYTEST, 1),
        close("done", 2, &task_id),
        shell("pass", 3, PYTEST, 0),
    ]);
    assert_eq!(projection.task(&task_id).unwrap().status, TaskStatus::Done);
    assert!(focal_task(&projection).is_none());
}
```

- [ ] **Step 2: Run the tests and confirm the first two fail**

Run: `cargo test -p memory close_then_ -- --test-threads=1`

Expected: `close_then_fail_reopens_and_is_focal` fails because status is `Done` and `focal_task` is `None`. `close_then_fail_does_not_steal_focus_from_an_older_failure` fails because the newer task stays `Done`. `close_then_pass_stays_done` already passes.

- [ ] **Step 3: Add the reopen transition**

In `crates/core/src/claim.rs`, next to `close_task`:

```rust
/// A later non-zero exit of the same command makes a closed task active again.
pub fn reopen_task(&mut self, id: &str, at_ms: u64) {
    if let Some(task) = self.task_mut(id) {
        if task.status == TaskStatus::Done {
            task.status = TaskStatus::Active;
            task.closed_by_event_id = None;
            task.updated_at_ms = at_ms;
        }
    }
}
```

In `fold_shell`, call it only on the non-zero branch, after `open_task` has already run:

```rust
if exit_code != 0 {
    projection.reopen_task(&task_id, event.timestamp_ms);
    ensure_projector_frontier(
        projection,
        &task_id,
        &command,
        &event.id,
        event.timestamp_ms,
    );
} else {
    supersede_projector_frontier(projection, &task_id, event.timestamp_ms);
}
```

- [ ] **Step 4: Re-run**

Run: `cargo test -p memory close_then_ -- --test-threads=1`

Expected: the three new tests pass.

Run: `cargo test -p memory --lib`

Expected: the existing fold tests still pass, including `exit_zero_supersedes`, `oldest_failure_is_focal`, and `two_failures_oldest_wins`.
