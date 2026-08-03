"""
AI Client — migrated from aiClient.js and multimodalAiClient.js.
Provider-agnostic LLM client using Google Gemini. All Groq code removed.
"""

import asyncio
import json
import re
import time
import random
from typing import Any, Optional

from google import genai
from google.genai import types

from app.core.config import get_settings
from app.core.logging import logger


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
    max_retries = 3

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
                    ),
                ),
                timeout=180.0,
            )

            llm_end = time.time()
            content = response.text
            usage = response.usage_metadata

            json_parse_start = time.time()
            extracted_json = parse_ai_json_response(content)
            json_parse_end = time.time()
            
            processing_time_ms = int((time.time() - start_time) * 1000)

            return {
                "extractedJson": extracted_json,
                "tokens": {
                    "promptTokens": getattr(usage, "prompt_token_count", 0) if usage else 0,
                    "completionTokens": getattr(usage, "candidates_token_count", 0) if usage else 0,
                    "totalTokens": getattr(usage, "total_token_count", 0) if usage else 0,
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

                # If explicit rate limit (429)
                if "429" in str(error):
                    delay_ms = max(delay_ms, 5000)

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
    max_retries = 3

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

            extracted_json = parse_ai_json_response(content)
            processing_time_ms = int((time.time() - start_time) * 1000)

            return {
                "extractedJson": extracted_json,
                "tokens": {
                    "promptTokens": getattr(usage, "prompt_token_count", 0) if usage else 0,
                    "completionTokens": getattr(usage, "candidates_token_count", 0) if usage else 0,
                    "totalTokens": getattr(usage, "total_token_count", 0) if usage else 0,
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
                if "429" in str(error):
                    delay_ms = max(delay_ms, 5000)

                logger.warning(f"[MultimodalAI] Backing off for {int(delay_ms)}ms...")
                await asyncio.sleep(delay_ms / 1000)

    raise ValueError(
        f"Multimodal extraction failed after {max_retries} retries. Last error: {last_error}"
    )
