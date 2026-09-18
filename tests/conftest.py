"""
Shared test fixtures.

Beanie runs against an in-memory MongoDB (mongomock-motor), so documents are
really inserted, validated and read back — only the LLM/OCR/file-storage calls
are stubbed. That keeps the orchestration, status roll-up, persistence and
authorization logic under genuine test.
"""

import os
import sys

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017/test")
os.environ.setdefault("GEMINI_API_KEY", "test-key")


@pytest_asyncio.fixture
async def db():
    """Fresh in-memory database with all analysis-related models initialised."""
    from beanie import init_beanie
    from mongomock_motor import AsyncMongoMockClient

    from app.models.analysis_report import AnalysisReport
    from app.models.analysis_session import AnalysisSession
    from app.models.analysis_audit_log import AnalysisAuditLog
    from app.models.counter import Counter
    from app.models.multi_policy_session import MultiPolicyAnalysisSession
    from app.models.policy import Policy
    from app.models.prescription import Prescription

    client = AsyncMongoMockClient()
    database = client["claimsupport_test"]
    await init_beanie(
        database=database,
        document_models=[
            Policy,
            Prescription,
            AnalysisReport,
            AnalysisSession,
            MultiPolicyAnalysisSession,
            AnalysisAuditLog,
            Counter,
        ],
    )
    return database


VALID_RX_JSON = {
    "diagnosis": "Acute appendicitis",
    "patientName": "Test Patient",
    "medicines": [{"name": "Paracetamol"}],
    "procedures": [{"name": "Appendectomy"}],
    "visitDate": "2026-06-14",
}

VALID_POLICY_JSON = {
    "insuranceCompany": "TestInsurer",
    "policyType": "Health",
    "coveredTreatments": ["Appendectomy"],
    "excludedTreatments": [],
    "coverageAmount": 500000,
}
