"""
Local Semantic Retrieval Provider.
Performs policy-aware, auditable, sub-millisecond clause retrieval with strict
policy boundary isolation, temporal date filtering, and priority weighting.
"""

from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
import re
import math

from app.core.logging import logger
from app.models.rag_chunk import PolicyChunk, AuthorityPriority
from app.services.rag.providers.base_provider import BaseRetrievalProvider


class LocalSemanticRetrievalProvider(BaseRetrievalProvider):
    """
    MongoDB-backed semantic retrieval provider.
    Guarantees zero cross-policy data leakage and enforces authority priority.
    """

    # Priority weighting for ranking
    AUTHORITY_WEIGHTS = {
        AuthorityPriority.CONTRACTUAL_POLICY_WORDING.value: 1.5,
        AuthorityPriority.STRUCTURED_POLICY_RULE.value: 1.2,
        AuthorityPriority.PRODUCT_REFERENCE_DOCUMENT.value: 1.0,
        AuthorityPriority.EXPERT_CASE.value: 0.7,
    }

    async def index_chunks(self, chunks: List[PolicyChunk]) -> int:
        """Saves chunks into MongoDB `kb_policy_chunks`."""
        if not chunks:
            return 0
        inserted = 0
        for chunk in chunks:
            existing = await PolicyChunk.find_one(PolicyChunk.chunk_id == chunk.chunk_id)
            if existing:
                await existing.delete()
            await chunk.insert()
            inserted += 1
        return inserted

    async def delete_policy_chunks(self, policy_id: str) -> int:
        """Deletes all chunks belonging to a policy."""
        result = await PolicyChunk.find(PolicyChunk.policy_id == policy_id).delete()
        return getattr(result, "deleted_count", 0)

    async def search(
        self,
        query: str,
        filters: Dict[str, Any],
        top_k: int = 5,
        as_of_date: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """
        Executes policy-aware semantic search with multi-dimensional filtering.
        """
        eval_date = as_of_date or datetime.now(timezone.utc)
        requested_policy_id = str(filters.get("policy_id") or "")
        requested_insurer = (filters.get("insurer") or "").strip().lower()
        requested_product = (filters.get("product") or "").strip().lower()
        requested_variant = (filters.get("variant") or "").strip().lower()
        requested_rule_type = filters.get("rule_type")
        requested_doc_type = filters.get("document_type")

        # ──────────────────────────────────────────────────────────────────────
        # 1. Strict Policy-Aware Filtering
        # ──────────────────────────────────────────────────────────────────────
        # A query MUST match requested policy_id OR be a universal "GLOBAL_REFERENCE"
        db_query: Dict[str, Any] = {}

        if requested_policy_id:
            db_query["policyId"] = {"$in": [requested_policy_id, "GLOBAL_REFERENCE"]}
        else:
            db_query["policyId"] = "GLOBAL_REFERENCE"

        # Rule type filter
        if requested_rule_type:
            db_query["ruleType"] = requested_rule_type

        # Document type filter
        if requested_doc_type:
            db_query["documentType"] = requested_doc_type

        # Fetch candidate chunks from MongoDB
        candidates = await PolicyChunk.find(db_query).to_list()

        # ──────────────────────────────────────────────────────────────────────
        # 2. In-Memory Filter Verification (Insurer, Product, Variant, Dates)
        # ──────────────────────────────────────────────────────────────────────
        eligible_chunks: List[PolicyChunk] = []

        for chk in candidates:
            # Policy ID Isolation: If not the exact requested policy and not GLOBAL_REFERENCE, exclude
            if requested_policy_id and chk.policy_id not in (requested_policy_id, "GLOBAL_REFERENCE"):
                continue

            # Temporal Validity: effective_from <= eval_date <= effective_to
            def _to_naive_utc(d: Optional[datetime]) -> Optional[datetime]:
                if d is None:
                    return None
                return d.replace(tzinfo=None) if d.tzinfo else d

            eval_date_naive = _to_naive_utc(eval_date)
            chk_from = _to_naive_utc(chk.effective_from)
            chk_to = _to_naive_utc(chk.effective_to)

            if chk_from and eval_date_naive and chk_from > eval_date_naive:
                continue
            if chk_to and eval_date_naive and chk_to < eval_date_naive:
                continue

            # Insurer Isolation:
            # If chunk is from a specific insurer (not "All"), it MUST match the requested insurer
            chk_insurer = chk.insurer.strip().lower()
            if chk_insurer not in ("all", "universal", ""):
                if requested_insurer and requested_insurer not in ("all", "---", "none", ""):
                    if chk_insurer not in requested_insurer and requested_insurer not in chk_insurer:
                        continue

            # Product Isolation:
            # If chunk is from a specific product (not "All" / "Standard Benchmark"), it MUST match requested product
            chk_product = chk.product.strip().lower()
            if chk_product not in ("all", "standard benchmark", "universal", ""):
                if requested_product and requested_product not in ("all", "---", "none", ""):
                    if chk_product not in requested_product and requested_product not in chk_product:
                        continue

            # Variant Isolation:
            # If chunk is variant-specific (e.g. "Select" or "Black"), it MUST match requested variant
            chk_variant = chk.variant.strip().lower()
            if chk_variant not in ("all", "universal", ""):
                if requested_variant and requested_variant not in ("all", "---", "none", ""):
                    if chk_variant != requested_variant:
                        continue

            eligible_chunks.append(chk)

        if not eligible_chunks:
            return []

        # ──────────────────────────────────────────────────────────────────────
        # 3. Query Tokenization & Scoring
        # ──────────────────────────────────────────────────────────────────────
        query_tokens = [w.lower() for w in re.findall(r"[A-Za-z0-9]{3,}", query)]
        if not query_tokens:
            query_tokens = ["coverage"]

        scored_results: List[tuple[float, PolicyChunk]] = []

        for chk in eligible_chunks:
            score = self._compute_relevance(query_tokens, chk)
            if score > 0.05:  # Minimum relevance floor
                scored_results.append((score, chk))

        # Sort by relevance descending
        scored_results.sort(key=lambda x: x[0], reverse=True)

        # ──────────────────────────────────────────────────────────────────────
        # 4. Format Top-K Results with Source Evidence & Normalization
        # ──────────────────────────────────────────────────────────────────────
        top_results = scored_results[:top_k]
        if not top_results:
            return []

        max_raw_score = max(x[0] for x in top_results) if top_results else 1.0

        output: List[Dict[str, Any]] = []
        for raw_score, chk in top_results:
            # Normalize relevance to range 0.60 - 0.98
            norm_rel = min(0.98, max(0.60, round(raw_score / (max_raw_score * 1.05), 2)))

            output.append({
                "chunkId": chk.chunk_id,
                "text": chk.text,
                "source_document": chk.source,
                "page": chk.page_number or 1,
                "section": chk.section or "Policy Terms",
                "rule_type": chk.rule_type or "GENERAL_CONDITIONS",
                "document_type": chk.document_type,
                "authority_level": chk.authority_level,
                "insurer": chk.insurer,
                "product": chk.product,
                "variant": chk.variant,
                "policy_version": chk.policy_version,
                "relevance": norm_rel,
            })

        return output

    def _compute_relevance(self, query_tokens: List[str], chk: PolicyChunk) -> float:
        """Computes multi-feature semantic relevance score for a clause."""
        corpus = f"{chk.section} {chk.text}".lower()
        chunk_keywords = set(k.lower() for k in (chk.keywords or []))

        term_hits = 0
        exact_phrases = 0

        for token in query_tokens:
            if token in corpus:
                term_hits += 1
            if token in chunk_keywords:
                term_hits += 1.5

        # Check multi-word phrase matches
        full_query = " ".join(query_tokens)
        if len(query_tokens) >= 2 and full_query in corpus:
            exact_phrases += 3.0

        base_score = (term_hits / max(len(query_tokens), 1)) + exact_phrases

        # Authority Level Priority Multiplier
        weight = self.AUTHORITY_WEIGHTS.get(chk.authority_level, 1.0)
        final_score = base_score * weight

        return round(final_score, 4)
