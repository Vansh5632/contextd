"""Atomic-claim judgments.

Gold factuality is supplied by the case file. This module does not tokenize
like the production extractor and does not treat a regex hit as support.
"""

from __future__ import annotations

from typing import Any

ESTABLISHED = frozenset({"observed", "inferred", "stated"})

Fact = str  # SUPPORTED | CONTRADICTED | INSUFFICIENT_EVIDENCE


def rendered_claims(snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(snapshot, dict) or snapshot.get("error"):
        return []
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(slot: str, task_id: str | None, item: dict[str, Any]) -> None:
        if not isinstance(item, dict) or "text" not in item:
            return
        key = (str(item.get("id")), slot)
        if key in seen:
            return
        seen.add(key)
        rows.append(
            {
                "slot": slot,
                "task_id": task_id,
                "id": item.get("id"),
                "text": item.get("text") or "",
                "confidence": item.get("confidence"),
                "origin": item.get("origin"),
                "evidence_ids": list(item.get("evidence_ids") or []),
            }
        )

    def walk(task: dict[str, Any] | None) -> None:
        if not isinstance(task, dict):
            return
        task_id = task.get("id")
        for slot in ("reality", "decisions", "ruled_out", "unknowns", "constraints"):
            for item in task.get(slot) or []:
                add(slot, task_id, item)
        if isinstance(task.get("intent"), dict):
            add("intent", task_id, task["intent"])
        if isinstance(task.get("next_action"), dict):
            add("next_action", task_id, task["next_action"])

    walk(snapshot.get("focal_task"))
    for task in snapshot.get("open_work") or []:
        walk(task)
    for item in snapshot.get("constraints") or []:
        add("standing_constraint", None, item)
    return rows


def _same(left: str | None, right: str | None) -> bool:
    return (left or "").strip() == (right or "").strip()


def _matching(rows: list[dict[str, Any]], text: str) -> list[dict[str, Any]]:
    return [row for row in rows if _same(row.get("text"), text)]


def _store_current_reality(claims: list[dict[str, Any]], text: str) -> bool:
    return any(
        _same(row.get("text"), text)
        and row.get("status") == "current"
        and row.get("kind") == "reality"
        and row.get("confidence") in ESTABLISHED
        for row in claims
    )


def _presented(rendered: list[dict[str, Any]], text: str) -> bool:
    return any(
        _same(row.get("text"), text)
        and row.get("slot") == "reality"
        and row.get("confidence") in ESTABLISHED
        for row in rendered
    )


def _hypothesis(rendered: list[dict[str, Any]], claims: list[dict[str, Any]], text: str) -> bool:
    if any(
        _same(row.get("text"), text)
        and (row.get("slot") == "unknowns" or row.get("confidence") == "hypothesis")
        for row in rendered
    ):
        return True
    return any(
        _same(row.get("text"), text)
        and row.get("status") == "current"
        and (row.get("kind") == "unknown" or row.get("confidence") == "hypothesis")
        for row in claims
    )


def _checker(atom: dict[str, Any], checkpoint: dict[str, Any], text: str) -> str:
    for item in checkpoint.get("accepted") or []:
        if _same(item.get("text"), text):
            return "accepted"
    unit = None
    bind = atom.get("source_bind")
    if bind:
        unit = (checkpoint.get("binds") or {}).get(bind)
    for item in checkpoint.get("rejected") or []:
        if _same(item.get("text"), text) or (unit and item.get("claim_id") == unit):
            return "rejected"
    for item in checkpoint.get("skipped") or []:
        if unit and item.get("unit") in {unit, f"{unit}"}:
            return "skipped"
        if unit and item.get("claim_id") == unit:
            return "skipped"
    return "absent"


def _origins(rendered: list[dict[str, Any]], claims: list[dict[str, Any]], text: str) -> list[str]:
    found: list[str] = []
    for row in _matching(rendered, text) + _matching(claims, text):
        origin = row.get("origin")
        if origin and origin not in found:
            found.append(origin)
    return found


def _timestamp_reversed(claims_text: str, events: list[dict[str, Any]]) -> bool:
    asserts = []
    claim_ids: set[str] = set()
    for event in events:
        payload = event.get("payload") or {}
        if payload.get("op") == "assert" and _same(payload.get("text"), claims_text):
            asserts.append(event["timestamp_ms"])
            if payload.get("claim_id"):
                claim_ids.add(payload["claim_id"])
    if not asserts or not claim_ids:
        return False
    supers = [
        event["timestamp_ms"]
        for event in events
        if (event.get("payload") or {}).get("op") == "supersede"
        and (event.get("payload") or {}).get("supersedes") in claim_ids
    ]
    if not supers:
        return False
    return min(supers) < min(asserts)


def _duplicate_skip(atom: dict[str, Any], checkpoint: dict[str, Any]) -> bool:
    bind = atom.get("source_bind")
    unit = (checkpoint.get("binds") or {}).get(bind) if bind else None
    for item in checkpoint.get("skipped") or []:
        reason = str(item.get("reason") or "").lower()
        if "duplicate" not in reason:
            continue
        if unit is None or item.get("unit") == unit or item.get("claim_id") == unit:
            return True
    return False


def judge_atom(atom: dict[str, Any], checkpoint: dict[str, Any]) -> dict[str, Any]:
    text = atom["text"]
    rendered = checkpoint.get("rendered") or []
    claims = checkpoint.get("store_claims") or []
    events = checkpoint.get("store_events") or []
    binds = checkpoint.get("binds") or {}
    presented = _presented(rendered, text)
    stored_current = _store_current_reality(claims, text)
    established = presented or stored_current
    checker = _checker(atom, checkpoint, text)
    rendered_hit = _matching(rendered, text)
    store_hit = _matching(claims, text)
    flags: list[str] = []
    status = "pass"
    detail = "matches the contract"

    expect = atom["expect"]
    factuality = atom.get("factuality")

    def fail(flag: str, message: str) -> None:
        nonlocal status, detail
        status = "fail"
        detail = message
        if flag not in flags:
            flags.append(flag)

    def block(message: str) -> None:
        nonlocal status, detail
        status = "blocked"
        detail = message

    if expect == "presented_iff_supported":
        if factuality == "SUPPORTED":
            if not established:
                if checker in {"skipped", "rejected"}:
                    fail("not_presented", "a supported claim was rejected by the checker")
                elif checker == "accepted":
                    fail("not_presented", "a supported claim was accepted and then left unrendered")
                    flags.append("renderer_excluded")
                else:
                    fail("not_presented", "a supported claim is not current")
        elif factuality in {"CONTRADICTED", "INSUFFICIENT_EVIDENCE"}:
            if established:
                fail("presented_as_reality", "a claim the evidence does not support is current")
            elif checker == "accepted" and not presented:
                flags.append("renderer_excluded")
        else:
            block(f"unknown factuality {factuality}")
    elif expect == "must_be_hypothesis":
        if established:
            fail("presented_as_reality", "an uncertain claim is stored as an established reality")
        elif not _hypothesis(rendered, claims, text):
            fail("uncertainty_dropped", "the uncertain claim was not retained as a hypothesis")
    elif expect == "must_be_superseded":
        current = any(row.get("status") == "current" for row in store_hit)
        superseded = any(row.get("status") == "superseded" for row in store_hit)
        if _timestamp_reversed(text, events):
            flags.append("timestamp_order_reversed")
        if not superseded or current:
            fail("correction_lost", "the corrected claim is still current or was never stored")
    elif expect == "must_stay_current":
        current_rows = [row for row in store_hit if row.get("status") == "current"]
        superseded = any(row.get("status") == "superseded" for row in store_hit)
        if superseded and not current_rows:
            fail(
                "superseded_other_task",
                "the claim was superseded even though this case required it to stay current",
            )
        elif not current_rows and not rendered_hit:
            fail("lost_unrelated", "an unrelated claim is no longer current")
    elif expect == "must_not_be_current":
        if established:
            fail("stale_current", "a stale claim is still an established reality")
    elif expect == "handler_available":
        error = checkpoint.get("handoff_error") or checkpoint.get("now_error")
        panic = bool(checkpoint.get("panic"))
        alive = checkpoint.get("process_alive", True)
        if panic:
            flags.append("panic")
        if error or panic or not alive:
            fail("handler_unavailable", error or "the query handler panicked or the process exited")
    elif expect == "author_is":
        origins = _origins(rendered, claims, text)
        wanted = atom.get("provenance")
        if not origins:
            fail("missing_claim", "the claim was not stored, so authorship cannot match")
        elif any(origin != wanted for origin in origins):
            if "user" in origins and wanted == "agent":
                fail("origin_user", "an agent note was stored as a user claim")
            else:
                fail("wrong_origin", f"origin {origins} does not match {wanted}")
    elif expect == "task_matches":
        event_id = binds.get(atom.get("task_bind"))
        projector_tasks = {
            row["task_id"]
            for row in claims
            if row.get("origin") == "projector"
            and event_id
            and event_id in (row.get("evidence_ids") or [])
        }
        claim_tasks = {row["task_id"] for row in store_hit if row.get("status") == "current"}
        if not claim_tasks:
            fail("missing_claim", "the claim is not current, so its task cannot match")
        elif not projector_tasks:
            fail("missing_task", "no projector task cites the expected command event")
        elif claim_tasks.isdisjoint(projector_tasks):
            fail("wrong_task", "the claim is attached to a different task than the cited command")
    elif expect == "evidence_resolves":
        event_id = binds.get(atom.get("evidence_bind"))
        by_id = {event["id"]: event for event in events}
        evidence_ids: list[str] = []
        for row in store_hit + rendered_hit:
            evidence_ids.extend(row.get("evidence_ids") or [])
        if not event_id or event_id not in by_id:
            fail("evidence_missing", "the cited observation is not in the store")
        elif event_id not in evidence_ids:
            fail("evidence_mismatch", "the claim does not cite the expected observation")
        else:
            snippet = atom.get("snippet")
            payload = json.dumps(by_id[event_id].get("payload") or {})
            if snippet and snippet not in payload:
                fail("evidence_mismatch", "the cited observation does not contain the expected text")
    elif expect == "evidence_incomplete":
        event_id = binds.get(atom.get("evidence_bind"))
        by_id = {event["id"]: event for event in events}
        payload = (by_id.get(event_id) or {}).get("payload") or {}
        output = payload.get("output") if isinstance(payload, dict) else None
        incomplete = event_id in by_id and (
            not isinstance(output, str) or "truncated" in output.lower()
        )
        if not incomplete:
            block("the observation still has complete output, so this check does not apply")
            flags.append("situation_not_reached")
        elif established:
            flag = atom.get("when_presented") or "evidence_missing_but_presented"
            fail(flag, "incomplete evidence is still presented as an established reality")
        else:
            flags.append("output_removed")
    else:
        block(f"unknown expectation {expect}")

    if _duplicate_skip(atom, checkpoint) and not established:
        flags.append("duplicate_blocks_restatement")

    renderer_excluded = checker == "accepted" and not any(_same(row.get("text"), text) for row in rendered)
    if renderer_excluded and "renderer_excluded" not in flags:
        flags.append("renderer_excluded")

    return {
        "id": atom["id"],
        "text": text,
        "factuality": factuality,
        "expect": expect,
        "checkpoint": atom.get("checkpoint", "final"),
        "status": status,
        "passed": True if status == "pass" else False if status == "fail" else None,
        "flags": flags,
        "checker": checker,
        "presented_established": presented,
        "stored_current_reality": stored_current,
        "established": established,
        "origin": _origins(rendered, claims, text),
        "stored_status": [row.get("status") for row in store_hit],
        "renderer_excluded": renderer_excluded,
        "detail": detail,
        "justification": atom.get("justification"),
    }
