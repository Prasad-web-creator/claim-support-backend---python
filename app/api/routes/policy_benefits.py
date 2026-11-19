"""
My Benefits — read-only endpoint over the policy data already stored in MongoDB.

No LLM, OCR or analysis pipeline is called and the policy document is never modified.
"""

import copy

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException

from app.core.logging import logger
from app.middleware.auth import get_current_user
from app.models.policy import Policy
from app.services.benefits.policy_benefits import build_policy_benefits

router = APIRouter(prefix="/policies", tags=["Policy Benefits"])


def _declared_waiting_start(policy: Policy):
    """Waiting-period start + basis exactly as the analysis pipeline works it out (pure, no I/O)."""
    try:
        from app.services.analysis_interactive_orchestrator import _apply_declared_inception

        scratch = copy.deepcopy(policy.extracted_policy_json or {})
        start = _apply_declared_inception(scratch, policy)
        if start:
            return scratch.get("waitingPeriodStartDate"), scratch.get("waitingPeriodBasis")
    except Exception as e:  # never block the page on this
        logger.warning(f"[My Benefits] Could not derive waiting-period start for {policy.id}: {e}")
    return None, None


@router.get("/{policy_id}/benefits")
async def get_policy_benefits(policy_id: str, current_user: dict = Depends(get_current_user)):
    """Plain-language benefits for one of the current user's policies."""
    try:
        oid = PydanticObjectId(policy_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Policy not found")

    policy = await Policy.find_one(Policy.id == oid, Policy.user_id == current_user["id"])
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")

    start, basis = _declared_waiting_start(policy)
    return build_policy_benefits(policy, waiting_start=start, waiting_basis=basis)
