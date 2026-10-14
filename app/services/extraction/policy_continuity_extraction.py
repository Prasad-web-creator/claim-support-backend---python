"""
Policy Continuity Extraction.

Pulls the policy period and coverage-history facts (previous policy, renewal
wording, "continuous coverage since", supporting quotes) out of a policy
document. Extraction only — continuity itself is decided deterministically by
`app.services.coverage.policy_continuity`.
"""

from app.core.logging import logger
from app.services.llm.ai_client import extract_json_with_retry
from app.services.llm.prompts import POLICY_CONTINUITY_EXTRACTION_PROMPT

# Bump when the continuity fields or prompt change, so cached policies are re-read once.
CONTINUITY_EXTRACTION_VERSION = 1

# The continuity facts live in their own object on the policy JSON, so this pass
# can never overwrite what the main extraction (or the user) put at the top level.
CONTINUITY_KEY = "continuity"

# Policy schedules (where periods and renewal details are printed) sit at the
# start of the document, so only the opening section is sent.
MAX_CONTINUITY_TEXT_CHARS = 24000

CONTINUITY_FIELDS = (
    "policyNumber",
    "policyHolder",
    "insuranceCompany",
    "policyStartDate",
    "policyEndDate",
    "isRenewal",
    "renewalInformation",
    "previousPolicyNumber",
    "previousPolicyStartDate",
    "previousPolicyEndDate",
    "previousInsurer",
    "continuousCoverageSince",
    "continuityEvidence",
)


def needs_continuity_extraction(policy_json) -> bool:
    if not isinstance(policy_json, dict):
        return True
    block = policy_json.get(CONTINUITY_KEY)
    return not isinstance(block, dict) or block.get("version") != CONTINUITY_EXTRACTION_VERSION


async def extract_policy_continuity_fields(policy_text: str) -> dict:
    """Continuity facts for one policy document, or {} if they could not be read."""
    if not policy_text or len(policy_text.strip()) < 50:
        return {}
    try:
        result = await extract_json_with_retry(
            system_prompt=POLICY_CONTINUITY_EXTRACTION_PROMPT,
            user_content=policy_text[:MAX_CONTINUITY_TEXT_CHARS],
            max_tokens=1500,
            operation_name="Policy Continuity Extraction",
        )
    except Exception as e:
        logger.warning(f"[Policy] Continuity extraction failed: {e}")
        return {}
    extracted = result.get("extractedJson") if isinstance(result, dict) else None
    return extracted if isinstance(extracted, dict) else {}


def merge_continuity_fields(policy_json: dict, continuity: dict) -> dict:
    """
    Store continuity facts on an extracted policy JSON, in place, under
    `policy_json["continuity"]`. Top-level fields are left untouched.
    """
    if not isinstance(policy_json, dict):
        return policy_json
    continuity = continuity if isinstance(continuity, dict) else {}

    block = {}
    for key in CONTINUITY_FIELDS:
        value = continuity.get(key)
        if key == "continuityEvidence":
            block[key] = [str(q) for q in value if q] if isinstance(value, list) else []
        else:
            block[key] = value if value not in ("", []) else None
    block["version"] = CONTINUITY_EXTRACTION_VERSION

    policy_json[CONTINUITY_KEY] = block
    return policy_json


async def ensure_continuity_fields(policy_json: dict, policy_text: str) -> bool:
    """
    Backfill continuity facts on a policy extracted before they existed.

    Returns True when `policy_json` was changed (so the caller can persist it).
    Only runs when the stored JSON has no/old continuity version and there is
    text to read, so each policy document is read for this at most once.
    """
    if not isinstance(policy_json, dict) or not needs_continuity_extraction(policy_json):
        return False
    if not policy_text or len(policy_text.strip()) < 50:
        return False
    continuity = await extract_policy_continuity_fields(policy_text)
    if not continuity:
        # Leave the marker unset: a transient failure should be retried next time.
        return False
    merge_continuity_fields(policy_json, continuity)
    return True
