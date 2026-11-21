"""Atomic decomposition and all-atoms aggregation. No network."""

from harness.atomic import (
    aggregate,
    apply_second_pass,
    coverage_report,
    decompose,
    gold_audit,
    polarity_conflict,
    verify_atoms,
)


def test_fullwidth_tail_is_a_separate_atom() -> None:
    atoms = decompose("retry_count is incremented before open_socket。数据库连接池耗尽。")
    assert atoms == [
        "retry_count is incremented before open_socket",
        "数据库连接池耗尽",
    ]


def test_semicolon_tail_is_a_separate_atom() -> None:
    atoms = decompose(
        "attempt_budget increments before open_socket; the database pool is exhausted"
    )
    assert atoms == [
        "attempt_budget increments before open_socket",
        "the database pool is exhausted",
    ]


def test_if_idle_keeps_the_whole_claim_and_the_condition() -> None:
    atoms = decompose("counts connections if idle")
    assert atoms == ["counts connections if idle", "if idle"]


def test_if_not_idle_keeps_the_negation_on_the_condition() -> None:
    atoms = decompose("counts connections if not idle")
    assert atoms == ["counts connections if not idle", "if not idle"]


def test_a_single_temporal_claim_is_not_split() -> None:
    text = "retry_count increments before open_socket"
    assert decompose(text) == [text]


def test_a_negation_is_not_split_into_the_positive_event() -> None:
    text = "quota_left never increases before release_permit"
    assert decompose(text) == [text]


def test_supported_requires_every_atom() -> None:
    assert aggregate(["SUPPORTED", "SUPPORTED"]) == "SUPPORTED"
    assert aggregate(["SUPPORTED", "INSUFFICIENT_EVIDENCE"]) == "INSUFFICIENT_EVIDENCE"
    assert aggregate(["SUPPORTED", "CONTRADICTED"]) == "CONTRADICTED"


def test_idle_polarity_disagrees_with_not_idle() -> None:
    evidence = "return len([item for item in connections if not item.idle])"
    assert polarity_conflict("if idle", evidence)
    assert not polarity_conflict("if not idle", evidence)


def test_one_supported_clause_cannot_carry_an_unsupported_tail() -> None:
    evidence = "self.retry_count += 1\n    self.open_socket()\n"
    atoms = [
        {"text": "retry_count is incremented before open_socket", "label": "SUPPORTED", "evidence_quote": "self.retry_count += 1"},
        {"text": "数据库连接池耗尽", "label": "INSUFFICIENT_EVIDENCE", "evidence_quote": ""},
    ]
    verdict = verify_atoms(atoms, evidence)
    assert verdict["label"] == "INSUFFICIENT_EVIDENCE"
    assert verdict["writes_memory"] is False


def test_because_extracts_both_assertions() -> None:
    atoms = decompose("release_permit failed because quota_left increased")
    assert atoms == ["release_permit failed", "quota_left increased"]


def test_before_stays_one_ordered_assertion() -> None:
    text = "quota_left increases by 1 before release_permit"
    report = coverage_report(text)
    assert report["atoms"] == [text]
    assert report["checks"]["before"] is True
    assert report["covered"] is True


def test_coverage_sees_a_negation_and_a_fullwidth_tail() -> None:
    negated = coverage_report("quota_left never increases before release_permit")
    assert negated["checks"]["negation"] is True
    assert any("never" in atom for atom in negated["atoms"])
    tailed = coverage_report("quota_left increases by 1 before release_permit。许可已经耗尽。")
    assert tailed["checks"]["clause_boundary"] is True
    assert "许可已经耗尽" in tailed["atoms"]
    assert tailed["covered"] is True


def test_second_pass_cannot_force_support_without_a_quote() -> None:
    evidence = "self.quota_left += 1\n    self.release_permit()\n"
    first = [
        {
            "text": "the quota left counter grows by one before the permit is released",
            "label": "INSUFFICIENT_EVIDENCE",
            "evidence_quote": "",
        }
    ]
    forced = apply_second_pass(
        first,
        {
            "the quota left counter grows by one before the permit is released": {
                "label": "SUPPORTED",
                "evidence_quote": "not in the listing",
            }
        },
        evidence,
    )
    assert forced[0]["label"] == "INSUFFICIENT_EVIDENCE"
    abstained = apply_second_pass(
        first,
        {
            "the quota left counter grows by one before the permit is released": {
                "label": "INSUFFICIENT_EVIDENCE",
                "evidence_quote": "",
            }
        },
        evidence,
    )
    assert abstained[0]["label"] == "INSUFFICIENT_EVIDENCE"
    cited = apply_second_pass(
        first,
        {
            "the quota left counter grows by one before the permit is released": {
                "label": "SUPPORTED",
                "evidence_quote": "self.quota_left += 1",
            }
        },
        evidence,
    )
    assert cited[0]["label"] == "SUPPORTED"
    assert aggregate([item["label"] for item in cited]) == "SUPPORTED"


def test_gold_audit_keeps_d03_and_d14() -> None:
    notes = {item["case_id"]: item for item in gold_audit()}
    assert notes["d03-paraphrase"]["decision"] == "keep"
    assert notes["d03-paraphrase"]["gold"] == "SUPPORTED"
    assert "quota_left += 1" in notes["d03-paraphrase"]["evidence"]
    assert notes["d14-rust"]["decision"] == "keep"
    assert "quota_left += 1;" in notes["d14-rust"]["evidence"]


def test_reversed_condition_atom_blocks_the_sentence() -> None:
    evidence = "return len([item for item in connections if not item.idle])"
    atoms = [
        {"text": "counts connections", "label": "SUPPORTED", "evidence_quote": "len([item for item in connections if not item.idle])"},
        {"text": "if idle", "label": "SUPPORTED", "evidence_quote": "if not item.idle"},
    ]
    verdict = verify_atoms(atoms, evidence)
    assert verdict["label"] == "CONTRADICTED"
    assert verdict["atoms"][1]["label"] == "CONTRADICTED"
