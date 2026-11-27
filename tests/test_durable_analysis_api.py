"""Exercise the actual worker, authenticated API and GridFS against isolated MongoDB."""

import asyncio
from datetime import date
import os
import time
from urllib.parse import urlparse
from uuid import uuid4

import fitz
import httpx
import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from app.core.config import settings
from app.core.security import create_access_token
from app.services.coverage import coverage_analysis, policy_clause_search
from app.services.extraction import metadata_extraction, policy_continuity_extraction, policy_extraction, prescription_extraction
from main import create_app


@pytest.mark.asyncio
async def test_upload_consent_to_verified_report_on_real_mongo(monkeypatch):
    uri = os.environ.get("CLAIMSUPPORT_TEST_MONGO_URI")
    if not uri:
        pytest.skip("Set CLAIMSUPPORT_TEST_MONGO_URI for the isolated MongoDB integration test")
    assert urlparse(uri).hostname in {"localhost", "127.0.0.1", "::1", "mongo"}
    db_name = f"claimsupport_test_{uuid4().hex[:12]}"
    for key, value in {
        "MONGODB_URI": uri, "MONGODB_DATABASE": db_name,
        "REQUIRE_ANALYSIS_CONSENT": True,
        "ORPHAN_CLEANUP_ENABLED": False, "STORAGE_MONITOR_ENABLED": False,
    }.items():
        monkeypatch.setattr(settings, key, value)

    api = create_app()
    owner = {"Authorization": f"Bearer {create_access_token('synthetic-owner')}"}
    other = {"Authorization": f"Bearer {create_access_token('synthetic-other')}"}
    original_clause = "Appendectomy is covered under this policy."
    policy_start = date(date.today().year - 1, 1, 1)
    policy_end = date(date.today().year + 1, 12, 31)
    policy_facts = {
        "policyName": "Example Health", "insuranceCompany": "Example Insurance", "policyNumber": "TEST-001",
        "policyHolderName": "Example Member", "insuredMembers": [{"name": "Example Member"}],
        "policyStartDate": policy_start.isoformat(), "policyEndDate": policy_end.isoformat(),
        "coveredTreatments": ["Appendectomy"],
    }
    model_responses = {
        "Prescription JSON Extraction": {
            "patientName": "Example Member", "diagnosis": "Acute appendicitis",
            "procedures": [{"name": "Appendectomy"}], "visitDate": date.today().isoformat(),
        },
        "Policy JSON Extraction": policy_facts,
        "Policy Continuity Extraction": {"isRenewal": False, "policyStartDate": policy_start.isoformat()},
        "Policy Clause Search": {"items": [{
            "item": "Appendectomy", "decision": "Covered", "directMention": True,
            "clauses": [{"section": "Section 1 - Benefits", "quote": original_clause,
                         "effect": "Covers", "decisive": True}],
        }]},
        "Coverage Analysis": {"next_action": "generate_report", "confidence": 95,
                              "analysis": {"overallStatus": "Covered", "comparison": [{
                                  "item": "Appendectomy", "coverageStatus": "Covered",
                              }]}},
        "Background Policy Metadata": {},
        "Background Prescription Metadata": {},
    }

    async def model_reply(*args, operation_name, **kwargs):
        return {"extractedJson": model_responses[operation_name]}

    for service in (prescription_extraction, policy_extraction, policy_continuity_extraction,
                    policy_clause_search, coverage_analysis, metadata_extraction):
        monkeypatch.setattr(service, "extract_json_with_retry", model_reply)

    with fitz.open() as document:
        page = document.new_page()
        for line, value in enumerate([
            "Policy Name: Example Health",
            "Insurance Company: Example Insurance",
            "Policy Number: TEST-001",
            "Insured Person Name: Example Member",
            f"Policy Start Date: {policy_start:%d-%m-%Y}",
            f"Policy End Date: {policy_end:%d-%m-%Y}",
            "Section 1 - Benefits",
            original_clause,
        ]):
            page.insert_text((45, 50 + line * 20), value)
        policy_pdf = document.tobytes()

    async def poll(client, job_path, until):
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            result = (await client.get(job_path, headers=owner)).json()
            if result["status"] == "failed" or until(result):
                return result
            await asyncio.sleep(0.2)
        pytest.fail("Analysis did not reach the requested state")

    try:
        async with api.router.lifespan_context(api), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api), base_url="http://test",
        ) as client:
            assert (await client.get("/ready")).json()["ready"] is True
            consent = await client.post("/api/analysis/jobs", headers=owner, json={
                "accepted": True, "agreementVersion": settings.ANALYSIS_AGREEMENT_VERSION,
            })
            assert consent.status_code == 201, consent.text
            job_id = consent.json()["id"]
            job_path = f"/api/analysis/jobs/{job_id}"
            owner["X-Analysis-Job-Id"] = job_id
            assert (await client.get(job_path, headers=other)).status_code == 404
            upload = await client.post("/api/upload", headers=owner, data={"documentType": "policy"}, files={
                "file": ("policy.pdf", policy_pdf, "application/pdf"),
            })
            assert upload.status_code == 200, upload.text
            stored = upload.json()["fileId"]
            assert (await client.get(f"/api/upload/{stored}", headers=other)).status_code == 404
            both_inputs = await client.post("/api/prescriptions", headers=owner, json={
                "gridFsFileId": stored, "isManual": True,
                "extractedPrescriptionText": "Procedure: Appendectomy",
            })
            assert both_inputs.status_code == 400
            policy = await client.post("/api/policies", headers=owner, json={
                "gridFsFileId": stored, "originalFileName": "policy.pdf",
            })
            assert policy.status_code == 200, policy.text
            prescription = await client.post("/api/prescriptions", headers=owner, json={
                "isManual": True,
                "extractedPrescriptionText": "Patient Name: Example Member\n"
                f"Procedure: Appendectomy\nVisit Date: {date.today():%d-%m-%Y}",
            })
            assert prescription.status_code == 200, prescription.text
            rx_id = prescription.json()["_id"]
            preparation = await client.post(
                f"{job_path}/prescription", headers=owner, json={"prescriptionPath": rx_id},
            )
            assert preparation.status_code == 202, preparation.text
            prepared = await poll(client, job_path, lambda job: job["stage"] in {
                "prescription_ready", "invalid_prescription",
            })
            assert prepared["prescriptionValid"] is True, prepared
            start = await client.post(f"{job_path}/start", headers=owner, json={
                "prescriptionPath": rx_id, "policyIds": [policy.json()["_id"]],
            })
            assert start.status_code == 202, start.text

        # A new app lifetime must pick up the same persisted job.
        restarted = create_app()
        async with restarted.router.lifespan_context(restarted), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=restarted), base_url="http://test",
        ) as client:
            result = await poll(client, job_path, lambda job: job["status"] in {
                "completed", "manual_review_required", "waiting_for_user",
            })
            report_id = result["result"]["policyAnalyses"][0]["reportId"]
            report = await client.get(f"/api/analysis/{report_id}", headers=owner)
            assert report.status_code == 200
            comparison = report.json()["comparison"]
            assert result["status"] == "completed", comparison
            item, = comparison
            assert item["coverageStatus"] == "Covered"
            with fitz.open(stream=policy_pdf, filetype="pdf") as original:
                assert original_clause in original[0].get_text()
            assert any(
                clause["section"] == "Section 1 - Benefits" and clause["quote"] == original_clause
                for clause in item["policyClauses"]
            )
    finally:
        mongo = AsyncIOMotorClient(uri)
        try:
            await mongo.drop_database(db_name)
        finally:
            mongo.close()
