"""Optional Gemini routing advice using fixed categories only."""

import asyncio
from typing import Literal

from google.genai import types
from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.config import get_settings
from app.core.logging import logger
from app.services.llm.ai_client import get_ai_client, get_llm_semaphore


class TriageAdvice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recommendation: Literal["request_policy_wording", "verify_policy_version", "contact_insurer"]


TRIAGE_INPUTS = {
    "unmatched": "There is no approved rule for the policy wording or item.",
    "conflicting": "Approved rules conflict or a required policy quotation cannot be verified.",
}
USER_MESSAGES = {
    "request_policy_wording": "Provide the complete policy wording and applicable endorsements for manual review.",
    "verify_policy_version": "Verify the policy wording version with the insurer before relying on the result.",
    "contact_insurer": "Ask the insurer to confirm the applicable benefit in writing.",
}


async def triage_unresolved(category: Literal["unmatched", "conflicting"]) -> str | None:
    settings = get_settings()
    if not getattr(settings, "GEMINI_FALLBACK_ENABLED", False) or not settings.GEMINI_API_KEY:
        return None

    async def request():
        async with get_llm_semaphore():
            return await get_ai_client().aio.models.generate_content(
                model=getattr(settings, "GEMINI_MODEL", None) or settings.AI_MODEL,
                contents=TRIAGE_INPUTS[category],
                config=types.GenerateContentConfig(
                    system_instruction="Suggest a next administrative step for manual insurance policy review. Do not decide eligibility or payment.",
                    response_mime_type="application/json",
                    response_schema=TriageAdvice,
                    temperature=0,
                ),
            )

    retries = getattr(settings, "GEMINI_FALLBACK_RETRIES", 1)
    timeout = getattr(settings, "GEMINI_FALLBACK_TIMEOUT_SECONDS", 5)
    for attempt in range(retries + 1):
        try:
            response = await asyncio.wait_for(request(), timeout=timeout)
            parsed = TriageAdvice.model_validate_json(response.text or "")
            return USER_MESSAGES[parsed.recommendation]
        except ValidationError:
            break
        except Exception:
            if attempt < retries:
                await asyncio.sleep(min(2**attempt, 4))

    from app.core.telemetry import record_failure

    record_failure("gemini_fallback")
    logger.warning("[Coverage] Gemini administrative triage unavailable")
    return None
