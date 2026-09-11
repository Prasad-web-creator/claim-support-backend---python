"""
Vector Embedding and Semantic Similarity Abstraction for Expert Cases.
Supports deterministic local semantic embeddings (zero-dependency, fast)
and cloud Gemini embedding models (text-embedding-004) with cosine similarity matching.
"""

import math
import re
import hashlib
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

from app.core.logging import logger


class BaseCaseEmbeddingProvider(ABC):
    """Abstract interface for case vector embedding providers."""

    @abstractmethod
    async def generate_embedding(self, text: str) -> List[float]:
        """Generates a dense vector embedding for case semantic text."""
        pass

    @staticmethod
    def cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
        """Computes standard cosine similarity between two vector embeddings."""
        if not vec1 or not vec2 or len(vec1) != len(vec2):
            return 0.0
        dot = sum(a * b for a, b in zip(vec1, vec2))
        norm_a = math.sqrt(sum(a * a for a in vec1))
        norm_b = math.sqrt(sum(b * b for b in vec2))
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return dot / (norm_a * norm_b)


class LocalSemanticEmbeddingProvider(BaseCaseEmbeddingProvider):
    """
    Deterministic semantic embedding provider using hashed feature hashing and
    domain-specific clinical/insurance token projections into 128 dimensions.
    Operates offline without network latency or external API keys.
    """

    DIMENSION = 128

    async def generate_embedding(self, text: str) -> List[float]:
        vec = [0.0] * self.DIMENSION
        if not text or not text.strip():
            return vec

        tokens = re.findall(r"[A-Za-z0-9_]{3,}", text.lower())
        if not tokens:
            return vec

        for token in tokens:
            # Deterministic hash to dimension index
            h = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
            idx = h % self.DIMENSION
            sign = 1.0 if ((h >> 8) & 1) == 0 else -1.0

            # Weight clinical/insurance domain terms higher
            weight = 1.0
            if any(k in token for k in ("cataract", "cholelithiasis", "hernia", "surgery", "waiting", "exclusion", "robotic")):
                weight = 2.5
            elif any(k in token for k in ("covered", "rejected", "failed", "passed", "limit")):
                weight = 1.8

            vec[idx] += sign * weight

        # L2-normalize vector
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0:
            vec = [round(x / norm, 5) for x in vec]

        return vec


class GeminiCaseEmbeddingProvider(BaseCaseEmbeddingProvider):
    """
    Cloud provider using Gemini text-embedding-004 with fallback to local provider.
    """

    def __init__(self):
        self.fallback = LocalSemanticEmbeddingProvider()

    async def generate_embedding(self, text: str) -> List[float]:
        try:
            from app.core.config import get_settings
            settings = get_settings()
            api_key = getattr(settings, "GEMINI_API_KEY", None)

            if not api_key or api_key == "test-api-key":
                return await self.fallback.generate_embedding(text)

            import google.generativeai as genai
            genai.configure(api_key=api_key)
            result = genai.embed_content(
                model="models/text-embedding-004",
                content=text,
                task_type="retrieval_document",
            )
            raw_vec = result.get("embedding", [])
            return [round(float(x), 5) for x in raw_vec]
        except Exception as e:
            logger.debug(f"[CaseEmbedding] Cloud embedding fallback to local: {e}")
            return await self.fallback.generate_embedding(text)


def build_case_semantic_text(case_data: Dict[str, Any]) -> str:
    """
    Constructs standardized, PII-free semantic text representation for embedding generation.
    """
    diag = case_data.get("diagnosis") or case_data.get("normalizedDiagnosis") or "General Illness"
    treat = case_data.get("treatment") or case_data.get("normalizedTreatment") or "Medical Treatment"
    months = case_data.get("policyAgeMonths") or case_data.get("policy_age_months") or "Unknown"
    rule = ", ".join(case_data.get("applicableRuleTypes") or case_data.get("relevantRuleIds") or ["Standard Terms"])
    dec = case_data.get("decision") or case_data.get("finalExpertDecision") or case_data.get("aiDecision") or "Adjudicated"
    reason = case_data.get("reasonCode") or case_data.get("reason_code") or "Policy Terms"
    exp = case_data.get("expertExplanation") or case_data.get("explanation") or ""

    parts = [
        f"Diagnosis: {diag}",
        f"Treatment: {treat}",
        f"Policy Age: {months} months" if months != "Unknown" else "Policy Age: Current",
        f"Rule: {rule}",
        f"Decision: {dec}",
        f"Reason: {reason}",
    ]
    if exp:
        parts.append(f"Rationale: {exp[:250]}")

    return " | ".join(parts)
