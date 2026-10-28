"""
The generic CRUD update must write the Mongo key Beanie reads (the field alias).
Writing the Python field name left the user's declared policy inception date unread.
"""

import asyncio

from app.models.policy import Policy
from app.services import crud_service
from app.services.crud_service import CrudService


class FakePolicyDoc:
    model_fields = Policy.model_fields

    def __init__(self):
        self.id = "p1"
        self.ops = None

    async def update(self, ops):
        self.ops = ops


def test_update_writes_alias_keys_and_clears_field_name_keys(monkeypatch):
    doc = FakePolicyDoc()
    service = CrudService(Policy, "Policy", [])

    async def fake_get(user_id, doc_id):
        return doc

    class FakeLog:
        def __init__(self, **kwargs):
            pass

        async def insert(self):
            pass

    monkeypatch.setattr(service, "get_by_id", fake_get)
    monkeypatch.setattr(crud_service, "ActivityLog", FakeLog)

    asyncio.run(service.update("u1", "p1", {
        "inceptionDate": "2022-10-01T00:00:00.000",
        "continuousRenewal": True,
        "status": "Active",
    }))

    assert doc.ops["$set"]["inceptionDate"] == "2022-10-01T00:00:00.000"
    assert doc.ops["$set"]["continuousRenewal"] is True
    assert doc.ops["$set"]["status"] == "Active"
    assert "inception_date" not in doc.ops["$set"]
    assert set(doc.ops["$unset"]) == {"inception_date", "continuous_renewal"}
