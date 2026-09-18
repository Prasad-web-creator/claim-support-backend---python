import asyncio

import pytest
import pytest_asyncio

from tests.conftest import VALID_POLICY_JSON, VALID_RX_JSON

import app.services.multi_policy_orchestrator as mpo
from app.models.multi_policy_session import PolicyAnalysisEntry
from app.models.policy import Policy
from app.models.prescription import Prescription
from app.services.coverage.policy_comparison import build_comparison_summary, classify_outcome

USER = "user-1"
OTHER_USER = "user-2"


# ──────────────────────────────────────────────────────────────────────────────
# Harness
# ──────────────────────────────────────────────────────────────────────────────

class Harness:
    """
    Creates real Policy/Prescription documents and stubs only the expensive
    leaf operations (file fetch, OCR, LLM extraction, coverage analysis), so
    tests can assert exactly what work was and was not performed.
    """

    def __init__(self):
        self.rx_text_extractions = 0
        self.rx_json_extractions = 0
        self.policy_doc_extractions = []
        self.policy_json_extractions = []
        self.analyses = []
        self.behaviour = {}          # label -> "ok" | "fail" | "clarify" | "invalid"
        self.ids = {}                # label -> policy id
        self.labels = {}             # policy id -> label
        self.rx_id = ""

    def id(self, label: str) -> str:
        return self.ids[label]

    def label(self, policy_id: str) -> str:
        return self.labels.get(str(policy_id), str(policy_id))

    def entry(self, result: dict, label: str) -> dict:
        target = self.id(label)
        return next(e for e in result["policyAnalyses"] if e["policyId"] == target)

    def runs_for(self, label: str) -> list:
        return [a for a in self.analyses if a["label"] == label]


@pytest_asyncio.fixture
async def harness(monkeypatch, db):
    h = Harness()

    async def add_policy(label, cached=False, user=USER):
        policy = Policy(
            user_id=user,
            policy_name=f"Policy {label}",
            insurance_company="TestInsurer",
            grid_fs_file_id=f"file-{label}",
            extracted_policy_text="CACHED POLICY TEXT" if cached else "",
            extracted_policy_json=dict(VALID_POLICY_JSON) if cached else {},
        )
        await policy.insert()
        h.ids[label] = str(policy.id)
        h.labels[str(policy.id)] = label
        return str(policy.id)

    async def add_prescription(is_manual=False, cached=False):
        rx = Prescription(
            user_id=USER,
            is_manual=is_manual,
            grid_fs_file_id="" if is_manual else "file-rx",
            extracted_prescription_text=("CACHED RX TEXT" if cached else ("I have had a fever" if is_manual else "")),
            extracted_prescription_json=dict(VALID_RX_JSON) if cached else {},
        )
        await rx.insert()
        h.rx_id = str(rx.id)
        return str(rx.id)

    h.add_policy = add_policy
    h.add_prescription = add_prescription

    # ─── stub the expensive leaves ───────────────────────────────────────────
    async def fake_load_rx_text(ctx, user_id):
        h.rx_text_extractions += 1
        return "PRESCRIPTION TEXT"

    monkeypatch.setattr(mpo, "_load_prescription_text", fake_load_rx_text)

    async def fake_extract_rx(text):
        h.rx_json_extractions += 1
        return {"extractedJson": dict(VALID_RX_JSON)}

    monkeypatch.setattr(mpo, "extract_prescription_details", fake_extract_rx)

    async def fake_process_document(doc_id, user_id, doc_label="Document"):
        h.policy_doc_extractions.append(doc_id)
        return "POLICY TEXT"

    monkeypatch.setattr(mpo, "_process_document", fake_process_document)

    async def fake_extract_policy(text):
        h.policy_json_extractions.append(text)
        return {"extractedJson": dict(VALID_POLICY_JSON)}

    monkeypatch.setattr(mpo, "extract_policy_details", fake_extract_policy)

    # ─── stub the shared single-policy core ──────────────────────────────────
    async def fake_run_single(session, *, user_id, validation, policy_ctx, policy_text,
                              is_manual_rx, start_time, background_tasks=None):
        await asyncio.sleep(0)  # force interleaving under gather
        pid = str(session.policy_id)
        label = h.label(pid)
        h.analyses.append({
            "label": label,
            "policyId": pid,
            "sessionId": str(session.id),
            "parentSessionId": session.parent_session_id,
            "policyJson": validation["validatedPolicyJson"],
            "prescriptionJson": validation["validatedPrescriptionJson"],
            "isManual": is_manual_rx,
            "resumed": False,
        })
        mode = h.behaviour.get(label, "ok")
        if mode == "fail":
            raise RuntimeError(f"coverage analysis exploded for {label}")
        if mode == "clarify":
            return {
                "status": "needs_clarification",
                "sessionId": str(session.id),
                "questions": [{"id": "hospitalization_status", "question": "Admitted?"}],
            }
        if mode == "invalid":
            return {"status": "complete", "overallStatus": "Invalid Policy"}
        return {
            "status": "complete",
            "_id": f"report-{label}",
            "overallStatus": "Covered",
            "decisionType": "Automatic",
            "dominanceScore": 80.0,
        }

    monkeypatch.setattr(mpo, "run_single_policy_analysis", fake_run_single)

    async def fake_resume(session_id, user_id, answers, background_tasks=None):
        label = next((a["label"] for a in h.analyses if a["sessionId"] == session_id), "?")
        h.analyses.append({
            "label": label, "policyId": h.ids.get(label, ""),
            "sessionId": session_id, "resumed": True,
        })
        return {
            "status": "complete",
            "_id": f"report-{label}",
            "overallStatus": "Covered",
            "decisionType": "Automatic",
            "dominanceScore": 90.0,
        }

    monkeypatch.setattr(mpo, "resume_analysis_session", fake_resume)

    return h


# ──────────────────────────────────────────────────────────────────────────────
# Case 1 — single policy still behaves exactly as before
# ──────────────────────────────────────────────────────────────────────────────

async def test_case1_single_policy_via_multi_endpoint(harness):
    await harness.add_policy("A")
    await harness.add_prescription()

    result = await mpo.start_multi_policy_analysis(USER, harness.rx_id, [harness.id("A")])

    assert result["status"] == "completed"
    assert result["policyCount"] == 1
    entry = harness.entry(result, "A")
    assert entry["status"] == "completed"
    assert entry["reportId"] == "report-A"
    assert harness.rx_json_extractions == 1


def test_case1b_single_policy_pipeline_signature_unchanged():
    """The single-policy entrypoint keeps its public contract."""
    import inspect
    from app.services.analysis_interactive_orchestrator import start_analysis_session

    params = list(inspect.signature(start_analysis_session).parameters)
    assert params == [
        "user_id", "prescription_id", "policy_file_id", "policy_doc_id", "background_tasks"
    ]


# ──────────────────────────────────────────────────────────────────────────────
# Case 2 — two policies complete independently
# ──────────────────────────────────────────────────────────────────────────────

async def test_case2_two_policies_complete_independently(harness):
    await harness.add_policy("A")
    await harness.add_policy("B")
    await harness.add_prescription()

    result = await mpo.start_multi_policy_analysis(
        USER, harness.rx_id, [harness.id("A"), harness.id("B")]
    )

    assert result["status"] == "completed"
    assert [e["policyId"] for e in result["policyAnalyses"]] == [harness.id("A"), harness.id("B")]
    assert all(e["status"] == "completed" for e in result["policyAnalyses"])

    # Every policy got its own child session and its own report
    assert len({e["sessionId"] for e in result["policyAnalyses"]}) == 2
    assert {e["reportId"] for e in result["policyAnalyses"]} == {"report-A", "report-B"}


async def test_case2b_policy_data_is_not_shared_between_analyses(harness):
    """Policy A's clauses/exclusions must never reach Policy B's analysis."""
    await harness.add_policy("A")
    await harness.add_policy("B")
    await harness.add_prescription()

    await mpo.start_multi_policy_analysis(USER, harness.rx_id, [harness.id("A"), harness.id("B")])

    a, b = harness.analyses[0], harness.analyses[1]
    assert a["policyJson"] is not b["policyJson"]
    assert a["prescriptionJson"] is not b["prescriptionJson"]

    # Mutating one analysis' prescription JSON must not affect the other
    a["prescriptionJson"]["isPreExisting"] = True
    assert "isPreExisting" not in b["prescriptionJson"]


async def test_case2c_child_sessions_persisted_with_parent_link(harness, db):
    from app.models.analysis_session import AnalysisSession

    await harness.add_policy("A")
    await harness.add_policy("B")
    await harness.add_prescription()

    result = await mpo.start_multi_policy_analysis(
        USER, harness.rx_id, [harness.id("A"), harness.id("B")]
    )

    children = await AnalysisSession.find(
        AnalysisSession.parent_session_id == result["sessionId"]
    ).to_list()
    assert len(children) == 2
    assert {c.policy_id for c in children} == {harness.id("A"), harness.id("B")}
    assert all(c.prescription_id == harness.rx_id for c in children)
    assert all(c.user_id == USER for c in children)


# ──────────────────────────────────────────────────────────────────────────────
# Case 3 — prescription extracted only once
# ──────────────────────────────────────────────────────────────────────────────

async def test_case3_prescription_extracted_once_for_many_policies(harness):
    for label in ("A", "B", "C"):
        await harness.add_policy(label)
    await harness.add_prescription()

    await mpo.start_multi_policy_analysis(
        USER, harness.rx_id, [harness.id("A"), harness.id("B"), harness.id("C")]
    )

    assert harness.rx_text_extractions == 1
    assert harness.rx_json_extractions == 1
    assert len(harness.analyses) == 3


async def test_case3b_cached_prescription_skips_extraction_entirely(harness):
    await harness.add_policy("A")
    await harness.add_policy("B")
    await harness.add_prescription(cached=True)

    result = await mpo.start_multi_policy_analysis(
        USER, harness.rx_id, [harness.id("A"), harness.id("B")]
    )

    assert harness.rx_text_extractions == 0
    assert harness.rx_json_extractions == 0
    assert result["status"] == "completed"


# ──────────────────────────────────────────────────────────────────────────────
# Case 4 — only the uncached policy is extracted
# ──────────────────────────────────────────────────────────────────────────────

async def test_case4_only_uncached_policy_is_extracted(harness):
    await harness.add_policy("CACHED", cached=True)
    await harness.add_policy("FRESH", cached=False)
    await harness.add_prescription()

    await mpo.start_multi_policy_analysis(
        USER, harness.rx_id, [harness.id("CACHED"), harness.id("FRESH")]
    )

    assert harness.policy_doc_extractions == ["file-FRESH"]
    assert len(harness.policy_json_extractions) == 1


# ──────────────────────────────────────────────────────────────────────────────
# Case 5 — one policy fails, others continue
# ──────────────────────────────────────────────────────────────────────────────

async def test_case5_one_policy_failure_is_isolated(harness):
    for label in ("A", "B", "C"):
        await harness.add_policy(label)
    await harness.add_prescription()
    harness.behaviour["B"] = "fail"

    result = await mpo.start_multi_policy_analysis(
        USER, harness.rx_id, [harness.id("A"), harness.id("B"), harness.id("C")]
    )

    assert harness.entry(result, "A")["status"] == "completed"
    assert harness.entry(result, "C")["status"] == "completed"
    failed = harness.entry(result, "B")
    assert failed["status"] == "failed"
    assert "exploded" in failed["errorMessage"]
    assert failed["failedAtStage"] == "policy_analysis"

    # Parent reflects the mix rather than collapsing to a single outcome
    assert result["status"] == "partial"
    assert harness.entry(result, "A")["reportId"] == "report-A"


async def test_case5b_all_policies_failing_marks_session_failed(harness):
    await harness.add_policy("A")
    await harness.add_policy("B")
    await harness.add_prescription()
    harness.behaviour["A"] = "fail"
    harness.behaviour["B"] = "fail"

    result = await mpo.start_multi_policy_analysis(
        USER, harness.rx_id, [harness.id("A"), harness.id("B")]
    )
    assert result["status"] == "failed"


async def test_case5c_failed_child_session_marked_failed(harness):
    from app.models.analysis_session import AnalysisSession

    await harness.add_policy("A")
    await harness.add_prescription()
    harness.behaviour["A"] = "fail"

    result = await mpo.start_multi_policy_analysis(USER, harness.rx_id, [harness.id("A")])

    children = await AnalysisSession.find(
        AnalysisSession.parent_session_id == result["sessionId"]
    ).to_list()
    assert len(children) == 1
    assert children[0].status == "failed"


# ──────────────────────────────────────────────────────────────────────────────
# Case 6 — one policy needs clarification, only that one pauses
# ──────────────────────────────────────────────────────────────────────────────

async def test_case6_only_clarifying_policy_pauses(harness):
    for label in ("A", "B", "C"):
        await harness.add_policy(label)
    await harness.add_prescription()
    harness.behaviour["B"] = "clarify"

    result = await mpo.start_multi_policy_analysis(
        USER, harness.rx_id, [harness.id("A"), harness.id("B"), harness.id("C")]
    )

    assert harness.entry(result, "A")["status"] == "completed"
    assert harness.entry(result, "C")["status"] == "completed"
    waiting = harness.entry(result, "B")
    assert waiting["status"] == "waiting_for_user"
    assert waiting["questions"][0]["id"] == "hospitalization_status"
    assert result["status"] == "waiting_for_user"


# ──────────────────────────────────────────────────────────────────────────────
# Case 7 — answering resumes only the affected policy
# ──────────────────────────────────────────────────────────────────────────────

async def test_case7_resume_affects_only_that_policy(harness):
    for label in ("A", "B", "C"):
        await harness.add_policy(label)
    await harness.add_prescription()
    harness.behaviour["B"] = "clarify"

    started = await mpo.start_multi_policy_analysis(
        USER, harness.rx_id, [harness.id("A"), harness.id("B"), harness.id("C")]
    )
    runs_before = len(harness.analyses)

    resumed = await mpo.resume_policy_analysis(
        started["sessionId"], harness.id("B"), USER,
        {"hospitalization_status": "Yes, admitted"},
    )

    entry_b = harness.entry(resumed, "B")
    assert entry_b["status"] == "completed"
    assert entry_b["reportId"] == "report-B"
    assert resumed["status"] == "completed"

    # Exactly one additional pipeline invocation — A and C were not re-run
    assert len(harness.analyses) == runs_before + 1
    assert harness.analyses[-1]["resumed"] is True
    assert harness.analyses[-1]["label"] == "B"
    assert len(harness.runs_for("A")) == 1
    assert len(harness.runs_for("C")) == 1


async def test_case7b_resume_rejects_policy_not_waiting(harness):
    await harness.add_policy("A")
    await harness.add_prescription()
    started = await mpo.start_multi_policy_analysis(USER, harness.rx_id, [harness.id("A")])

    with pytest.raises(ValueError, match="not waiting for clarification"):
        await mpo.resume_policy_analysis(started["sessionId"], harness.id("A"), USER, {"x": "y"})


async def test_case7c_resume_rejects_unknown_policy(harness):
    await harness.add_policy("A")
    await harness.add_prescription()
    started = await mpo.start_multi_policy_analysis(USER, harness.rx_id, [harness.id("A")])

    with pytest.raises(ValueError, match="not part of this analysis session"):
        await mpo.resume_policy_analysis(started["sessionId"], "000000000000000000000000", USER, {})


# ──────────────────────────────────────────────────────────────────────────────
# Case 8 — invalid policy is isolated
# ──────────────────────────────────────────────────────────────────────────────

async def test_case8_invalid_policy_isolated(harness):
    await harness.add_policy("A")
    await harness.add_policy("BAD")
    await harness.add_prescription()
    harness.behaviour["BAD"] = "invalid"

    result = await mpo.start_multi_policy_analysis(
        USER, harness.rx_id, [harness.id("A"), harness.id("BAD")]
    )

    bad = harness.entry(result, "BAD")
    assert bad["status"] == "invalid"
    assert bad["overallStatus"] == "Invalid Policy"
    assert bad["reportId"] == ""       # invalid reports are never persisted
    assert harness.entry(result, "A")["status"] == "completed"
    assert result["status"] == "partial"


async def test_case8b_nonexistent_policy_rejects_whole_request(harness):
    await harness.add_policy("A")
    await harness.add_prescription()

    with pytest.raises(PermissionError, match="not found or not accessible"):
        await mpo.start_multi_policy_analysis(
            USER, harness.rx_id, [harness.id("A"), "000000000000000000000000"]
        )


async def test_case8c_malformed_policy_id_rejected(harness):
    await harness.add_prescription()
    with pytest.raises(PermissionError):
        await mpo.start_multi_policy_analysis(USER, harness.rx_id, ["not-an-object-id"])


# ──────────────────────────────────────────────────────────────────────────────
# Case 9 — duplicate policy ids
# ──────────────────────────────────────────────────────────────────────────────

def test_case9_duplicate_policy_ids_are_normalized():
    from app.api.routes.analysis import MultiAnalysisRequest

    req = MultiAnalysisRequest(
        prescriptionPath="rx-1",
        policyIds=["policy-a", "policy-b", "policy-a", " policy-b "],
    )
    assert req.policyIds == ["policy-a", "policy-b"]


def test_case9b_empty_policy_list_rejected():
    from app.api.routes.analysis import MultiAnalysisRequest
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="At least one policy"):
        MultiAnalysisRequest(prescriptionPath="rx-1", policyIds=[])


def test_case9c_too_many_policies_rejected():
    from app.api.routes.analysis import MultiAnalysisRequest
    from app.core.config import get_settings
    from pydantic import ValidationError

    too_many = [f"policy-{i}" for i in range(get_settings().MAX_POLICIES_PER_ANALYSIS + 1)]
    with pytest.raises(ValidationError, match="maximum of"):
        MultiAnalysisRequest(prescriptionPath="rx-1", policyIds=too_many)


async def test_case9d_one_entry_means_one_analysis(harness):
    """Even if dedupe were bypassed upstream, one entry == one analysis."""
    await harness.add_policy("A")
    await harness.add_prescription()

    result = await mpo.start_multi_policy_analysis(USER, harness.rx_id, [harness.id("A")])
    assert result["policyCount"] == 1
    assert len(harness.runs_for("A")) == 1


# ──────────────────────────────────────────────────────────────────────────────
# Case 10 — retry / resume creates no duplicates
# ──────────────────────────────────────────────────────────────────────────────

async def test_case10_retry_reruns_only_the_failed_policy(harness):
    await harness.add_policy("A")
    await harness.add_policy("B")
    await harness.add_prescription()
    harness.behaviour["B"] = "fail"

    started = await mpo.start_multi_policy_analysis(
        USER, harness.rx_id, [harness.id("A"), harness.id("B")]
    )
    assert started["status"] == "partial"
    runs_before = len(harness.analyses)

    harness.behaviour["B"] = "ok"   # transient failure cleared
    retried = await mpo.retry_policy_analysis(started["sessionId"], harness.id("B"), USER)

    assert harness.entry(retried, "B")["status"] == "completed"
    assert harness.entry(retried, "A")["reportId"] == "report-A"
    assert retried["status"] == "completed"
    assert len(harness.analyses) == runs_before + 1      # only B re-ran
    assert len(harness.runs_for("A")) == 1


async def test_case10b_retry_of_completed_policy_is_a_noop(harness):
    """Idempotency: retrying a finished policy must not create a second analysis."""
    await harness.add_policy("A")
    await harness.add_prescription()
    started = await mpo.start_multi_policy_analysis(USER, harness.rx_id, [harness.id("A")])
    runs_before = len(harness.analyses)

    retried = await mpo.retry_policy_analysis(started["sessionId"], harness.id("A"), USER)

    assert len(harness.analyses) == runs_before
    assert harness.entry(retried, "A")["reportId"] == "report-A"


async def test_case10c_retry_reuses_cached_prescription(harness):
    await harness.add_policy("A")
    await harness.add_prescription()
    harness.behaviour["A"] = "fail"

    started = await mpo.start_multi_policy_analysis(USER, harness.rx_id, [harness.id("A")])
    rx_runs_before = harness.rx_json_extractions

    harness.behaviour["A"] = "ok"
    await mpo.retry_policy_analysis(started["sessionId"], harness.id("A"), USER)

    # The retry reuses the parent's stored prescription extraction
    assert harness.rx_json_extractions == rx_runs_before


async def test_case10d_get_session_is_safe_to_poll(harness):
    await harness.add_policy("A")
    await harness.add_prescription()
    started = await mpo.start_multi_policy_analysis(USER, harness.rx_id, [harness.id("A")])
    runs_before = len(harness.analyses)

    for _ in range(3):
        snapshot = await mpo.get_multi_policy_session(started["sessionId"], USER)
        assert snapshot["status"] == "completed"

    assert len(harness.analyses) == runs_before


async def test_case10e_one_child_session_per_policy(harness):
    await harness.add_policy("A")
    await harness.add_policy("B")
    await harness.add_prescription()

    result = await mpo.start_multi_policy_analysis(
        USER, harness.rx_id, [harness.id("A"), harness.id("B")]
    )

    assert len({e["sessionId"] for e in result["policyAnalyses"]}) == 2
    assert len(harness.analyses) == 2
    assert all(a["parentSessionId"] == result["sessionId"] for a in harness.analyses)


# ──────────────────────────────────────────────────────────────────────────────
# Case 11 — manual prescription behaviour preserved
# ──────────────────────────────────────────────────────────────────────────────

async def test_case11_manual_prescription_flags_preserved(harness):
    await harness.add_policy("A")
    await harness.add_policy("B")
    await harness.add_prescription(is_manual=True)

    result = await mpo.start_multi_policy_analysis(
        USER, harness.rx_id, [harness.id("A"), harness.id("B")]
    )

    assert result["isManualPrescription"] is True
    assert len(harness.analyses) == 2
    for record in harness.analyses:
        rx = record["prescriptionJson"]
        assert rx["isManual"] is True
        assert rx["prescriptionSource"] == "Self-entered Prescription"
        assert rx["visitDate"]                     # defaulted when absent
        assert record["isManual"] is True


def test_case11b_manual_defaults_helper_matches_single_policy_flow():
    from app.services.analysis_interactive_orchestrator import _apply_manual_prescription_defaults

    rx = {"diagnosis": "Fever"}
    out = _apply_manual_prescription_defaults(rx, "I have had a fever for two days")

    assert out["isManual"] is True
    assert out["prescriptionSource"] == "Self-entered Prescription"
    assert out["manualText"] == "I have had a fever for two days"
    assert out["visitDate"] == out["consultationDate"]


# ──────────────────────────────────────────────────────────────────────────────
# Case 12 — existing single-policy API unchanged
# ──────────────────────────────────────────────────────────────────────────────

def test_case12_single_policy_request_contract_unchanged():
    from app.api.routes.analysis import AnalysisRequest
    from pydantic import ValidationError

    ok = AnalysisRequest(prescriptionPath="rx-1", policyId="policy-a")
    assert ok.policyId == "policy-a" and ok.policyPath is None

    ok2 = AnalysisRequest(prescriptionPath="rx-1", policyPath="file-1")
    assert ok2.policyPath == "file-1"

    with pytest.raises(ValidationError):
        AnalysisRequest(prescriptionPath="rx-1")
    with pytest.raises(ValidationError):
        AnalysisRequest(prescriptionPath="rx-1", policyId="a", policyPath="b")


def test_case12b_existing_routes_still_registered():
    from app.api.router import api_router

    paths = {r.path for r in api_router.routes}
    for legacy in (
        "/api/analysis/start",
        "/api/analysis/{session_id}/answer",
        "/api/analysis/{report_id}",
        "/api/analysis",
    ):
        assert legacy in paths

    for new in (
        "/api/analysis/start-multi",
        "/api/analysis/multi/{session_id}",
        "/api/analysis/multi/{session_id}/{policy_id}/answer",
        "/api/analysis/multi/{session_id}/{policy_id}/retry",
    ):
        assert new in paths


def test_case12c_multi_routes_registered_before_catchall():
    """/analysis/multi/... must not be shadowed by /analysis/{report_id}."""
    from app.api.router import api_router

    paths = [r.path for r in api_router.routes]
    assert paths.index("/api/analysis/multi/{session_id}") < paths.index("/api/analysis/{report_id}")


def test_case12d_legacy_models_accept_documents_without_new_field():
    """Historical sessions/reports stored before this feature stay readable."""
    from app.models.analysis_session import AnalysisSession
    from app.models.analysis_report import AnalysisReport

    legacy_session = AnalysisSession(userId="u", policyId="p", prescriptionId="rx")
    assert legacy_session.parent_session_id == ""

    legacy_report = AnalysisReport(userId="u", policyId="p", prescriptionId="rx")
    assert legacy_report.parent_session_id == ""


def test_case12e_new_field_is_optional_on_load():
    """A document read from Mongo without parentSessionId must still validate."""
    from app.models.analysis_session import AnalysisSession

    session = AnalysisSession.model_validate({
        "userId": "u", "policyId": "p", "prescriptionId": "rx", "status": "completed",
    })
    assert session.parent_session_id == ""


# ──────────────────────────────────────────────────────────────────────────────
# Authorization
# ──────────────────────────────────────────────────────────────────────────────

async def test_cannot_analyze_another_users_policy(harness):
    await harness.add_policy("A")
    await harness.add_policy("FOREIGN", user=OTHER_USER)
    await harness.add_prescription()

    with pytest.raises(PermissionError):
        await mpo.start_multi_policy_analysis(
            USER, harness.rx_id, [harness.id("A"), harness.id("FOREIGN")]
        )


async def test_no_analysis_runs_when_authorization_fails(harness):
    await harness.add_policy("A")
    await harness.add_policy("FOREIGN", user=OTHER_USER)
    await harness.add_prescription()

    with pytest.raises(PermissionError):
        await mpo.start_multi_policy_analysis(
            USER, harness.rx_id, [harness.id("A"), harness.id("FOREIGN")]
        )
    # Authorization is checked up-front: nothing was analysed or extracted
    assert harness.analyses == []
    assert harness.rx_json_extractions == 0


async def test_cannot_read_another_users_session(harness):
    await harness.add_policy("A")
    await harness.add_prescription()
    started = await mpo.start_multi_policy_analysis(USER, harness.rx_id, [harness.id("A")])

    with pytest.raises(PermissionError):
        await mpo.get_multi_policy_session(started["sessionId"], OTHER_USER)
    with pytest.raises(PermissionError):
        await mpo.resume_policy_analysis(started["sessionId"], harness.id("A"), OTHER_USER, {})
    with pytest.raises(PermissionError):
        await mpo.retry_policy_analysis(started["sessionId"], harness.id("A"), OTHER_USER)


# ──────────────────────────────────────────────────────────────────────────────
# Concurrency
# ──────────────────────────────────────────────────────────────────────────────

async def test_concurrency_is_bounded(monkeypatch, harness):
    from app.core.config import get_settings

    limit = get_settings().MULTI_POLICY_MAX_CONCURRENCY
    state = {"current": 0, "peak": 0}
    original = mpo.run_single_policy_analysis

    async def counting_run(session, **kwargs):
        state["current"] += 1
        state["peak"] = max(state["peak"], state["current"])
        try:
            await asyncio.sleep(0.01)
            return await original(session, **kwargs)
        finally:
            state["current"] -= 1

    monkeypatch.setattr(mpo, "run_single_policy_analysis", counting_run)

    labels = ["P0", "P1", "P2", "P3", "P4"]
    for label in labels:
        await harness.add_policy(label)
    await harness.add_prescription()

    await mpo.start_multi_policy_analysis(
        USER, harness.rx_id, [harness.id(label) for label in labels]
    )

    assert state["peak"] <= limit
    assert state["peak"] > 0


async def test_llm_semaphore_respects_configured_limit():
    from app.core.config import get_settings
    from app.services.llm import ai_client

    ai_client._llm_semaphore = None
    sem = ai_client.get_llm_semaphore()
    assert sem._value == get_settings().AI_MAX_CONCURRENT_REQUESTS
    assert ai_client.get_llm_semaphore() is sem


async def test_concurrent_report_numbers_are_unique(db):
    """The counter must not hand the same sequence to parallel analyses."""
    from app.models.counter import Counter

    results = await asyncio.gather(
        *[Counter.get_next_sequence(USER, "AnalysisReport") for _ in range(10)]
    )
    assert sorted(results) == list(range(1, 11))


# ──────────────────────────────────────────────────────────────────────────────
# Status roll-up + comparison summary
# ──────────────────────────────────────────────────────────────────────────────

def _entry(policy_id, status, overall="", score=0.0):
    return PolicyAnalysisEntry(
        policyId=policy_id, status=status, overallStatus=overall, dominanceScore=score
    )


@pytest.mark.parametrize("statuses,expected", [
    (["completed", "completed"], "completed"),
    (["completed", "failed"], "partial"),
    (["failed", "failed"], "failed"),
    (["completed", "waiting_for_user", "failed"], "waiting_for_user"),
    (["completed", "queued"], "analyzing"),
    (["manual_review_required", "completed"], "completed"),
    (["invalid", "completed"], "partial"),
    (["invalid", "failed"], "failed"),
])
def test_parent_status_rollup(statuses, expected):
    entries = [_entry(f"p{i}", s) for i, s in enumerate(statuses)]
    assert mpo._derive_parent_status(entries) == expected


def test_parent_status_rollup_empty():
    assert mpo._derive_parent_status([]) == "failed"


@pytest.mark.parametrize("status,overall,expected", [
    ("completed", "Covered", "covered"),
    ("completed", "Partially Covered - Sub-Limit Applied", "partial"),
    ("completed", "Rejected - Waiting Period Active", "not_covered"),
    ("completed", "Not Covered", "not_covered"),
    ("manual_review_required", "", "review"),
    ("waiting_for_user", "", "waiting_for_user"),
    ("failed", "", "failed"),
    ("invalid", "Invalid Policy", "failed"),
    ("queued", "", "pending"),
])
def test_outcome_classification(status, overall, expected):
    assert classify_outcome(status, overall) == expected


def test_comparison_summary_aggregates_without_merging():
    entries = [
        _entry("a", "completed", "Covered", 90.0),
        _entry("b", "completed", "Not Covered", 10.0),
        _entry("c", "waiting_for_user"),
    ]
    summary = build_comparison_summary(entries)

    assert summary["totalPolicies"] == 3
    assert summary["counts"]["covered"] == 1
    assert summary["counts"]["not_covered"] == 1
    assert summary["counts"]["waiting_for_user"] == 1
    assert summary["bestPolicyId"] == "a"

    # Aggregation must not rewrite any individual decision
    rows = {r["policyId"]: r for r in summary["results"]}
    assert rows["b"]["overallStatus"] == "Not Covered"
    assert rows["a"]["overallStatus"] == "Covered"


def test_comparison_summary_picks_highest_score_among_covered():
    entries = [
        _entry("a", "completed", "Covered", 55.0),
        _entry("b", "completed", "Covered", 95.0),
    ]
    assert build_comparison_summary(entries)["bestPolicyId"] == "b"


def test_comparison_summary_no_best_when_nothing_covered():
    entries = [_entry("a", "failed"), _entry("b", "completed", "Not Covered")]
    assert build_comparison_summary(entries)["bestPolicyId"] == ""


# ──────────────────────────────────────────────────────────────────────────────
# Response shape
# ──────────────────────────────────────────────────────────────────────────────

async def test_response_exposes_required_fields_only(harness):
    await harness.add_policy("A")
    await harness.add_prescription()
    result = await mpo.start_multi_policy_analysis(USER, harness.rx_id, [harness.id("A")])

    for key in ("sessionId", "prescriptionId", "status", "policyAnalyses", "comparisonSummary"):
        assert key in result

    # Internal extraction payloads must not leak to the client
    assert "prescriptionText" not in result
    assert "policyText" not in result

    entry = result["policyAnalyses"][0]
    for key in ("policyId", "policyName", "status", "overallStatus", "reportId",
                "sessionId", "questions", "errorMessage", "startedAt", "completedAt"):
        assert key in entry


async def test_comparison_summary_included_in_response(harness):
    await harness.add_policy("A")
    await harness.add_policy("B")
    await harness.add_prescription()

    result = await mpo.start_multi_policy_analysis(
        USER, harness.rx_id, [harness.id("A"), harness.id("B")]
    )

    summary = result["comparisonSummary"]
    assert summary["totalPolicies"] == 2
    assert summary["counts"]["covered"] == 2
    assert len(summary["results"]) == 2
