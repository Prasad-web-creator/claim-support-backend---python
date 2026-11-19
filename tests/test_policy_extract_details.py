"""
Tests for POST /policies/{policy_id}/extract-details.

The upload form is prefilled from the same continuity reading the analysis uses
to ask "new or renewed?" / "first start date?", so a prefilled answer can never
contradict the document and trigger those clarification questions. The AI call
is mocked here; only the mapping, caching and access rules are tested.
"""

import pytest
from httpx import AsyncClient, ASGITransport
from beanie import PydanticObjectId

from main import create_app
from app.middleware.auth import get_current_user
from app.models.policy import Policy
from app.services.storage.file_upload_service import FileUploadService
import app.services.extraction.policy_continuity_extraction as pce

USER = "test-user-123"
POLICY_TEXT = "POLICY SCHEDULE\n" + "Health insurance policy schedule text. " * 10

EMPTY = {
    "success": True,
    "isRenewal": None,
    "startDate": None,
    "renewedWithoutBreak": None,
    "extractedFields": {"isRenewal": False, "startDate": False, "renewedWithoutBreak": False},
}


def _app(user_id=USER):
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: {"id": user_id}
    return app


async def _policy(text=POLICY_TEXT, user_id=USER, **kw):
    p = Policy(user_id=user_id, policy_name="Uploaded Policy", grid_fs_file_id="f1", extracted_policy_text=text, **kw)
    await p.insert()
    return p


def _mock_continuity(monkeypatch, block):
    calls = []

    async def fake(text):
        calls.append(text)
        return dict(block)

    monkeypatch.setattr(pce, "extract_policy_continuity_fields", fake)

    async def no_bytes(_):
        return None

    monkeypatch.setattr(FileUploadService, "get_file_bytes", no_bytes)
    return calls


async def _post(policy_id, user_id=USER):
    async with AsyncClient(transport=ASGITransport(app=_app(user_id)), base_url="http://test") as ac:
        return await ac.post(f"/api/policies/{policy_id}/extract-details")


@pytest.mark.asyncio
async def test_renewed_policy_prefills_first_start_not_current_period(db, monkeypatch):
    """Current period starts 2026, cover since 2024: the first start (2024) is prefilled."""
    _mock_continuity(monkeypatch, {
        "policyStartDate": "2026-04-01", "policyEndDate": "2027-03-31", "isRenewal": True,
        "renewalInformation": "Renewal", "continuousCoverageSince": "2024-04-01",
        "renewedWithoutBreak": True,
        "continuityEvidence": ["Continuous coverage since 01-Apr-2024 without break"],
    })
    p = await _policy()
    res = await _post(p.id)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["isRenewal"] is True
    assert data["startDate"] == "2024-04-01"
    assert data["renewedWithoutBreak"] is True
    assert data["extractedFields"] == {"isRenewal": True, "startDate": True, "renewedWithoutBreak": True}

    # Cached on the policy for the analysis to reuse, without marking it as fully extracted.
    stored = (await Policy.get(p.id)).extracted_policy_json
    assert stored["continuity"]["continuousCoverageSince"] == "2024-04-01"


@pytest.mark.asyncio
async def test_new_policy_prefills_its_start_date(db, monkeypatch):
    _mock_continuity(monkeypatch, {
        "policyStartDate": "2026-10-01", "isRenewal": False, "renewalInformation": "Fresh policy",
    })
    data = (await _post((await _policy()).id)).json()
    assert data["isRenewal"] is False
    assert data["startDate"] == "2026-10-01"
    assert data["renewedWithoutBreak"] is None


@pytest.mark.asyncio
async def test_renewal_clauses_alone_do_not_mean_renewed(db, monkeypatch):
    """The document says neither new nor renewal: nothing is prefilled, so the user answers."""
    _mock_continuity(monkeypatch, {"policyStartDate": "2026-04-01", "isRenewal": None})
    data = (await _post((await _policy()).id)).json()
    assert data == {**EMPTY}


@pytest.mark.asyncio
async def test_previous_policy_number_means_renewed(db, monkeypatch):
    _mock_continuity(monkeypatch, {"policyStartDate": "2026-04-01", "isRenewal": None, "previousPolicyNumber": "OLD/123"})
    assert (await _post((await _policy()).id)).json()["isRenewal"] is True


@pytest.mark.asyncio
async def test_cached_continuity_is_not_read_again(db, monkeypatch):
    calls = _mock_continuity(monkeypatch, {"isRenewal": False, "renewalInformation": "Fresh", "policyStartDate": "2026-01-01"})
    p = await _policy()
    await _post(p.id)
    await _post(p.id)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_another_users_policy_is_not_readable(db, monkeypatch):
    calls = _mock_continuity(monkeypatch, {"isRenewal": True})
    p = await _policy(user_id="someone-else")
    res = await _post(p.id)
    assert res.status_code == 404
    assert calls == []


@pytest.mark.asyncio
async def test_missing_policy_is_404(db, monkeypatch):
    _mock_continuity(monkeypatch, {})
    assert (await _post(str(PydanticObjectId()))).status_code == 404


@pytest.mark.asyncio
async def test_requires_login(db):
    p = await _policy()
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as ac:
        res = await ac.post(f"/api/policies/{p.id}/extract-details")
    assert res.status_code in (401, 403)


@pytest.mark.asyncio
async def test_no_readable_text_returns_nulls(db, monkeypatch):
    calls = _mock_continuity(monkeypatch, {"isRenewal": True})
    p = await _policy(text="")
    assert (await _post(p.id)).json() == EMPTY
    assert calls == []


@pytest.mark.asyncio
async def test_failed_reading_returns_nulls(db, monkeypatch):
    _mock_continuity(monkeypatch, {})  # the AI read failed / returned nothing
    assert (await _post((await _policy()).id)).json() == EMPTY
