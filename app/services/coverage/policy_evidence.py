"""Rank policy clauses on demand without storing duplicate document text."""

from collections.abc import Iterable
import re

from app.services.rag.clause_chunker import ClauseChunker


def policy_chunks(text: str, policy: dict, policy_id: str = "", source_name: str = "Policy wording",
                  document_type: str = "policy wording") -> list[dict]:
    return ClauseChunker.chunk_policy_text(
        text,
        {
            "policy_id": policy_id or "policy",
            "insurer": policy.get("insuranceCompany") or "Policy",
            "product": policy.get("policyName") or "Policy",
            "variant": policy.get("policyType") or "All",
            "policyVersion": str(policy.get("policyVersion") or "1.0"),
            "effectiveFrom": policy.get("policyStartDate"),
            "effectiveTo": policy.get("policyEndDate"),
        },
        document_type=document_type,
        source_name=source_name,
        min_chunk_chars=20,
    )


def ranked_clauses(chunks: Iterable[dict], query: Iterable[str], top_k: int) -> list[dict]:
    words = set(re.findall(r"[^\W_]{3,}", " ".join(query).casefold())) - {"the", "and", "with", "for"}
    if not words:
        return []
    ranked = []
    for chunk in chunks:
        text = chunk["text"]
        section = chunk.get("section") or "Policy Terms"
        text_tokens = set(re.findall(r"[^\W_]{3,}", text.casefold()))
        heading_tokens = set(re.findall(r"[^\W_]{3,}", section.casefold()))
        score = (len(words & text_tokens) + len(words & heading_tokens)) / (2 * len(words))
        if score:
            ranked.append((score, {
                "chunkId": chunk["chunkId"], "text": text, "source_document": chunk["source"],
                "page": chunk.get("pageNumber"), "section": section, "rule_type": chunk.get("ruleType"),
                "document_type": chunk.get("documentType"), "authority_level": chunk.get("authorityLevel"),
                "insurer": chunk.get("insurer"), "product": chunk.get("product"), "variant": chunk.get("variant"),
                "policy_version": chunk.get("policyVersion"), "relevance": round(score, 4),
            }))
    ranked.sort(key=lambda row: -row[0])
    return [row for _, row in ranked[:top_k]]
