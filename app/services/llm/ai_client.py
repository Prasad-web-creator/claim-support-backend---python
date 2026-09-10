"""
AI Client — migrated from aiClient.js and multimodalAiClient.js.
Provider-agnostic LLM client using Google Gemini. All Groq code removed.
"""

import asyncio
import json
import re
import time
import random
import contextvars
from dataclasses import dataclass, field
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


import contextvars

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
        total_in = sum(s.input_tokens for s in self.steps)
        total_out = sum(s.output_tokens for s in self.steps)
        total_usd = sum(s.cost_usd for s in self.steps)
        total_inr = sum(s.cost_inr for s in self.steps)

        header_title = f"💰 [ANALYSIS COST BREAKDOWN] Report #{self.report_number} | Session: {self.session_id}" if self.report_number else f"💰 [ANALYSIS COST BREAKDOWN] Session: {self.session_id}"

        lines = [
            "\n" + "=" * 98,
            header_title,
            "=" * 98,
            f"  {'Step':<5} | {'Operation':<38} | {'In Tok':>8} | {'Out Tok':>8} | {'Cost (USD)':>12} | {'Cost (INR)':>14}",
            "-" * 98,
        ]

        for idx, step in enumerate(self.steps, 1):
            cost_inr_str = f"₹{step.cost_inr:.3f}" if step.cost_inr > 0 else "₹0.000 (Free)"
            cost_usd_str = f"${step.cost_usd:.5f}"
            lines.append(
                f"  {idx:<5} | {step.operation:<38} | {step.input_tokens:>8,d} | {step.output_tokens:>8,d} | {cost_usd_str:>12} | {cost_inr_str:>14}"
            )

        lines.append("-" * 98)
        lines.append(
            f"  {'TOTAL':<5} | {doc_summary:<38} | {total_in:>8,d} | {total_out:>8,d} | {f'${total_usd:.5f}':>12} | {f'₹{total_inr:.3f} INR':>14}"
        )
        lines.append("=" * 98)

        logger.info("\n".join(lines))


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

def get_ai_client() -> genai.Client:
    global _global_client
    if _global_client is None:
        settings = get_settings()
        if not settings.GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY is not defined in environment variables.")
        _global_client = genai.Client(api_key=settings.GEMINI_API_KEY)
    return _global_client


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
    retry_count = 0
    max_retries = 6  # Increased to handle transient 503 surges

    # Enforce "JSON" in prompt to help guide the model
    enforced_prompt = system_prompt
    if "json" not in enforced_prompt.lower():
        enforced_prompt += "\n\nYou MUST return your answer in valid JSON format."

    client = get_ai_client()
    last_error: Exception | None = None

    while retry_count <= max_retries:
        try:
            current_prompt = enforced_prompt
            if retry_count > 0:
                current_prompt += "\n\nCRITICAL RETRY INSTRUCTION: Your previous response was invalid. You MUST return ONLY valid JSON. No markdown, no explanations."
                if last_error and str(last_error):
                    current_prompt += f"\n\nThe parser failed with this error: {last_error}. Fix the JSON structure."

            llm_start = time.time()

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

            logger.info(
                f"\n----------------------------- Google's server response -----------------------------------\n"
                f"{response}\n"
                f"---------------------------------------------------------------------------------------------"
            )

            prompt_toks = getattr(usage, "prompt_token_count", 0) if usage else 0
            comp_toks = getattr(usage, "candidates_token_count", 0) if usage else 0
            total_toks = getattr(usage, "total_token_count", 0) if usage else 0
            cost_info = calculate_gemini_cost(prompt_toks, comp_toks, model_name=model_name)

            logger.info(
                f"[AI Cost] 💵 Model: {model_name} | In: {prompt_toks} tok | Out: {comp_toks} tok | Cost: {cost_info['formattedCost']}"
            )

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
            retry_count += 1
            logger.error(f"[AI Client] Attempt {retry_count} failed: {type(error).__name__}: {error}")

            if retry_count <= max_retries:
                # Exponential backoff with jitter
                delay_ms = (2**retry_count * 1000) + (random.random() * 1000)

                # Extended backoff for rate limit (429) and service unavailable (503)
                if "429" in str(error) or "503" in str(error):
                    delay_ms = max(delay_ms, 8000)

                logger.warning(f"[AI Client] Backing off for {int(delay_ms)}ms...")
                await asyncio.sleep(delay_ms / 1000)

    raise ValueError(
        f"Extraction failed after {max_retries} retries. Last error: {last_error}"
    )


async def extract_json_multimodal(
    system_prompt: str,
    text_content: str = "",
    inline_parts: list[dict] | None = None,
    model_name: str | None = None,
    max_tokens: int = 8192,
    operation_name: str = "Multimodal AI JSON Extraction",
) -> dict:
    """
    Sends a multimodal request to Gemini supporting inline binary data (images, PDFs).

    Args:
        system_prompt: System instruction text
        text_content: Additional text to include in the user turn
        inline_parts: List of {"mimeType": str, "data": bytes} dicts
        model_name: Gemini model to use
        max_tokens: Maximum output tokens

    Returns:
        Same structure as extract_json_with_retry
    """
    settings = get_settings()
    if model_name is None:
        model_name = settings.AI_VISION_MODEL or settings.AI_MODEL

    if inline_parts is None:
        inline_parts = []

    start_time = time.time()
    retry_count = 0
    max_retries = 6  # Increased to handle transient 503 surges

    enforced_prompt = system_prompt
    if "json" not in enforced_prompt.lower():
        enforced_prompt += "\n\nYou MUST return your answer in valid JSON format."

    client = get_ai_client()
    last_error: Exception | None = None

    while retry_count <= max_retries:
        try:
            current_prompt = enforced_prompt
            if retry_count > 0:
                current_prompt += "\n\nCRITICAL RETRY INSTRUCTION: Your previous response was invalid. Return ONLY valid JSON. No markdown, no explanations."
                if last_error:
                    current_prompt += f"\n\nThe parser failed with this error: {last_error}. Fix the JSON structure."

            # Build content parts
            contents = []
            for part in inline_parts:
                import base64
                contents.append(
                    types.Part.from_bytes(
                        data=part["data"],
                        mime_type=part["mimeType"],
                    )
                )

            if text_content and text_content.strip():
                contents.append(types.Part.from_text(text=text_content))

            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=current_prompt,
                        response_mime_type="application/json",
                        temperature=0.1,
                        max_output_tokens=max_tokens,
                    ),
                ),
                timeout=180.0,
            )

            content = response.text
            usage = response.usage_metadata

            logger.info(
                f"\n----------------------------- Google's server response -----------------------------------\n"
                f"{response}\n"
                f"---------------------------------------------------------------------------------------------"
            )

            prompt_toks = getattr(usage, "prompt_token_count", 0) if usage else 0
            comp_toks = getattr(usage, "candidates_token_count", 0) if usage else 0
            total_toks = getattr(usage, "total_token_count", 0) if usage else 0
            cost_info = calculate_gemini_cost(prompt_toks, comp_toks, model_name=model_name)

            logger.info(
                f"[AI Cost] 💵 [Multimodal] Model: {model_name} | In: {prompt_toks} tok | Out: {comp_toks} tok | Cost: {cost_info['formattedCost']}"
            )

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

            extracted_json = parse_ai_json_response(content)
            processing_time_ms = int((time.time() - start_time) * 1000)

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
            }

        except Exception as error:
            last_error = error
            logger.error(f"[MultimodalAI] Attempt {retry_count + 1} failed: {type(error).__name__}: {error}")
            retry_count += 1

            if retry_count <= max_retries:
                delay_ms = (2**retry_count * 1000) + (random.random() * 1000)
                # Extended backoff for rate limit (429) and service unavailable (503)
                if "429" in str(error) or "503" in str(error):
                    delay_ms = max(delay_ms, 8000)

                logger.warning(f"[MultimodalAI] Backing off for {int(delay_ms)}ms...")
                await asyncio.sleep(delay_ms / 1000)

    raise ValueError(
        f"Multimodal extraction failed after {max_retries} retries. Last error: {last_error}"
    )


async def extract_text_multimodal(
    system_prompt: str,
    text_content: str = "",
    inline_parts: list[dict] | None = None,
    model_name: str | None = None,
    max_tokens: int = 32768,
    operation_name: str = "Gemini Vision OCR",
) -> str:
    """
    Direct multimodal text generation from Gemini (supports images, PDFs).
    Returns raw transcribed text directly without JSON parsing overhead.
    """
    settings = get_settings()
    if model_name is None:
        model_name = settings.AI_VISION_MODEL or settings.AI_MODEL

    if inline_parts is None:
        inline_parts = []

    retry_count = 0
    max_retries = 3

    client = get_ai_client()
    last_error: Exception | None = None

    while retry_count <= max_retries:
        try:
            contents = []
            for part in inline_parts:
                contents.append(
                    types.Part.from_bytes(
                        data=part["data"],
                        mime_type=part["mimeType"],
                    )
                )

            if text_content and text_content.strip():
                contents.append(types.Part.from_text(text=text_content))

            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        temperature=0.1,
                        max_output_tokens=max_tokens,
                    ),
                ),
                timeout=180.0,
            )

            usage = response.usage_metadata

            logger.info(
                f"\n----------------------------- Google's server response -----------------------------------\n"
                f"{response}\n"
                f"---------------------------------------------------------------------------------------------"
            )

            prompt_toks = getattr(usage, "prompt_token_count", 0) if usage else 0
            comp_toks = getattr(usage, "candidates_token_count", 0) if usage else 0
            cost_info = calculate_gemini_cost(prompt_toks, comp_toks, model_name=model_name)

            logger.info(
                f"[AI Cost] 💵 [Vision OCR] Model: {model_name} | In: {prompt_toks} tok | Out: {comp_toks} tok | Cost: {cost_info['formattedCost']}"
            )

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

            text = response.text or ""
            return text.strip()

        except Exception as error:
            last_error = error
            logger.error(f"[MultimodalTextAI] Attempt {retry_count + 1} failed: {type(error).__name__}: {error}")
            retry_count += 1

            if retry_count <= max_retries:
                delay_ms = (2**retry_count * 1000) + (random.random() * 1000)
                if "429" in str(error) or "503" in str(error):
                    delay_ms = max(delay_ms, 6000)

                logger.warning(f"[MultimodalTextAI] Backing off for {int(delay_ms)}ms...")
                await asyncio.sleep(delay_ms / 1000)

    raise ValueError(
        f"Multimodal text extraction failed after {max_retries} retries. Last error: {last_error}"
    )

