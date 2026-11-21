"""Execute one evaluation case against an isolated daemon."""

from __future__ import annotations

from typing import Any

from harness.continuation import score_trace
from harness.daemon import IsolatedDaemon
from harness.judge import judge_atom, rendered_claims
from harness.store_observer import read_store

PANIC_MARK = "panicked at"


def incidents_from_steps(steps: list[dict[str, Any]], panic: bool) -> list[dict[str, Any]]:
    """Request failures and panics. These are measurements, not empty successes."""
    incidents: list[dict[str, Any]] = []
    for step in steps:
        if step.get("error") or step.get("transport_error"):
            incidents.append(
                {
                    "client_seq": step.get("client_seq"),
                    "action": step.get("action"),
                    "query": step.get("query"),
                    "error": step.get("error"),
                    "transport_error": step.get("transport_error"),
                }
            )
    if panic:
        incidents.append({"kind": "panic", "error": PANIC_MARK})
    return incidents


def harness_error_judgment(atom: dict[str, Any], exc: Exception) -> dict[str, Any]:
    return {
        "id": atom.get("id"),
        "text": atom.get("text"),
        "factuality": atom.get("factuality"),
        "expect": atom.get("expect"),
        "checkpoint": atom.get("checkpoint", "final"),
        "metric_role": atom.get("metric_role"),
        "status": "blocked",
        "disposition": "harness_error",
        "covered": None,
        "passed": None,
        "flags": ["harness_error"],
        "checker": "absent",
        "evidence": "not_applicable",
        "presented_established": False,
        "stored_current_reality": False,
        "established": False,
        "origin": [],
        "stored_status": [],
        "renderer_excluded": False,
        "detail": f"harness error: {exc}",
        "justification": atom.get("justification"),
    }


def _session(case: dict[str, Any], step: dict[str, Any]) -> str:
    if "session" in step:
        return str(step["session"])
    return str(case.get("session") or "default")


def _checkpoint(
    name: str,
    daemon: IsolatedDaemon,
    binds: dict[str, str],
    handoff: dict[str, Any] | None,
    now: dict[str, Any] | None,
    handoff_error: str | None,
    now_error: str | None,
) -> dict[str, Any]:
    store = read_store(daemon.db_path)
    stderr = daemon.stderr_text()
    accepted = list((handoff or {}).get("accepted") or [])
    rejected = list((handoff or {}).get("rejected") or [])
    skipped = list((handoff or {}).get("skipped") or [])
    snapshot = (now or {}).get("response") if isinstance(now, dict) else None
    return {
        "name": name,
        "binds": dict(binds),
        "accepted": accepted,
        "rejected": rejected,
        "skipped": skipped,
        "rendered": rendered_claims(snapshot if isinstance(snapshot, dict) else None),
        "store_claims": store["claims"],
        "store_events": store["events"],
        "store_tasks": store["tasks"],
        "handoff_error": handoff_error,
        "now_error": now_error,
        "process_alive": daemon.alive(),
        "panic": PANIC_MARK in stderr,
        "diagnostics": list((handoff or {}).get("diagnostics") or []),
    }


def _apply_step(
    case: dict[str, Any],
    step: dict[str, Any],
    daemon: IsolatedDaemon,
    binds: dict[str, str],
    state: dict[str, Any],
    client_seq: int,
) -> dict[str, Any]:
    action = step["action"]
    record: dict[str, Any] = {"client_seq": client_seq, "action": action, "transport_error": None}
    if action == "observe":
        body: dict[str, Any] = {
            "query": "observe",
            "kind": step["kind"],
            "session": _session(case, step),
        }
        for key in ("command", "cwd", "exit_code", "output", "text", "replaces_text"):
            if key in step:
                body[key] = step[key]
        if step.get("support"):
            body["support_event_id"] = binds[step["support"]]
        outcome = daemon.ask(body)
        record["query"] = "observe"
        record["error"] = outcome["error"]
        record["transport_error"] = outcome.get("transport_error")
        response = outcome["response"] or {}
        event_id = response.get("event_id")
        record["event_id"] = event_id
        record["stored"] = response.get("stored")
        if step.get("bind") and event_id:
            binds[step["bind"]] = str(event_id)
        record["bind"] = step.get("bind")
        if step.get("kind") == "tool":
            state.setdefault("evidence_text", []).append(str(step.get("output") or ""))
    elif action in {"handoff", "now"}:
        outcome = daemon.ask({"query": action})
        record["query"] = action
        record["error"] = outcome["error"]
        record["transport_error"] = outcome.get("transport_error")
        response = outcome["response"] or {}
        state[action] = {"response": response, "error": outcome["error"]}
        if action == "handoff" and outcome["error"] is None:
            record["accepted"] = [item.get("text") for item in response.get("accepted") or []]
            record["skipped"] = response.get("skipped") or []
            record["rejected"] = response.get("rejected") or []
            record["diagnostics"] = response.get("diagnostics") or []
            state.setdefault("diagnostics", []).extend(record["diagnostics"])
        if action == "now" and outcome["error"] is None:
            record["claim_texts"] = [item["text"] for item in rendered_claims(response)]
    elif action == "agent_claim":
        body = {
            "query": "agent_claim",
            "kind": step["kind"],
            "text": step["text"],
        }
        if step.get("confidence"):
            body["confidence"] = step["confidence"]
        if step.get("replaces"):
            body["replaces"] = binds.get(step["replaces"], step["replaces"])
        outcome = daemon.ask(body)
        record["query"] = "agent_claim"
        record["error"] = outcome["error"]
        record["transport_error"] = outcome.get("transport_error")
        record["response"] = outcome["response"]
        if outcome["error"] is None:
            record["admitted_without_gate"] = True
    elif action == "user_claim":
        text = step.get("text")
        if step.get("text_bind"):
            text = binds[step["text_bind"]]
        outcome = daemon.ask({"query": "user_claim", "kind": step["kind"], "text": text})
        record["query"] = "user_claim"
        record["error"] = outcome["error"]
        record["transport_error"] = outcome.get("transport_error")
        record["response"] = outcome["response"]
        record["admitted_without_gate"] = outcome["error"] is None
    elif action == "intent":
        outcome = daemon.ask({"query": "intent", "text": step["text"]})
        record["query"] = "intent"
        record["error"] = outcome["error"]
        record["transport_error"] = outcome.get("transport_error")
        record["admitted_without_gate"] = outcome["error"] is None
    elif action == "restart":
        try:
            daemon.restart()
            record["restart_ok"] = True
            record["error"] = None
        except Exception as exc:  # startup failure is a measurement, not a crash of the suite
            record["restart_ok"] = False
            record["error"] = str(exc)
    elif action == "wait_compaction":
        import time

        event_id = binds[step["bind"]]
        deadline = time.monotonic() + (step.get("timeout_ms", 8000) / 1000)
        compacted = False
        while time.monotonic() < deadline:
            store = read_store(daemon.db_path)
            payload = next(
                (event["payload"] for event in store["events"] if event["id"] == event_id),
                None,
            )
            if isinstance(payload, dict) and "output" not in payload:
                compacted = True
                break
            time.sleep(0.1)
        record["compacted"] = compacted
        record["error"] = None if compacted else "compaction did not become visible before the timeout"
        state["compaction_blocked"] = not compacted
    elif action == "checkpoint":
        record["name"] = step["name"]
    else:
        record["error"] = f"unknown action {action}"
    record["process_alive"] = daemon.alive()
    return record


def reproduction_class(case: dict[str, Any], atoms: list[dict[str, Any]], executed: bool) -> str:
    initial = case.get("initial_class") or "open"
    if case.get("control"):
        return "control" if executed else initial
    if case.get("judgment") == "open_contract":
        return initial
    if not executed:
        return "predicted" if initial == "reproduced" else initial
    if initial == "contract_limitation":
        return "contract_limitation"
    wanted = set(case.get("predicted_violations") or [])
    if case.get("predicted_violation"):
        wanted.add(case["predicted_violation"])
    seen = {flag for atom in atoms for flag in atom.get("flags") or []}
    if initial in {"reproduced", "source_analysis", "open"}:
        if wanted and wanted & seen:
            return "reproduced"
        if not wanted and any(atom.get("status") == "fail" for atom in atoms):
            return "reproduced"
        return "not_observed"
    return initial


def _status_from_atoms(atoms: list[dict[str, Any]]) -> tuple[str, str | None, bool | None, bool]:
    harness = any(
        atom.get("disposition") == "harness_error" or "harness_error" in (atom.get("flags") or [])
        for atom in atoms
    )
    if any(atom.get("status") == "fail" for atom in atoms):
        return "fail", None, False, harness
    if harness:
        reasons = [atom.get("detail") for atom in atoms if atom.get("disposition") == "harness_error"]
        return "blocked", (reasons[0] if reasons else "harness error"), None, True
    if any(atom.get("status") == "blocked" for atom in atoms):
        reasons = [atom["detail"] for atom in atoms if atom.get("status") == "blocked"]
        return "blocked", reasons[0] if reasons else "atom blocked", None, False
    if any(atom.get("status") == "incomplete" for atom in atoms):
        return "incomplete", None, None, False
    if any(atom.get("status") == "vacuous" for atom in atoms):
        return "vacuous", None, None, False
    return "pass", None, True, False


def execute_case(case: dict[str, Any], binary: Any) -> dict[str, Any]:
    mode = case.get("mode", "daemon")
    base = {
        "id": case["id"],
        "suite": case.get("suite"),
        "failure_id": case.get("failure_id"),
        "title": case.get("title"),
        "mode": mode,
        "initial_class": case.get("initial_class"),
        "judgment": case.get("judgment", "contract"),
        "control": bool(case.get("control")),
        "deterministic": bool(case.get("deterministic", mode == "daemon")),
        "needs_human_adjudication": bool(case.get("needs_human_adjudication")),
        "notes": case.get("notes"),
    }
    if mode == "documented":
        return {
            **base,
            "executed": False,
            "status": "blocked",
            "blocked_reason": case.get("blocked_reason") or "documented",
            "contract_holds": None,
            "reproduction_class": reproduction_class(case, [], False),
            "atoms": [],
            "steps": [],
            "panic": False,
        }
    if mode == "grader":
        from harness.validity_demo import regex_gap

        demo = regex_gap()
        holds = bool(demo["regex_matches"] and demo["grader_flags_false_reality"])
        return {
            **base,
            "executed": True,
            "status": "pass" if holds else "fail",
            "blocked_reason": None,
            "contract_holds": holds,
            "reproduction_class": "reproduced" if holds else "not_observed",
            "atoms": demo["judgment"],
            "steps": [],
            "panic": False,
            "measures": "grader",
            "demonstration": demo,
        }
    if mode == "synthetic":
        scored = score_trace(case)
        holds = scored["layer"] == case["expected_layer"]
        return {
            **base,
            "executed": True,
            "status": "pass" if holds else "fail",
            "blocked_reason": None,
            "contract_holds": holds,
            "reproduction_class": "control",
            "atoms": [],
            "steps": [],
            "panic": False,
            "continuation": scored,
            "measures": "metric",
        }
    if mode != "daemon":
        return {
            **base,
            "executed": False,
            "status": "blocked",
            "blocked_reason": f"unknown mode {mode}",
            "contract_holds": None,
            "reproduction_class": "open",
            "atoms": [],
            "steps": [],
            "panic": False,
        }

    daemon = IsolatedDaemon(binary)
    steps: list[dict[str, Any]] = []
    checkpoints: dict[str, dict[str, Any]] = {}
    binds: dict[str, str] = {}
    state: dict[str, Any] = {"handoff": None, "now": None, "compaction_blocked": False}
    try:
        daemon.start()
        for index, step in enumerate(case.get("steps") or [], start=1):
            record = _apply_step(case, step, daemon, binds, state, index)
            steps.append(record)
            if step["action"] == "checkpoint" or index == len(case.get("steps") or []):
                name = step.get("name") if step["action"] == "checkpoint" else "final"
                if name not in checkpoints or step["action"] == "checkpoint":
                    handoff = state.get("handoff") or {}
                    now = state.get("now") or {}
                    checkpoints[name] = _checkpoint(
                        name,
                        daemon,
                        binds,
                        handoff.get("response") if isinstance(handoff, dict) else None,
                        now,
                        (handoff or {}).get("error") if isinstance(handoff, dict) else None,
                        (now or {}).get("error") if isinstance(now, dict) else None,
                    )
        if "final" not in checkpoints:
            handoff = state.get("handoff") or {}
            now = state.get("now") or {}
            checkpoints["final"] = _checkpoint(
                "final",
                daemon,
                binds,
                handoff.get("response") if isinstance(handoff, dict) else None,
                now,
                (handoff or {}).get("error") if isinstance(handoff, dict) else None,
                (now or {}).get("error") if isinstance(now, dict) else None,
            )
    except Exception as exc:
        stderr_tail = daemon.stderr_text()[-2500:]
        panic = PANIC_MARK in daemon.stderr_text()
        daemon.close()
        return {
            **base,
            "executed": False,
            "status": "blocked",
            "blocked_reason": f"harness error: {exc}",
            "contract_holds": None,
            "reproduction_class": reproduction_class(case, [], False),
            "atoms": [],
            "steps": steps,
            "panic": panic,
            "stderr_tail": stderr_tail,
            "incidents": incidents_from_steps(steps, panic),
            "harness_error": True,
        }

    panic = PANIC_MARK in daemon.stderr_text()
    stderr_tail = daemon.stderr_text()[-2500:]
    final_checkpoint = checkpoints.get("final") or {}
    store_summary = {
        "tasks": [
            {"id": task["id"], "status": task["status"]}
            for task in final_checkpoint.get("store_tasks") or []
        ],
        "claims": [
            {
                "text": claim["text"],
                "status": claim["status"],
                "kind": claim["kind"],
                "origin": claim["origin"],
                "confidence": claim["confidence"],
                "task_id": claim["task_id"],
            }
            for claim in final_checkpoint.get("store_claims") or []
        ],
    }
    daemon.close()

    judgments = []
    for atom in case.get("atoms") or []:
        name = atom.get("checkpoint", "final")
        checkpoint = checkpoints.get(name)
        if checkpoint is None:
            judgments.append(
                {
                    "id": atom["id"],
                    "text": atom["text"],
                    "factuality": atom.get("factuality"),
                    "expect": atom["expect"],
                    "checkpoint": name,
                    "metric_role": atom.get("metric_role"),
                    "status": "blocked",
                    "disposition": "blocked",
                    "covered": False,
                    "passed": None,
                    "flags": ["missing_checkpoint"],
                    "checker": "absent",
                    "evidence": "not_applicable",
                    "presented_established": False,
                    "stored_current_reality": False,
                    "established": False,
                    "origin": [],
                    "stored_status": [],
                    "renderer_excluded": False,
                    "detail": f"checkpoint {name} was not recorded",
                    "justification": atom.get("justification"),
                }
            )
            continue
        try:
            judgments.append(judge_atom(atom, checkpoint))
        except Exception as exc:
            judgments.append(harness_error_judgment(atom, exc))

    if state.get("compaction_blocked"):
        for judgment in judgments:
            if judgment["expect"] == "evidence_incomplete" and judgment["status"] != "fail":
                judgment["status"] = "blocked"
                judgment["disposition"] = "blocked"
                judgment["passed"] = None
                judgment["detail"] = "compaction did not become visible before the timeout"
                if "situation_not_reached" not in judgment["flags"]:
                    judgment["flags"].append("situation_not_reached")

    status, blocked_reason, holds, harness_error = _status_from_atoms(judgments)
    if case.get("judgment") == "open_contract":
        status = "blocked"
        blocked_reason = "open_contract"
        holds = None
    executed = True
    panic = PANIC_MARK in stderr_tail or panic
    return {
        **base,
        "executed": executed,
        "status": status,
        "blocked_reason": blocked_reason,
        "contract_holds": holds,
        "reproduction_class": reproduction_class(case, judgments, True),
        "atoms": judgments,
        "steps": steps,
        "panic": panic,
        "stderr_tail": stderr_tail,
        "incidents": incidents_from_steps(steps, panic),
        "harness_error": harness_error,
        "checkpoints": sorted(checkpoints),
        "store_summary": store_summary,
        "verifier_diagnostics": list(state.get("diagnostics") or []),
        "evidence_text": "\n".join(state.get("evidence_text") or []),
    }
