"""
Repair for values that the generic CRUD update once stored under the Python field
name (e.g. "inception_date") instead of the Mongo key Beanie reads (the alias,
"inceptionDate"). Beanie only fetches alias keys, so those values were never read:
a policy's declared inception date, for one, was silently ignored.

Every such value came from a later edit, so it is newer than the alias value and
replaces it. The stray key is then removed, which makes the repair idempotent.
"""

from app.core.logging import logger


async def repair_field_name_keys(model_cls) -> int:
    """Move values stored under field names to their alias keys. Returns documents repaired."""
    collection = model_cls.get_motor_collection()
    repaired = 0
    for name, info in model_cls.model_fields.items():
        alias = info.alias
        if not alias or alias == name or alias == "_id":
            continue
        result = await collection.update_many(
            {name: {"$exists": True}},
            [{"$set": {alias: f"${name}"}}, {"$unset": name}],
        )
        if result.modified_count:
            logger.info(
                f"[Data Repair] {model_cls.__name__}: moved '{name}' to '{alias}' on {result.modified_count} document(s)."
            )
            repaired += result.modified_count
    return repaired
