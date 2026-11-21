"""Router decisions and fail-closed model errors. No network."""

from harness.router import apply_routes, route_claim

LISTING = "def release(self, permit):\n    self.quota_left += 1\n    self.release_permit()\n"
REVERSED = "def release(self, permit):\n    self.release_permit()\n    self.quota_left += 1\n"
RUST = "fn release(permit: i32) {\n    quota_left += 1;\n    release_permit();\n}\n"
GO = "func release(permit int) {\n    quotaLeft++\n    releasePermit()\n}\n"
CONFLICT = LISTING + "def release(self, permit):\n    self.quota_left = 0\n    self.release_permit()\n"
IDLE = "return len([item for item in connections if not item.idle])"


def test_literal_increment_is_deterministic_support() -> None:
    decision = route_claim("quota_left increases by 1 before release_permit", LISTING)
    assert decision.route == "deterministic"
    assert decision.label == "SUPPORTED"
    assert decision.evidence_quote
    assert decision.evidence_quote in LISTING


def test_rust_and_go_shapes_do_not_need_a_model() -> None:
    assert route_claim("quota_left increases by 1 before release_permit", RUST).label == "SUPPORTED"
    assert route_claim("quotaLeft increases by 1 before releasePermit", GO).label == "SUPPORTED"


def test_reversed_order_and_wrong_amount_are_contradictions() -> None:
    reversed_order = route_claim("quota_left increases by 1 before release_permit", REVERSED)
    assert reversed_order.label == "CONTRADICTED"
    assert reversed_order.evidence_quote
    assert reversed_order.evidence_quote in REVERSED
    assert route_claim("quota_left increases by 50 before release_permit", LISTING).label == "CONTRADICTED"
    assert route_claim("quota_left never increases before release_permit", LISTING).label == "CONTRADICTED"
    assert route_claim("retry_count never increments before open_socket", "self.retry_count += 1\nself.open_socket()\n").label == "CONTRADICTED"


def test_paraphrase_and_bare_increment_go_to_the_model() -> None:
    paraphrase = route_claim("the quota left counter grows by one before the permit is released", LISTING)
    bare = route_claim("retry_count increments before open_socket", "self.retry_count += 1\nself.open_socket()\n")
    assert paraphrase.route == "semantic"
    assert bare.route == "semantic"


def test_foreign_tail_and_missing_evidence_do_not_call_a_model() -> None:
    tailed = route_claim("quota_left increases by 1 before release_permit。许可已经耗尽。", LISTING)
    assert tailed.route == "deterministic"
    assert tailed.label == "INSUFFICIENT_EVIDENCE"
    missing = route_claim("quota_left increases by 1 before release_permit", "   ")
    assert missing.route == "abstain"
    assert missing.label == "INSUFFICIENT_EVIDENCE"
    assert missing.impact == "high"


def test_initialization_before_an_increment_is_not_a_conflict() -> None:
    evidence = "def release(self, permit):\n    self.quota_left = 0\n    self.quota_left += 1\n    self.release_permit()\n"
    decision = route_claim("quota_left increases by 1 before release_permit", evidence)
    assert decision.route == "deterministic"
    assert decision.label == "SUPPORTED"
    assert decision.evidence_quote


def test_textual_order_outside_one_straight_line_is_not_a_proof() -> None:
    split = "def bump():\n    quota_left += 1\n\ndef other():\n    release_permit()\n"
    branched = "def release(self, permit):\n    if permit:\n        self.quota_left += 1\n    else:\n        self.release_permit()\n"
    claim = "quota_left increases by 1 before release_permit"
    assert route_claim(claim, split).route == "semantic"
    assert route_claim(claim, branched).route == "semantic"


def test_conflicting_updates_abstain() -> None:
    decision = route_claim("quota_left increases by 1 before release_permit", CONFLICT)
    assert decision.route == "abstain"
    assert decision.reason == "conflicting_evidence"


def test_idle_polarity_is_a_deterministic_contradiction() -> None:
    decision = route_claim("counts connections if idle", IDLE)
    assert decision.route == "deterministic"
    assert decision.label == "CONTRADICTED"


def test_model_failure_fails_closed_without_raising() -> None:
    rows = [
        {
            "id": "d03",
            "suite": "development",
            "claim_text": "the quota left counter grows by one before the permit is released",
            "evidence_text": LISTING,
            "gold": "SUPPORTED",
            "gold_role": "supported",
        },
        {
            "id": "held",
            "suite": "heldout",
            "claim_text": "sealed",
            "evidence_text": LISTING,
            "gold": "SUPPORTED",
        },
        {
            "id": "scratch",
            "suite": "scratch",
            "claim_text": "not in this experiment",
            "evidence_text": LISTING,
            "gold": "SUPPORTED",
        },
        {
            "id": "blank",
            "claim_text": "missing suite",
            "evidence_text": LISTING,
            "gold": "SUPPORTED",
        },
    ]

    def explode(_rows):
        raise TimeoutError("api unavailable")

    results = apply_routes(rows, explode)
    assert [row["id"] for row in results] == ["d03"]
    assert results[0]["model_label"] == "INSUFFICIENT_EVIDENCE"
    assert results[0]["route"] == "semantic_failed"
    assert results[0]["writes_memory"] is False
    assert results[0]["llm_calls"] == 0
