"""
Coverage Analysis Reports grouping tests.

Verifies that policies analysed together against one prescription come back as a
single group, while single-policy and historical reports keep working.
"""

from datetime import datetime, timezone, timedelta

import pytest
import pytest_asyncio

from tests.conftest import VALID_POLICY_JSON, VALID_RX_JSON

from app.models.analysis_report import AnalysisReport
from app.models.multi_policy_session import MultiPolicyAnalysisSession, PolicyAnalysisEntry
from app.services.report_grouping import list_report_groups, delete_report_group

USER = "user-1"
OTHER_USER = "user-2"


async def _make_report(
    *,
    user_id=USER,
    policy_id="policy-x",
    prescription_id="rx-1",
    parent_session_id="",
    overall_status="Covered",
    status="completed",
    policy_name="Policy X",
    omit_parent_field=False,
):
    report = AnalysisReport(
        user_id=user_id,
        policy_id=policy_id,
        prescription_id=prescription_id,
        parent_session_id=parent_session_id,
        status=status,
        overall_status=overall_status,
        decision_type="Automatic",
        dominance_score=88.0,
        processing_time_ms=4200,
        policy_json=dict(VALID_POLICY_JSON, policyName=policy_name, policyNumber="POL-123"),
        prescription_json=dict(VALID_RX_JSON),
    )
    await report.insert()

    if omit_parent_field:
        # Simulate a historical document written before parentSessionId existed
        await AnalysisReport.get_motor_collection().update_one(
            {"_id": report.id}, {"$unset": {"parentSessionId": ""}}
        )
    return report


async def _make_session(entries, user_id=USER, prescription_id="rx-1", status="completed"):
    session = MultiPolicyAnalysisSession(
        user_id=user_id,
        prescription_id=prescription_id,
        status=status,
        prescription_json=dict(VALID_RX_JSON),
        policy_analyses=entries,
    )
    await session.insert()
    return session


# ──────────────────────────────────────────────────────────────────────────────
# Case 1 — one prescription + one policy (classic single-policy flow)
# ──────────────────────────────────────────────────────────────────────────────

async def test_single_policy_report_is_its_own_group(db):
    report = await _make_report()

    result = await list_report_groups(USER)

    assert result["totalDocs"] == 1
    group = result["docs"][0]
    assert group["groupType"] == "single"
    assert group["groupId"] == str(report.id)
    assert group["policyCount"] == 1
    assert len(group["policyReports"]) == 1

    row = group["policyReports"][0]
    assert row["reportId"] == str(report.id)
    assert row["overallStatus"] == "Covered"
    assert row["policyName"] == "Policy X"
    assert row["policyNumber"] == "POL-123"
    assert row["reportLabel"].startswith("CR-")


# ──────────────────────────────────────────────────────────────────────────────
# Case 2 — one prescription + two policies → ONE group
# ──────────────────────────────────────────────────────────────────────────────

async def test_two_policies_collapse_into_one_group(db):
    ra = await _make_report(policy_id="pa", parent_session_id="SID", policy_name="Policy A")
    rb = await _make_report(policy_id="pb", parent_session_id="SID", policy_name="Policy B")

    session = await _make_session([
        PolicyAnalysisEntry(policyId="pa", policyName="Policy A", status="completed",
                            overallStatus="Covered", reportId=str(ra.id)),
        PolicyAnalysisEntry(policyId="pb", policyName="Policy B", status="completed",
                            overallStatus="Not Covered", reportId=str(rb.id)),
    ])

    result = await list_report_groups(USER)

    # The two reports no longer appear as separate list entries
    assert result["totalDocs"] == 1
    group = result["docs"][0]
    assert group["groupType"] == "multi"
    assert group["groupId"] == str(session.id)
    assert group["policyCount"] == 2
    assert group["completedCount"] == 2

    names = [r["policyName"] for r in group["policyReports"]]
    assert names == ["Policy A", "Policy B"]

    # Each policy keeps its own independent result and report reference
    rows = {r["policyId"]: r for r in group["policyReports"]}
    assert rows["pa"]["overallStatus"] == "Covered"
    assert rows["pb"]["overallStatus"] == "Not Covered"
    assert rows["pa"]["reportId"] == str(ra.id)
    assert rows["pb"]["reportId"] == str(rb.id)
    assert rows["pa"]["reportNumber"] != rows["pb"]["reportNumber"]


# ──────────────────────────────────────────────────────────────────────────────
# Case 3 — three or more policies
# ──────────────────────────────────────────────────────────────────────────────

async def test_three_policies_in_one_group(db):
    entries = []
    for label in ("A", "B", "C"):
        r = await _make_report(policy_id=f"p{label}", parent_session_id="SID",
                               policy_name=f"Policy {label}")
        entries.append(PolicyAnalysisEntry(
            policyId=f"p{label}", policyName=f"Policy {label}",
            status="completed", overallStatus="Covered", reportId=str(r.id),
        ))
    await _make_session(entries)

    result = await list_report_groups(USER)

    assert result["totalDocs"] == 1
    assert result["docs"][0]["policyCount"] == 3
    assert len(result["docs"][0]["policyReports"]) == 3


# ──────────────────────────────────────────────────────────────────────────────
# Case 4 — several prescriptions, each with several policies
# ──────────────────────────────────────────────────────────────────────────────

async def test_multiple_prescriptions_stay_separate_groups(db):
    for rx in ("rx-1", "rx-2"):
        entries = []
        for label in ("A", "B"):
            r = await _make_report(policy_id=f"{rx}-p{label}", prescription_id=rx,
                                   parent_session_id="SID", policy_name=f"Policy {label}")
            entries.append(PolicyAnalysisEntry(
                policyId=f"{rx}-p{label}", policyName=f"Policy {label}",
                status="completed", overallStatus="Covered", reportId=str(r.id),
            ))
        await _make_session(entries, prescription_id=rx)

    result = await list_report_groups(USER)

    assert result["totalDocs"] == 2
    prescription_ids = {g["prescriptionId"] for g in result["docs"]}
    assert prescription_ids == {"rx-1", "rx-2"}
    assert all(g["policyCount"] == 2 for g in result["docs"])

    # Groups must not borrow each other's policies
    for group in result["docs"]:
        rx = group["prescriptionId"]
        assert all(r["policyId"].startswith(rx) for r in group["policyReports"])


# ──────────────────────────────────────────────────────────────────────────────
# Case 5 — one policy failed while the others completed
# ──────────────────────────────────────────────────────────────────────────────

async def test_failed_policy_shown_inside_the_same_group(db):
    ok = await _make_report(policy_id="pa", parent_session_id="SID", policy_name="Policy A")
    await _make_session([
        PolicyAnalysisEntry(policyId="pa", policyName="Policy A", status="completed",
                            overallStatus="Covered", reportId=str(ok.id)),
        PolicyAnalysisEntry(policyId="pb", policyName="Policy B", status="failed",
                            errorMessage="gemini unavailable", failedAtStage="policy_analysis"),
    ], status="partial")

    result = await list_report_groups(USER)

    # The failed policy does not spawn its own list entry
    assert result["totalDocs"] == 1
    group = result["docs"][0]
    assert group["status"] == "partial"
    assert group["policyCount"] == 2
    assert group["completedCount"] == 1

    rows = {r["policyId"]: r for r in group["policyReports"]}
    assert rows["pb"]["status"] == "failed"
    assert rows["pb"]["errorMessage"] == "gemini unavailable"
    assert rows["pb"]["reportId"] == ""
    assert rows["pa"]["status"] == "completed"


async def test_invalid_policy_shown_inside_the_same_group(db):
    ok = await _make_report(policy_id="pa", parent_session_id="SID")
    await _make_session([
        PolicyAnalysisEntry(policyId="pa", status="completed", overallStatus="Covered",
                            reportId=str(ok.id)),
        PolicyAnalysisEntry(policyId="pb", status="invalid", overallStatus="Invalid Policy"),
    ], status="partial")

    groups = (await list_report_groups(USER))["docs"]
    assert len(groups) == 1
    rows = {r["policyId"]: r for r in groups[0]["policyReports"]}
    assert rows["pb"]["status"] == "invalid"
    assert rows["pb"]["outcome"] == "failed"


# ──────────────────────────────────────────────────────────────────────────────
# Case 6 — one policy is waiting for clarification
# ──────────────────────────────────────────────────────────────────────────────

async def test_waiting_policy_shown_inside_the_same_group(db):
    ok = await _make_report(policy_id="pa", parent_session_id="SID")
    await _make_session([
        PolicyAnalysisEntry(policyId="pa", status="completed", overallStatus="Covered",
                            reportId=str(ok.id)),
        PolicyAnalysisEntry(policyId="pb", status="waiting_for_user",
                            questions=[{"id": "hospitalization_status", "question": "Admitted?"}]),
    ], status="waiting_for_user")

    groups = (await list_report_groups(USER))["docs"]
    assert len(groups) == 1
    assert groups[0]["status"] == "waiting_for_user"

    rows = {r["policyId"]: r for r in groups[0]["policyReports"]}
    assert rows["pb"]["status"] == "waiting_for_user"
    assert rows["pb"]["questions"][0]["id"] == "hospitalization_status"
    assert rows["pa"]["overallStatus"] == "Covered"


# ──────────────────────────────────────────────────────────────────────────────
# Case 7 — historical reports
# ──────────────────────────────────────────────────────────────────────────────

async def test_historical_report_without_parent_field_still_listed(db):
    report = await _make_report(omit_parent_field=True)

    result = await list_report_groups(USER)

    assert result["totalDocs"] == 1
    group = result["docs"][0]
    assert group["groupType"] == "single"
    assert group["groupId"] == str(report.id)
    assert group["policyReports"][0]["reportId"] == str(report.id)


async def test_mixed_historical_and_grouped_reports(db):
    legacy = await _make_report(policy_id="old", omit_parent_field=True)
    single = await _make_report(policy_id="solo")

    r = await _make_report(policy_id="pa", parent_session_id="SID")
    await _make_session([
        PolicyAnalysisEntry(policyId="pa", status="completed", reportId=str(r.id)),
        PolicyAnalysisEntry(policyId="pb", status="failed"),
    ], status="partial")

    result = await list_report_groups(USER)

    # 2 standalone reports + 1 multi group = 3 entries (not 4)
    assert result["totalDocs"] == 3
    types = [g["groupType"] for g in result["docs"]]
    assert types.count("multi") == 1
    assert types.count("single") == 2

    single_ids = {g["groupId"] for g in result["docs"] if g["groupType"] == "single"}
    assert single_ids == {str(legacy.id), str(single.id)}


# ──────────────────────────────────────────────────────────────────────────────
# Ordering, pagination, isolation
# ──────────────────────────────────────────────────────────────────────────────

async def test_groups_are_newest_first(db):
    old = await _make_report(policy_id="old")
    await AnalysisReport.get_motor_collection().update_one(
        {"_id": old.id},
        {"$set": {"createdAt": datetime.now(timezone.utc) - timedelta(days=3)}},
    )
    new = await _make_report(policy_id="new")

    docs = (await list_report_groups(USER))["docs"]
    assert [d["groupId"] for d in docs] == [str(new.id), str(old.id)]


async def test_pagination_across_both_sources(db):
    for i in range(3):
        await _make_report(policy_id=f"solo-{i}")
    for i in range(2):
        r = await _make_report(policy_id=f"grouped-{i}", parent_session_id="SID")
        await _make_session([
            PolicyAnalysisEntry(policyId=f"grouped-{i}", status="completed", reportId=str(r.id)),
        ])

    first = await list_report_groups(USER, page=1, limit=2)
    second = await list_report_groups(USER, page=2, limit=2)
    third = await list_report_groups(USER, page=3, limit=2)

    assert first["totalDocs"] == 5
    assert first["totalPages"] == 3
    assert first["hasNextPage"] is True
    assert len(first["docs"]) == 2
    assert len(second["docs"]) == 2
    assert len(third["docs"]) == 1
    assert third["hasNextPage"] is False

    seen = [d["groupId"] for d in first["docs"] + second["docs"] + third["docs"]]
    assert len(set(seen)) == 5      # no duplicates, nothing skipped


async def test_other_users_analyses_are_not_listed(db):
    await _make_report(policy_id="mine")
    await _make_report(policy_id="theirs", user_id=OTHER_USER)
    r = await _make_report(policy_id="their-grouped", user_id=OTHER_USER, parent_session_id="SID")
    await _make_session(
        [PolicyAnalysisEntry(policyId="their-grouped", status="completed", reportId=str(r.id))],
        user_id=OTHER_USER,
    )

    result = await list_report_groups(USER)
    assert result["totalDocs"] == 1
    assert result["docs"][0]["policyReports"][0]["policyId"] == "mine"


async def test_grouped_reports_never_appear_as_singles(db):
    r = await _make_report(policy_id="pa", parent_session_id="SID")
    await _make_session([
        PolicyAnalysisEntry(policyId="pa", status="completed", reportId=str(r.id)),
    ])

    docs = (await list_report_groups(USER))["docs"]
    assert len(docs) == 1
    assert docs[0]["groupType"] == "multi"


# ──────────────────────────────────────────────────────────────────────────────
# Deletion
# ──────────────────────────────────────────────────────────────────────────────

async def test_deleting_a_group_removes_all_its_reports(db):
    ra = await _make_report(policy_id="pa", parent_session_id="SID")
    rb = await _make_report(policy_id="pb", parent_session_id="SID")
    session = await _make_session([
        PolicyAnalysisEntry(policyId="pa", status="completed", reportId=str(ra.id)),
        PolicyAnalysisEntry(policyId="pb", status="completed", reportId=str(rb.id)),
    ])

    result = await delete_report_group(USER, str(session.id))

    assert result == {"deletedGroups": 1, "deletedReports": 2}
    assert await AnalysisReport.find(AnalysisReport.user_id == USER).count() == 0
    assert await MultiPolicyAnalysisSession.find(
        MultiPolicyAnalysisSession.user_id == USER
    ).count() == 0
    assert (await list_report_groups(USER))["totalDocs"] == 0


async def test_deleting_a_single_group_removes_one_report(db):
    report = await _make_report()
    keep = await _make_report(policy_id="keep")

    result = await delete_report_group(USER, str(report.id))

    assert result == {"deletedGroups": 1, "deletedReports": 1}
    remaining = await AnalysisReport.find(AnalysisReport.user_id == USER).to_list()
    assert [str(r.id) for r in remaining] == [str(keep.id)]


async def test_cannot_delete_another_users_group(db):
    report = await _make_report(user_id=OTHER_USER)
    result = await delete_report_group(USER, str(report.id))

    assert result == {"deletedGroups": 0, "deletedReports": 0}
    assert await AnalysisReport.find(AnalysisReport.user_id == OTHER_USER).count() == 1


async def test_delete_unknown_group_is_a_noop(db):
    result = await delete_report_group(USER, "000000000000000000000000")
    assert result == {"deletedGroups": 0, "deletedReports": 0}


# ──────────────────────────────────────────────────────────────────────────────
# Group payload shape
# ──────────────────────────────────────────────────────────────────────────────

async def test_group_exposes_prescription_context(db):
    r = await _make_report(policy_id="pa", parent_session_id="SID")
    await _make_session([
        PolicyAnalysisEntry(policyId="pa", status="completed", reportId=str(r.id)),
    ], prescription_id="rx-42")

    group = (await list_report_groups(USER))["docs"][0]

    assert group["prescriptionId"] == "rx-42"
    assert group["prescription"]["diagnosis"] == "Acute appendicitis"
    assert group["prescription"]["patientName"] == "Test Patient"
    assert group["createdAt"]
    assert group["reportIds"] == [str(r.id)]


async def test_placeholder_values_are_cleaned(db):
    report = await _make_report()
    await AnalysisReport.get_motor_collection().update_one(
        {"_id": report.id},
        {"$set": {"prescriptionJson.patientName": "Unknown",
                  "policyJson.policyNumber": "N/A"}},
    )

    group = (await list_report_groups(USER))["docs"][0]
    assert group["prescription"]["patientName"] == ""
    assert group["policyReports"][0]["policyNumber"] == ""


async def test_empty_state(db):
    result = await list_report_groups(USER)
    assert result["docs"] == []
    assert result["totalDocs"] == 0
    assert result["hasNextPage"] is False


# ──────────────────────────────────────────────────────────────────────────────
# End-to-end: orchestrator output feeds the grouped list
# ──────────────────────────────────────────────────────────────────────────────

async def test_multi_policy_run_produces_one_grouped_entry(db, monkeypatch):
    """
    Run the multi-policy orchestrator against real documents and confirm the
    reports page shows ONE grouped entry, not one entry per policy.
    """
    import app.services.multi_policy_orchestrator as mpo
    from app.models.policy import Policy

    policies = []
    for label in ("A", "B", "C"):
        policy = Policy(
            user_id=USER,
            policy_name=f"Policy {label}",
            insurance_company="TestInsurer",
            grid_fs_file_id=f"file-{label}",
            extracted_policy_text="CACHED POLICY TEXT",
            extracted_policy_json=dict(VALID_POLICY_JSON),
        )
        await policy.insert()
        policies.append(policy)

    async def fake_rx_text(ctx, user_id):
        return "PRESCRIPTION TEXT"

    async def fake_rx_json(text):
        return {"extractedJson": dict(VALID_RX_JSON)}

    async def fake_persist(ctx, user_id, rx_text, prescription_json, fallback_original_name=""):
        return None

    monkeypatch.setattr(mpo, "_load_prescription_text", fake_rx_text)
    monkeypatch.setattr(mpo, "extract_prescription_details", fake_rx_json)
    monkeypatch.setattr(mpo, "_persist_prescription_extraction", fake_persist)

    # Policy C fails; A and B persist a real report exactly as the pipeline does.
    async def fake_run_single(session, *, user_id, validation, policy_ctx, policy_text,
                              is_manual_rx, start_time, background_tasks=None):
        if policy_ctx.policy_doc.policy_name.endswith("C"):
            raise RuntimeError("coverage analysis failed")
        report = AnalysisReport(
            user_id=session.user_id,
            policy_id=session.policy_id,
            prescription_id=session.prescription_id,
            session_id=str(session.id),
            parent_session_id=session.parent_session_id or "",
            status="completed",
            overall_status="Covered",
            decision_type="Automatic",
            dominance_score=75.0,
            processing_time_ms=3300,
            policy_json=validation["validatedPolicyJson"],
            prescription_json=validation["validatedPrescriptionJson"],
        )
        await report.insert()
        result = report.dict(by_alias=True)
        result["_id"] = str(report.id)
        result["status"] = "complete"
        return result

    monkeypatch.setattr(mpo, "run_single_policy_analysis", fake_run_single)

    session_payload = await mpo.start_multi_policy_analysis(
        USER, "rx-99", [str(p.id) for p in policies]
    )
    assert session_payload["status"] == "partial"

    # Three reports would previously have produced three list entries
    assert await AnalysisReport.find(AnalysisReport.user_id == USER).count() == 2

    result = await list_report_groups(USER)

    assert result["totalDocs"] == 1
    group = result["docs"][0]
    assert group["groupType"] == "multi"
    assert group["groupId"] == session_payload["sessionId"]
    assert group["prescriptionId"] == "rx-99"
    assert group["policyCount"] == 3
    assert group["completedCount"] == 2
    assert len(group["reportIds"]) == 2

    rows = {r["policyName"]: r for r in group["policyReports"]}
    assert set(rows) == {"Policy A", "Policy B", "Policy C"}
    assert rows["Policy A"]["reportId"] and rows["Policy A"]["reportLabel"].startswith("CR-")
    assert rows["Policy B"]["overallStatus"] == "Covered"
    assert rows["Policy C"]["status"] == "failed"
    assert rows["Policy C"]["reportId"] == ""
    # Report numbers stay unique per policy
    assert rows["Policy A"]["reportNumber"] != rows["Policy B"]["reportNumber"]
