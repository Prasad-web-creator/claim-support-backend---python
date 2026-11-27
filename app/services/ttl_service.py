"""Inspect or remove the retired document retention TTL indexes."""

from pymongo.errors import OperationFailure

RETENTION_COLLECTIONS = (
    "policies",
    "prescriptions",
    "analysisreports",
    "analysissessions",
    "multipolicyanalysissessions",
    "analysisauditlogs",
    "activitylogs",
    "storedfiles",
)


def _is_retention_ttl(index: dict) -> bool:
    keys = index.get("key", ())
    return len(keys) == 1 and keys[0][0] == "createdAt" and "expireAfterSeconds" in index


def retire_retention_indexes(database, *, apply: bool = False) -> list[dict]:
    changes = []
    for name in RETENTION_COLLECTIONS:
        collection = database[name]
        for index_name, index in collection.index_information().items():
            if not _is_retention_ttl(index):
                continue
            outcome = "candidate"
            if apply:
                current = collection.index_information().get(index_name, {})
                if current != index or not _is_retention_ttl(current):
                    continue
                try:
                    collection.drop_index(index_name)
                    outcome = "removed"
                except OperationFailure as exc:
                    if exc.code != 27:  # IndexNotFound after a concurrent migration
                        raise
                    outcome = "already removed"
            changes.append({
                "collection": name,
                "index": index_name,
                "key": index["key"],
                "previous_expire_after_seconds": index["expireAfterSeconds"],
                "outcome": outcome,
            })
    return changes
