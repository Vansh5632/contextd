"""Adversarial safety and the boundary between product defects and the router."""

from __future__ import annotations

from pathlib import Path

from datasets.define_regression import DELIVER, PANIC
from harness.adversarial import evaluate
from harness.generalization import heldout_rows
from harness.router import apply_routes, route_claim

ROOT = Path(__file__).resolve().parents[3]


def test_adversarial_supported_rules_are_sound() -> None:
    report = evaluate()
    failed = [row for row in report if not row["matches"]]
    assert failed == []
    assert all(row["writes_memory"] is False for row in report)


def test_unicode_and_restart_do_not_change_the_router() -> None:
    first = route_claim(PANIC, DELIVER)
    second = route_claim(PANIC, DELIVER)
    assert first == second
    assert first.label != "SUPPORTED"
    claim = "retry_count increases by 1 before open_socket"
    listed = route_claim(claim, DELIVER)
    assert listed.label == "SUPPORTED"
    assert route_claim(claim, DELIVER) == listed


def test_heldout_evidence_is_the_cited_listing_only() -> None:
    rows = heldout_rows()
    assert {row["id"] for row in rows} == {
        "h01-rust-supported",
        "h02-rust-order",
        "h03-js-decrement",
        "h04-js-negation",
        "h05-go-guard",
        "h06-mixed-tail",
    }
    assert all("probe failed" not in row["evidence_text"] for row in rows)
    assert all(row["writes_memory"] is False for row in rows)


def test_default_routing_still_drops_heldout_rows() -> None:
    rows = heldout_rows()
    assert apply_routes(rows, lambda batch: batch) == []


def test_two_tasks_do_not_share_evidence() -> None:
    rows = [
        {
            "id": "one",
            "suite": "development",
            "claim_text": "quota_left increases by 1 before release_permit",
            "evidence_text": "def release(self):\n    self.quota_left += 1\n    self.release_permit()\n",
            "gold": "SUPPORTED",
        },
        {
            "id": "two",
            "suite": "development",
            "claim_text": "quota_left increases by 1 before release_permit",
            "evidence_text": "def release(self):\n    self.release_permit()\n    self.quota_left += 1\n",
            "gold": "CONTRADICTED",
        },
    ]
    routed = apply_routes(rows, lambda batch: batch)
    by_id = {row["id"]: row for row in routed}
    assert by_id["one"]["model_label"] == "SUPPORTED"
    assert by_id["two"]["model_label"] == "CONTRADICTED"
    assert "release_permit()\n    self.quota_left" not in by_id["one"]["evidence_text"]


def test_a_semantic_result_keeps_its_token_counts() -> None:
    rows = [
        {
            "id": "s",
            "suite": "development",
            "claim_text": "the counter grows before the call",
            "evidence_text": "x += 1\ncall()\n",
            "gold": "SUPPORTED",
        }
    ]

    def semantic(batch):
        return [
            {
                **batch[0],
                "model_label": "SUPPORTED",
                "evidence_quote": "x += 1",
                "llm_calls": 1,
                "prompt_tokens": 11,
                "completion_tokens": 7,
                "latency_s": 0.5,
            }
        ]

    routed = apply_routes(rows, semantic)
    assert routed[0]["route"] == "semantic"
    assert routed[0]["prompt_tokens"] == 11
    assert routed[0]["completion_tokens"] == 7
    assert routed[0]["latency_s"] == 0.5


def test_model_call_finishes_before_the_writer_lock() -> None:
    daemon = (ROOT / "bin" / "contextd" / "src" / "main.rs").read_text()
    start = daemon.index("async fn verified_handoff")
    body = daemon[start : daemon.index("async fn current_projection", start)]
    assert body.index("classify_semantic") < body.index("store.writer()")
    assert "store.writer()" not in body[: body.index("classify_semantic")]
    verify = (ROOT / "crates" / "broker" / "src" / "verify.rs").read_text()
    signature = verify[verify.index("pub async fn classify_semantic") : verify.index("pub async fn classify_semantic") + 180]
    assert "Connection" not in signature


def test_note_claim_does_not_call_the_router() -> None:
    daemon = (ROOT / "bin" / "contextd" / "src" / "main.rs").read_text()
    start = daemon.index("async fn record_agent_claim")
    body = daemon[start : daemon.index("#[tokio::main]", start)]
    assert "evidence_ids: Vec::new()" in body
    assert "router" not in body
    assert "run_shadow" not in body
    mcp = (ROOT / "crates" / "mcp" / "src" / "server.rs").read_text()
    assert "harness.router" not in mcp
    assert "run_shadow" not in mcp
