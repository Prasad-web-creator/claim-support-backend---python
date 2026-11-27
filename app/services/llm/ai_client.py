"""Google Gemini client for structured AI analysis."""

import asyncio
import json
import re
import time
import random
import contextvars
from dataclasses import dataclass
from typing import Any, Optional, List

from google import genai
from google.genai import types

from app.core.config import get_settings
from app.core.logging import logger

# ─── Gemini Pricing Rates (USD per 1M tokens) & INR Exchange Rate ───────────────
USD_TO_INR = 87.5

PRICING_TABLE = {
    "lite": {"input": 0.0375, "output": 0.15},       # gemini-flash-lite
    "flash": {"input": 0.075, "output": 0.30},       # gemini-flash
    "pro": {"input": 1.25, "output": 5.00},          # gemini-pro
}


_current_cost_tracker: contextvars.ContextVar[Optional["AnalysisCostTracker"]] = contextvars.ContextVar(
    "_current_cost_tracker", default=None
)


def get_current_cost_tracker() -> Optional["AnalysisCostTracker"]:
    return _current_cost_tracker.get()


def set_current_cost_tracker(tracker: Optional["AnalysisCostTracker"]):
    return _current_cost_tracker.set(tracker)


@dataclass
class CostStep:
    operation: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    cost_inr: float = 0.0
    engine: str = "Gemini"


class AnalysisCostTracker:
    """Collects all API calls and local OCR operations during an analysis run and prints an itemized bill."""

    def __init__(self, session_id: str = "", report_number: int | str = ""):
        self.session_id = str(session_id)
        self.report_number = str(report_number)
        self.steps: List[CostStep] = []

    def record_step(
        self,
        operation: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
        cost_inr: float = 0.0,
        engine: str = "Gemini",
    ):
        self.steps.append(
            CostStep(
                operation=operation,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
                cost_inr=cost_inr,
                engine=engine,
            )
        )

    def to_dict_list(self) -> List[dict]:
        return [
            {
                "operation": s.operation,
                "input_tokens": s.input_tokens,
                "output_tokens": s.output_tokens,
                "cost_usd": s.cost_usd,
                "cost_inr": s.cost_inr,
                "engine": s.engine,
            }
            for s in self.steps
        ]

    def load_steps(self, steps_data: List[dict]):
        for d in (steps_data or []):
            if isinstance(d, dict):
                self.steps.append(
                    CostStep(
                        operation=d.get("operation", "AI Operation"),
                        input_tokens=d.get("input_tokens", 0),
                        output_tokens=d.get("output_tokens", 0),
                        cost_usd=d.get("cost_usd", 0.0),
                        cost_inr=d.get("cost_inr", 0.0),
                        engine=d.get("engine", "Gemini"),
                    )
                )

    def print_summary_table(self, doc_summary: str = "Complete Analysis Pipeline"):
        # Retain internal step recording; silence verbose cost table at standard INFO level
        logger.debug(f"[CostTracker] Summary recorded for {doc_summary} ({len(self.steps)} operations)")


def calculate_gemini_cost(prompt_tokens: int, completion_tokens: int, model_name: str = "") -> dict:
    """Calculate the estimated USD and INR cost for a Gemini API call."""
    model_lower = (model_name or "").lower()
    if "lite" in model_lower:
        rates = PRICING_TABLE["lite"]
    elif "pro" in model_lower:
        rates = PRICING_TABLE["pro"]
    else:
        rates = PRICING_TABLE["flash"]

    cost_usd = (prompt_tokens * rates["input"] / 1_000_000.0) + (completion_tokens * rates["output"] / 1_000_000.0)
    cost_inr = cost_usd * USD_TO_INR
    return {
        "promptTokens": prompt_tokens,
        "completionTokens": completion_tokens,
        "totalTokens": prompt_tokens + completion_tokens,
        "costUsd": round(cost_usd, 6),
        "costInr": round(cost_inr, 4),
        "formattedCost": f"${cost_usd:.5f} USD (₹{cost_inr:.3f} INR)",
    }


def parse_ai_json_response(text: str) -> dict:
    """
    Aggressively parses and repairs AI-generated JSON.
    Strips markdown code fences and fixes common issues.
    """
    if not text:
        raise ValueError("AI returned an empty response.")

    clean_text = text.strip()

    # Find the earliest starting JSON character
    first_brace = clean_text.find("{")
    first_bracket = clean_text.find("[")

    start_index = -1
    if first_brace != -1 and first_bracket != -1:
        start_index = min(first_brace, first_bracket)
    elif first_brace != -1:
        start_index = first_brace
    elif first_bracket != -1:
        start_index = first_bracket

    if start_index != -1:
        clean_text = clean_text[start_index:]

    # Strip trailing markdown if present
    if clean_text.endswith("```"):
        last_ticks = clean_text.rfind("```")
        if last_ticks > 0:
            clean_text = clean_text[:last_ticks].strip()

    # Fix trailing commas before closing braces/brackets
    clean_text = re.sub(r",\s*([}\]])", r"\1", clean_text)

    # 1. Try standard lenient parse (strict=False permits literal newlines/control characters inside strings)
    try:
        return json.loads(clean_text, strict=False)
    except json.JSONDecodeError:
        pass

    # 2. Try regex repair for unescaped newlines inside strings
    try:
        repaired = re.sub(r'(?<!\\)\n', r'\\n', clean_text)
        return json.loads(repaired, strict=False)
    except Exception:
        pass

    # 3. Final attempt to raise descriptive parse error
    try:
        return json.loads(clean_text)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Failed to parse AI JSON response: {e} | Raw text redacted for security"
        )


_global_client = None
_llm_semaphore: Optional[asyncio.Semaphore] = None


def get_ai_client() -> genai.Client:
    global _global_client
    api_key = get_settings().GEMINI_API_KEY.strip()
    if not api_key:
        raise ValueError("GEMINI_API_KEY is required for Gemini analysis.")
    if _global_client is None:
        _global_client = genai.Client(api_key=api_key)
    return _global_client


def get_llm_semaphore() -> asyncio.Semaphore:
    """
    Process-wide bound on simultaneous in-flight Gemini requests.

    Multi-policy analysis fans out N coverage analyses at once; without this the
    provider returns 429/503 and every call burns its full retry budget.
    Created lazily so it binds to the running event loop.
    """
    global _llm_semaphore
    if _llm_semaphore is None:
        _llm_semaphore = asyncio.Semaphore(get_settings().AI_MAX_CONCURRENT_REQUESTS)
    return _llm_semaphore


async def _back_off(attempt: int, error: Exception) -> None:
    delay_ms = 2**attempt * 1000 + random.random() * 1000
    if "429" in str(error) or "503" in str(error):
        delay_ms = max(delay_ms, 8000)
    logger.warning(f"[AI Client] Backing off for {int(delay_ms)}ms...")
    await asyncio.sleep(delay_ms / 1000)


async def extract_json_with_retry(
    system_prompt: str,
    user_content: str,
    model_name: str | None = None,
    max_tokens: int = 4000,
    response_schema: Any = None,
    operation_name: str = "AI JSON Extraction",
) -> dict:
    """
    Extracts structured JSON using Gemini API with automatic retry and exponential backoff.

    Returns:
        {
            "extractedJson": dict,
            "tokens": {"promptTokens": int, "completionTokens": int, "totalTokens": int},
            "retryCount": int,
            "processingTimeMs": int
        }
    """
    settings = get_settings()
    if model_name is None:
        model_name = settings.AI_MODEL

    start_time = time.time()
    max_attempts = settings.AI_MAX_ATTEMPTS

    # Enforce "JSON" in prompt to help guide the model
    enforced_prompt = system_prompt
    if "json" not in enforced_prompt.lower():
        enforced_prompt += "\n\nYou MUST return your answer in valid JSON format."

    client = get_ai_client()
    last_error: Exception | None = None

    for retry_count in range(max_attempts):
        try:
            current_prompt = enforced_prompt
            if retry_count > 0:
                current_prompt += "\n\nCRITICAL RETRY INSTRUCTION: Your previous response was invalid. You MUST return ONLY valid JSON. No markdown, no explanations."
                if last_error and str(last_error):
                    current_prompt += f"\n\nThe parser failed with this error: {last_error}. Fix the JSON structure."

            llm_start = time.time()

            async with get_llm_semaphore():
                response = await asyncio.wait_for(
                    client.aio.models.generate_content(
                        model=model_name,
                        contents=user_content,
                        config=types.GenerateContentConfig(
                            system_instruction=current_prompt,
                            response_mime_type="application/json",
                            temperature=0.1,
                            max_output_tokens=max_tokens,
                            response_schema=response_schema,
                        ),
                    ),
                    timeout=180.0,
                )

            llm_end = time.time()
            content = response.text
            usage = response.usage_metadata

            prompt_toks = getattr(usage, "prompt_token_count", 0) if usage else 0
            comp_toks = getattr(usage, "candidates_token_count", 0) if usage else 0
            total_toks = getattr(usage, "total_token_count", 0) if usage else 0
            cost_info = calculate_gemini_cost(prompt_toks, comp_toks, model_name=model_name)

            tracker = get_current_cost_tracker()
            if tracker:
                tracker.record_step(
                    operation=operation_name,
                    input_tokens=prompt_toks,
                    output_tokens=comp_toks,
                    cost_usd=cost_info["costUsd"],
                    cost_inr=cost_info["costInr"],
                    engine=model_name or "Gemini",
                )

            json_parse_start = time.time()
            extracted_json = parse_ai_json_response(content)
            json_parse_end = time.time()
            
            processing_time_ms = int((time.time() - start_time) * 1000)
            logger.info(
                f"[AI] {operation_name} completed in {processing_time_ms}ms ({total_toks} tokens)"
            )

            return {
                "extractedJson": extracted_json,
                "tokens": {
                    "promptTokens": prompt_toks,
                    "completionTokens": comp_toks,
                    "totalTokens": total_toks,
                    "costUsd": cost_info["costUsd"],
                    "costInr": cost_info["costInr"],
                },
                "retryCount": retry_count,
                "processingTimeMs": processing_time_ms,
                "timing": {
                    "promptPreparationSec": round(llm_start - start_time, 2),
                    "llmProcessingSec": round(llm_end - llm_start, 2),
                    "jsonParsingSec": round(json_parse_end - json_parse_start, 2)
                }
            }

        except Exception as error:
            last_error = error
            attempt = retry_count + 1
            logger.error(f"[AI Client] Attempt {attempt}/{max_attempts} failed: {type(error).__name__}: {error}")
            if attempt < max_attempts:
                await _back_off(attempt, error)

    raise ValueError(
        f"Extraction failed after {max_attempts} attempts. Last error: {last_error}"
    )
