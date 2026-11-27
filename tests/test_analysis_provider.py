"""Gemini analysis and credential requirements, exercised without network calls."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.core.config import get_settings
from app.services.llm import ai_client
from main import create_app


@pytest.mark.parametrize("api_key", ["", "   "])
async def test_missing_gemini_key_rejects_extraction(monkeypatch, api_key):
    monkeypatch.setattr(get_settings(), "GEMINI_API_KEY", api_key)
    client = Mock()
    monkeypatch.setattr(ai_client.genai, "Client", client)

    with pytest.raises(ValueError, match="GEMINI_API_KEY is required"):
        await ai_client.extract_json_with_retry("Return JSON.", "sample input")

    client.assert_not_called()


async def test_missing_gemini_key_blocks_api_startup(monkeypatch):
    monkeypatch.setattr(get_settings(), "GEMINI_API_KEY", "")
    app = create_app()

    with pytest.raises(RuntimeError, match="GEMINI_API_KEY is required to start the API"):
        async with app.router.lifespan_context(app):
            pytest.fail("API started without Gemini credentials")


async def test_extraction_calls_gemini_with_configured_key_and_model(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "synthetic-test-key")
    monkeypatch.setattr(settings, "AI_MODEL", "mock-gemini-model")
    monkeypatch.setattr(ai_client, "_global_client", None)
    calls = []

    async def generate_content(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text='{"ok": true}', usage_metadata=None)

    client = Mock(return_value=SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)),
    ))
    monkeypatch.setattr(ai_client.genai, "Client", client)

    result = await ai_client.extract_json_with_retry("Return JSON.", "sample input")

    client.assert_called_once_with(api_key="synthetic-test-key")
    assert len(calls) == 1
    assert calls[0]["model"] == "mock-gemini-model"
    assert calls[0]["contents"] == "sample input"
    assert result["extractedJson"] == {"ok": True}


@pytest.mark.parametrize("max_attempts", [1, 3])
async def test_gemini_extraction_uses_shared_attempt_limit(monkeypatch, max_attempts):
    monkeypatch.setattr(ai_client, "get_settings", lambda: SimpleNamespace(AI_MAX_ATTEMPTS=max_attempts, AI_MAX_CONCURRENT_REQUESTS=3))
    calls, backoffs = [], []

    async def generate_content(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text='{"incomplete":', usage_metadata=None)

    async def back_off(attempt, error):
        backoffs.append(attempt)

    monkeypatch.setattr(ai_client, "get_ai_client", lambda: SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)),
    ))
    monkeypatch.setattr(ai_client, "_back_off", back_off)

    with pytest.raises(ValueError, match=f"failed after {max_attempts} attempts"):
        await ai_client.extract_json_with_retry("Return result", "sample input", model_name="mock-model")

    assert len(calls) == max_attempts
    assert backoffs == list(range(1, max_attempts))

