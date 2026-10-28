"""
Policy Continuity Check — deterministic engine.

Every status is decided here from dates and references alone; these tests pin
that behaviour, including the rule that missing history is never a gap.
"""

from app.services.coverage.policy_continuity import (
    STATUS_CONFLICT,
    STATUS_GAP,
    STATUS_UNKNOWN,
    STATUS_VERIFIED,
    evaluate_policy_continuity,
    declared_policy_continuity,
    parse_policy_date,
    resolve_claim_date,
)
from app.services.extraction.policy_continuity_extraction import (
    merge_continuity_fields,
    needs_continuity_extraction,
)

RX = {"diagnosis": "Appendicitis", "admissionDate": "2025-06-10"}


def policy(pid, start, end, **extra):
    pj = {"policyStartDate": start, "policyEndDate": end, "insuranceCompany": "Insurer A", **extra}
    return {"policyId": pid, "policyJson": pj}


def run(policies, rx=RX, current=None):
    return evaluate_policy_continuity(policies, rx, current_policy_id=current)


def roles(result):
    return {row["policyId"]: row["role"] for row in result["policiesEvaluated"] if row.get("policyId")}


# ─── Single policy ───────────────────────────────────────────────────────────

def test_single_policy_without_history_cannot_be_established():
    result = run([policy("A", "2025-01-01", "2025-12-31", policyNumber="P1")])

    assert result["status"] == STATUS_UNKNOWN
    assert result["coverageGaps"] == []
    # Missing previous-policy information is not treated as a gap or an origin.
    assert result["originalPolicyStartDate"] is None
    assert result["earliestDocumentedStartDate"] == "2025-01-01"
    assert result["currentPolicy"]["startDate"] == "2025-01-01"
    assert result["claimDate"] == "2025-06-10"
    assert result["claimDateSource"] == "Admission date"


def test_fresh_policy_is_its_own_origin():
    result = run([policy("A", "2025-01-01", "2025-12-31", isRenewal=False)])

    assert result["status"] == STATUS_VERIFIED
    assert result["originalPolicyStartDate"] == "2025-01-01"


def test_continuous_coverage_statement_sets_origin():
    result = run([policy(
        "A", "2025-01-01", "2025-12-31",
        isRenewal=True, continuousCoverageSince="15/03/2019",
        continuityEvidence=["Continuous coverage since 15/03/2019"],
    )])

    assert result["status"] == STATUS_VERIFIED
    assert result["originalPolicyStartDate"] == "2019-03-15"
    types = {e["type"] for e in result["continuityEvidence"]}
    assert {"continuous_coverage_statement", "renewal_statement", "document_quote", "claim_date_in_period"} <= types


def test_renewal_reference_without_previous_period_cannot_be_established():
    result = run([policy("A", "2025-01-01", "2025-12-31", isRenewal=True, previousPolicyNumber="P0")])

    assert result["status"] == STATUS_UNKNOWN
    assert any("previous policy period is not documented" in m for m in result["missingInformation"])
    assert any(e["type"] == "renewal_statement" for e in result["continuityEvidence"])


def test_declared_previous_period_consecutive_is_verified():
    result = run([policy(
        "A", "2025-01-01", "2025-12-31",
        previousPolicyNumber="P0", previousPolicyStartDate="2024-01-01", previousPolicyEndDate="2024-12-31",
    )])

    assert result["status"] == STATUS_VERIFIED
    assert result["originalPolicyStartDate"] == "2024-01-01"
    assert any(r["role"] == "declared" for r in result["policiesEvaluated"])


def test_declared_previous_period_with_gap_is_detected():
    result = run([policy(
        "A", "2025-02-15", "2026-02-14",
        previousPolicyNumber="P0", previousPolicyStartDate="2024-01-01", previousPolicyEndDate="2024-12-31",
    )])

    assert result["status"] == STATUS_GAP
    gap = result["coverageGaps"][0]
    assert (gap["from"], gap["to"], gap["days"]) == ("2025-01-01", "2025-02-14", 45)
    assert gap["withinRenewalGraceWindow"] is False
    assert result["originalPolicyStartDate"] == "2024-01-01"
    assert result["continuousCoverageFrom"] == "2025-02-15"


def test_nested_continuity_block_is_read():
    pj = {"policyStartDate": "2025-01-01", "policyEndDate": "2025-12-31"}
    merge_continuity_fields(pj, {"isRenewal": True, "continuousCoverageSince": "2020-04-01"})

    result = run([{"policyId": "A", "policyJson": pj}])

    assert result["status"] == STATUS_VERIFIED
    assert result["originalPolicyStartDate"] == "2020-04-01"


# ─── Multiple policies ───────────────────────────────────────────────────────

def test_consecutive_renewals_verified_from_any_policy():
    policies = [
        policy("A", "2023-01-01", "2023-12-31", policyNumber="P-100"),
        policy("B", "2024-01-01", "2024-12-31", policyNumber="P-100"),
        policy("C", "2025-01-01", "2025-12-31", policyNumber="P 100"),
    ]
    for current in ("A", "B", "C"):
        result = run(policies, current=current)
        assert result["status"] == STATUS_VERIFIED, current
        assert result["originalPolicyStartDate"] == "2023-01-01"
        assert result["currentPolicy"]["policyId"] == "C"
        assert roles(result) == {"A": "previous", "B": "previous", "C": "current"}


def test_consecutive_periods_link_documents_without_references():
    result = run([
        policy("A", "2024-01-01", "2024-12-31"),
        policy("B", "2025-01-01", "2025-12-31"),
    ], current="B")

    assert result["status"] == STATUS_VERIFIED
    assert any(e["type"] == "consecutive_periods" for e in result["continuityEvidence"])


def test_explicit_reference_with_gap_between_documents():
    result = run([
        policy("A", "2024-01-01", "2024-12-31", policyNumber="OLD-1"),
        policy("B", "2025-01-20", "2026-01-19", policyNumber="NEW-2", previousPolicyNumber="OLD-1"),
    ], current="B")

    assert result["status"] == STATUS_GAP
    gap = result["coverageGaps"][0]
    assert gap["days"] == 19
    assert gap["withinRenewalGraceWindow"] is True
    assert gap["betweenPolicies"][1].startswith("Insurer A")


def test_unrelated_alternative_policies_are_not_linked():
    policies = [
        policy("A", "2025-01-01", "2025-12-31", insuranceCompany="Insurer A", policyNumber="A1"),
        policy("B", "2025-03-01", "2026-02-28", insuranceCompany="Insurer B", policyNumber="B1"),
    ]
    result = run(policies, current="A")

    assert result["status"] == STATUS_UNKNOWN
    assert roles(result) == {"A": "current", "B": "not_linked"}


def test_statement_contradicted_by_gap_is_a_conflict():
    result = run([
        policy("A", "2023-01-01", "2023-12-31", policyNumber="P1"),
        policy("B", "2024-03-01", "2025-02-28", policyNumber="P1", continuousCoverageSince="2023-01-01"),
    ], current="B", rx={"visitDate": "2024-06-01"})

    assert result["status"] == STATUS_CONFLICT
    assert result["originalPolicyStartDate"] is None
    assert result["continuityConflicts"]


def test_declared_previous_end_disagreeing_with_document_is_a_conflict():
    result = run([
        policy("A", "2024-01-01", "2024-12-31", policyNumber="P0"),
        policy("B", "2025-01-01", "2025-12-31", policyNumber="P1",
               previousPolicyNumber="P0", previousPolicyEndDate="2024-11-30"),
    ], current="B")

    assert result["status"] == STATUS_CONFLICT


def test_claim_date_between_linked_policies_is_a_gap():
    result = run([
        policy("A", "2024-01-01", "2024-12-31", policyNumber="P1"),
        policy("B", "2025-08-01", "2026-07-31", policyNumber="P1"),
    ], current="A")

    assert result["status"] == STATUS_GAP
    assert result["coverageGaps"][0]["coversClaimDate"] is True


def test_claim_date_after_all_policies_is_not_assumed_a_gap():
    result = run([policy("A", "2023-01-01", "2023-12-31", isRenewal=False)])

    assert result["status"] == STATUS_UNKNOWN
    assert result["coverageGaps"] == []


def test_different_policyholders_are_not_linked_by_dates():
    result = run([
        policy("A", "2024-01-01", "2024-12-31", policyHolder="Mr. Ravi Kumar"),
        policy("B", "2025-01-01", "2025-12-31", policyHolder="Anita Sharma"),
    ], current="B")

    assert result["status"] == STATUS_UNKNOWN
    assert roles(result)["A"] == "not_linked"


def test_same_holder_with_title_difference_still_links():
    result = run([
        policy("A", "2024-01-01", "2024-12-31", policyHolder="Mr. Ravi Kumar"),
        policy("B", "2025-01-01", "2025-12-31", policyHolder="RAVI KUMAR"),
    ], current="B")

    assert result["status"] == STATUS_VERIFIED


def test_duplicate_documents_are_counted_once():
    result = run([
        policy("A", "2025-01-01", "2025-12-31", policyNumber="P1", isRenewal=False),
        policy("A2", "2025-01-01", "2025-12-31", policyNumber="P1", isRenewal=False),
    ], current="A")

    assert result["status"] == STATUS_VERIFIED
    assert roles(result)["A2"] == "duplicate"


def test_missing_claim_date_cannot_be_established():
    result = run([policy("A", "2025-01-01", "2025-12-31", isRenewal=False)], rx={"diagnosis": "x"})

    assert result["status"] == STATUS_UNKNOWN
    assert result["claimDate"] is None


def test_undated_policy_cannot_be_established():
    result = run([{"policyId": "A", "policyJson": {"policyNumber": "P1"}}], current="A")

    assert result["status"] == STATUS_UNKNOWN
    assert result["currentPolicy"] == {}
    assert roles(result) == {"A": "undated"}


# ─── Helpers ─────────────────────────────────────────────────────────────────

def test_date_parsing_formats():
    assert parse_policy_date("01/04/2024").isoformat() == "2024-04-01"
    assert parse_policy_date("1st April 2024").isoformat() == "2024-04-01"
    assert parse_policy_date("01 Apr 2024").isoformat() == "2024-04-01"
    assert parse_policy_date("2024-04-01T00:00:00").isoformat() == "2024-04-01"
    assert parse_policy_date("12") is None
    assert parse_policy_date("") is None
    assert parse_policy_date("Yes") is None


def test_claim_date_prefers_admission_over_visit():
    d, source = resolve_claim_date({"visitDate": "2025-06-01", "admission": "2025-06-05"})
    assert d.isoformat() == "2025-06-05" and source == "Admission date"
    d, source = resolve_claim_date({"visitDate": "2025-06-01", "admission": "Admitted"})
    assert d.isoformat() == "2025-06-01" and source == "Visit date"


def test_continuity_extraction_marker():
    pj = {"policyStartDate": "2025-01-01"}
    assert needs_continuity_extraction(pj)
    merge_continuity_fields(pj, {"policyStartDate": "2099-01-01"})
    assert not needs_continuity_extraction(pj)
    # The continuity pass never overwrites the main extraction.
    assert pj["policyStartDate"] == "2025-01-01"


# ─── Declared Policy Continuity (UI Spec Requirements) ───────────────────────

def test_declared_policy_continuity_verified_no_gaps():
    pj = {
        "declaredInceptionDate": "2022-04-01",
        "policyStartDate": "2025-04-01",
        "policyEndDate": "2026-03-31",
        "continuousRenewal": True,
        "insuranceCompany": "Star Health",
        "policyNumber": "STAR-12345",
    }
    rx = {"admissionDate": "2025-08-15"}
    result = declared_policy_continuity(pj, rx)

    assert result is not None
    assert result["status"] == STATUS_VERIFIED
    # 1. Original Policy Start Date is manually entered inception date
    assert result["originalPolicyStartDate"] == "2022-04-01"
    assert result["originalPolicyStartSource"] == "Entered by you"
    # 2. Current Policy Period is retrieved from uploaded policy document
    assert result["currentPolicy"]["startDate"] == "2025-04-01"
    assert result["currentPolicy"]["endDate"] == "2026-03-31"
    assert result["currentPolicy"]["label"] == "Star Health (STAR-12345)"
    # 3. Continuity Status is verified with no gaps
    assert result["coverageGaps"] == []
    # 4. Claim date retrieved from admission date
    assert result["claimDate"] == "2025-08-15"
    assert result["claimDateSource"] == "Admission date"
    # 5. Policies Evaluated and Continuity Evidence removed
    assert result["policiesEvaluated"] == []
    assert result["continuityEvidence"] == []


def test_declared_policy_continuity_gap_when_renewal_had_break():
    pj = {
        "declaredInceptionDate": "2020-01-01",
        "policyStartDate": "2025-01-01",
        "policyEndDate": "2025-12-31",
        "continuousRenewal": False,
        "coverRestartDate": "2024-01-01",
    }
    rx = {"consultationDate": "2025-05-10"}
    result = declared_policy_continuity(pj, rx)

    assert result is not None
    # Gaps detected -> not verified
    assert result["status"] == STATUS_GAP
    assert len(result["coverageGaps"]) == 1
    assert result["claimDate"] == "2025-05-10"
    assert result["claimDateSource"] == "Consultation date"


def test_declared_policy_continuity_gap_when_claim_after_end_date():
    pj = {
        "declaredInceptionDate": "2022-01-01",
        "policyStartDate": "2024-01-01",
        "policyEndDate": "2024-12-31",
        "continuousRenewal": True,
    }
    rx = {"admissionDate": "2025-02-15"}
    result = declared_policy_continuity(pj, rx)

    assert result is not None
    # Claim date after policy end date -> GAP
    assert result["status"] == STATUS_GAP
    assert len(result["coverageGaps"]) == 1
    assert result["coverageGaps"][0]["from"] == "2025-01-01"
    assert result["coverageGaps"][0]["to"] == "2025-02-15"


def test_declared_policy_continuity_fallback_to_consultation_date():
    pj = {
        "declaredInceptionDate": "2023-01-01",
        "policyStartDate": "2025-01-01",
        "policyEndDate": "2025-12-31",
        "continuousRenewal": True,
    }
    # No admissionDate, only consultationDate
    rx = {"consultationDate": "2025-06-20", "diagnosis": "Fever"}
    result = declared_policy_continuity(pj, rx)

    assert result is not None
    assert result["status"] == STATUS_VERIFIED
    assert result["claimDate"] == "2025-06-20"
    assert result["claimDateSource"] == "Consultation date"


def test_evaluate_policy_continuity_uses_manual_inputs_not_document_extraction():
    """
    Even when the policy document PDF mentions previous policies and continuous cover
    from 2024-04-01, the user's manual inputs (inception 2023-04-01, renewed without break: No,
    restart: 2025-04-01) MUST be used as the original start date and continuity basis.
    """
    pj = {
        # Manually entered on upload
        "inceptionDate": "2023-04-01",
        "continuousRenewal": False,
        "coverRestartDate": "2025-04-01",
        # Extracted from policy PDF
        "policyStartDate": "2026-04-01",
        "policyEndDate": "2027-03-31",
        "insuranceCompany": "Prakriti Insurance",
        "policyNumber": "PGI-HL-26-4410287",
        # Text/clauses from policy PDF that previously caused false verification
        "continuousCoverageSince": "2024-04-01",
        "previousPolicyNumber": "PGI/HL/24/3307419",
        "previousPolicyStartDate": "2024-04-01",
        "previousPolicyEndDate": "2026-03-31",
        "isRenewal": True,
    }
    # Prescription document
    rx = {
        "admissionDate": "2026-10-12",
        "diagnosis": "Hernia",
    }

    result = evaluate_policy_continuity([{"policyId": "P1", "policyJson": pj}], rx, current_policy_id="P1")

    assert result is not None
    # 1. Original Policy Start Date MUST be from manual inputs (2023-04-01), NOT document (2024-04-01)
    assert result["originalPolicyStartDate"] == "2023-04-01"
    assert result["originalPolicyStartSource"] == "Entered by you"

    # 2. Current Policy Period MUST be from the policy PDF
    assert result["currentPolicy"]["startDate"] == "2026-04-01"
    assert result["currentPolicy"]["endDate"] == "2027-03-31"

    # 3. Claim Date MUST be from prescription document
    assert result["claimDate"] == "2026-10-12"
    assert result["claimDateSource"] == "Admission date"

    # 4. Status MUST NOT be verified because renewal had a break
    assert result["status"] == STATUS_GAP
    assert len(result["coverageGaps"]) == 1
    assert "Break in renewal before 01 Apr 2025" in result["coverageGaps"][0]["description"]
    assert "You entered that the renewals had a break" in result["summary"]


def test_evaluate_policy_continuity_verified_when_renewed_without_break():
    """
    When renewal was without a break, status is verified from the manual inception date.
    """
    pj = {
        "declaredInceptionDate": "2023-04-01",
        "continuousRenewal": True,
        "policyStartDate": "2026-04-01",
        "policyEndDate": "2027-03-31",
        "insuranceCompany": "Care Health",
        "policyNumber": "CARE-1234",
    }
    rx = {
        "admissionDate": "2026-10-12",
        "diagnosis": "Fever",
    }

    result = evaluate_policy_continuity([{"policyId": "P1", "policyJson": pj}], rx, current_policy_id="P1")

    assert result is not None
    assert result["status"] == STATUS_VERIFIED
    assert result["originalPolicyStartDate"] == "2023-04-01"
    assert result["currentPolicy"]["startDate"] == "2026-04-01"
    assert result["currentPolicy"]["endDate"] == "2027-03-31"
    assert result["claimDate"] == "2026-10-12"
    assert result["coverageGaps"] == []
    assert "Cover has continued without interruption from 01 Apr 2023" in result["summary"]

