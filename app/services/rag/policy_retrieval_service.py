"""
Policy Retrieval Service.
Coordinates policy-aware RAG, semantic chunking, multi-provider search,
source evidence preservation, and TTL caching.
"""

from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
import hashlib
import json

from app.core.config import get_settings
from app.core.logging import logger
from app.models.rag_chunk import PolicyChunk, AuthorityPriority, DocumentType
from app.services.rag.clause_chunker import ClauseChunker
from app.services.rag.providers.base_provider import BaseRetrievalProvider
from app.services.rag.providers.local_provider import LocalSemanticRetrievalProvider
from app.services.rag.providers.gemini_provider import GeminiRetrievalProvider
from app.models.policy import Policy


# In-memory retrieval cache with TTL
_retrieval_cache: Dict[str, tuple[datetime, List[Dict[str, Any]]]] = {}


class PolicyRetrievalService:
    """Service handling policy document indexing, caching, and contextual clause retrieval."""

    @classmethod
    def get_provider(cls, provider_name: Optional[str] = None) -> BaseRetrievalProvider:
        """Returns the configured retrieval provider."""
        settings = get_settings()
        name = (provider_name or getattr(settings, "RAG_PROVIDER", "local")).lower()
        if name == "gemini":
            return GeminiRetrievalProvider()
        return LocalSemanticRetrievalProvider()

    @classmethod
    def clear_cache(cls) -> int:
        """Clears the in-memory retrieval cache."""
        global _retrieval_cache
        count = len(_retrieval_cache)
        _retrieval_cache.clear()
        return count

    # ──────────────────────────────────────────────────────────────────────────
    # Indexing Operations
    # ──────────────────────────────────────────────────────────────────────────
    @classmethod
    async def index_policy_document(
        cls,
        policy_doc: Policy,
        document_type: str = "policy wording",
        source_name: Optional[str] = None,
    ) -> int:
        """
        Chunks and indexes extracted policy wording from a customer's Policy model.
        Tagged with CONTRACTUAL_POLICY_WORDING (Priority 1).
        """
        text = policy_doc.extracted_policy_text or ""
        if not text:
            return 0

        p_id = str(policy_doc.id)
        meta = {
            "policy_id": p_id,
            "insurer": policy_doc.insurance_company or "Unknown Insurer",
            "product": policy_doc.policy_name or "Policy",
            "variant": policy_doc.policy_type or "All",
            "policyVersion": (policy_doc.extracted_policy_json or {}).get("policyVersion", "1.0"),
            "documentId": str(policy_doc.grid_fs_file_id or p_id),
            "effectiveFrom": policy_doc.policy_start_date,
            "effectiveTo": policy_doc.policy_end_date,
            "authorityLevel": AuthorityPriority.CONTRACTUAL_POLICY_WORDING.value,
        }

        source = source_name or policy_doc.original_file_name or "Policy_Wording.pdf"
        chunk_dicts = ClauseChunker.chunk_policy_text(
            text=text,
            policy_meta=meta,
            document_type=document_type,
            source_name=source,
        )

        chunks = [PolicyChunk(**cd) for cd in chunk_dicts]
        provider = cls.get_provider()
        indexed_count = await provider.index_chunks(chunks)
        logger.info(f"[PolicyRAG] Indexed {indexed_count} contractual clauses for policy {p_id}")
        return indexed_count

    @classmethod
    async def index_policy_text(
        cls,
        policy_id: str,
        text: str,
        policy_meta: Dict[str, Any],
        document_type: str = "policy wording",
        source_name: str = "Policy_Document.pdf",
    ) -> int:
        """Indexes raw policy text for a specific policy_id."""
        if not text or not text.strip():
            return 0

        policy_meta["policy_id"] = policy_id
        if "authorityLevel" not in policy_meta:
            policy_meta["authorityLevel"] = AuthorityPriority.CONTRACTUAL_POLICY_WORDING.value

        chunk_dicts = ClauseChunker.chunk_policy_text(
            text=text,
            policy_meta=policy_meta,
            document_type=document_type,
            source_name=source_name,
        )

        chunks = [PolicyChunk(**cd) for cd in chunk_dicts]
        provider = cls.get_provider()
        return await provider.index_chunks(chunks)

    @classmethod
    async def index_knowledge_base_reference_data(cls, force_refresh: bool = False) -> int:
        """
        Indexes standard reference datasets (ReAssure 3.0 leaflets, 2-year waiting periods,
        and 32 permanent exclusions) under 'GLOBAL_REFERENCE' for universal reference matching.
        """
        existing_count = await PolicyChunk.find(PolicyChunk.policy_id == "GLOBAL_REFERENCE").count()
        if existing_count > 0 and not force_refresh:
            return existing_count

        from app.data.reference.reference_benchmarks import (
            SPECIFIC_2_YEAR_WAITING_CONDITIONS,
            PERMANENT_EXCLUSIONS_CATALOG,
            BENCHMARK_POLICY_SPEC,
        )

        ref_chunks: List[PolicyChunk] = []
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)

        # 1. Index 14 Waiting Period Conditions
        for i, cond in enumerate(SPECIFIC_2_YEAR_WAITING_CONDITIONS, 1):
            text = (
                f"2 YEAR'S SPECIFIC WAITING PERIOD: {cond['category']}.\n"
                f"Rule: {cond['standardRule']}\n"
                f"Keywords: {', '.join(cond['keywords'])}"
            )
            ref_chunks.append(PolicyChunk(
                chunkId=f"CHK-REF-WP-{i:03d}",
                policyId="GLOBAL_REFERENCE",
                insurer="All",
                product="Standard Benchmark",
                variant="All",
                policyVersion="2026.01",
                documentType=DocumentType.WAITING_PERIOD_DOCUMENT.value,
                pageNumber=1,
                section="2 YEAR'S SPECIFIC WAITING PERIODS",
                ruleType="WAITING_PERIOD",
                effectiveFrom=now,
                source="2 YEAR & Permanent Exclusion-2.pdf",
                text=text,
                authorityLevel=AuthorityPriority.STRUCTURED_POLICY_RULE.value,
                keywords=cond["keywords"],
            ))

        # 2. Index 32 Permanent Exclusions
        for excl in PERMANENT_EXCLUSIONS_CATALOG:
            num = excl["num"]
            name = excl["name"]
            text = (
                f"PERMANENT EXCLUSION #{num}: {name}.\n"
                f"Expenses relating to {name} are permanently non-payable under standard non-life health insurance policies."
            )
            ref_chunks.append(PolicyChunk(
                chunkId=f"CHK-REF-EXCL-{num:03d}",
                policyId="GLOBAL_REFERENCE",
                insurer="All",
                product="Standard Benchmark",
                variant="All",
                policyVersion="2026.01",
                documentType=DocumentType.EXCLUSION_DOCUMENT.value,
                pageNumber=2,
                section="PERMANENT EXCLUSION - NOT COVERED",
                ruleType="PERMANENT_EXCLUSION",
                effectiveFrom=now,
                source="2 YEAR & Permanent Exclusion-2.pdf",
                text=text,
                authorityLevel=AuthorityPriority.STRUCTURED_POLICY_RULE.value,
                keywords=excl["keywords"],
            ))

        # 3. Index ReAssure 3.0 Variant Benchmarks
        leaflet_variants = [
            ("Classic", "General room", "Road Ambulance INR 2000, Air Ambulance NA", "Modern Treatment INR 1 Lac"),
            ("Select", "Twin Sharing room", "Road Ambulance Up to SI, Air Ambulance 5L", "Modern Treatment Up to SI"),
            ("Elite", "All rooms except Deluxe/Suite", "Road Ambulance Up to SI, Air Ambulance 5L", "Modern Treatment Up to SI"),
            ("Black", "All rooms (zero capping)", "Road Ambulance Up to SI, Air Ambulance 5L", "Modern Treatment Up to SI"),
        ]
        for v_name, room, amb, modern in leaflet_variants:
            v_text = (
                f"Niva Bupa ReAssure 3.0 Plan Tier: {v_name}.\n"
                f"Room Category: {room}.\n"
                f"Ambulance: {amb}.\n"
                f"Modern Treatments: {modern}.\n"
                f"Hospitalization: Covered for hospital stays lasting 2+ hours (AYUSH 24+ hours).\n"
                f"Consumables: 100% covered under Claim Safeguard+ (Lists I-IV)."
            )
            ref_chunks.append(PolicyChunk(
                chunkId=f"CHK-REF-VAR-{v_name.upper()}",
                policyId="GLOBAL_REFERENCE",
                insurer="Niva Bupa Health Insurance Company Limited",
                product="ReAssure 3.0",
                variant=v_name,
                policyVersion="2026.01",
                documentType=DocumentType.PRODUCT_BROCHURE.value,
                pageNumber=3,
                section="VARIANT COMPARISON",
                ruleType="BENEFIT",
                effectiveFrom=now,
                source="Mail - ClaimSupport 2.pdf",
                text=v_text,
                authorityLevel=AuthorityPriority.PRODUCT_REFERENCE_DOCUMENT.value,
                keywords=[v_name.lower(), "room rent", "ambulance", "modern treatment", "safeguard"],
            ))

        provider = cls.get_provider()
        inserted = await provider.index_chunks(ref_chunks)
        logger.info(f"[PolicyRAG] Seeded {inserted} global reference chunks into RAG index")
        return inserted

    # ──────────────────────────────────────────────────────────────────────────
    # Context Retrieval Operations
    # ──────────────────────────────────────────────────────────────────────────
    @classmethod
    async def retrieve_policy_context(
        cls,
        policy_id: Optional[str],
        claim: Dict[str, Any],
        top_k: int = 5,
        as_of_date: Optional[datetime] = None,
        policy_metadata: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Retrieves top_k most relevant clauses for the current claim.
        Enforces policy isolation, authority weighting, and caching.
        """
        settings = get_settings()
        cache_enabled = getattr(settings, "RAG_CACHE_ENABLED", True)
        cache_ttl = getattr(settings, "RAG_CACHE_TTL_SECONDS", 3600)

        # 1. Build clinical search query
        query_terms = []
        if claim.get("diagnosis"):
            query_terms.append(str(claim["diagnosis"]))
        if claim.get("symptoms"):
            query_terms.extend([str(s) for s in claim["symptoms"] if s])
        if claim.get("procedures"):
            query_terms.extend([str(p) for p in claim["procedures"] if p])
        if claim.get("medicines"):
            for m in claim["medicines"]:
                query_terms.append(m.get("name") if isinstance(m, dict) else str(m))
        if claim.get("manualText"):
            query_terms.append(str(claim["manualText"]))

        query_str = " ".join(query_terms).strip()
        if not query_str:
            query_str = "medical insurance coverage eligibility benefits"

        # 2. Check in-memory cache
        cache_key = ""
        if cache_enabled:
            p_id_str = str(policy_id or "global")
            raw_key = f"{p_id_str}:{query_str}:{top_k}:{as_of_date}"
            cache_key = hashlib.sha256(raw_key.encode()).hexdigest()
            if cache_key in _retrieval_cache:
                cached_time, cached_results = _retrieval_cache[cache_key]
                if (datetime.now(timezone.utc) - cached_time).total_seconds() < cache_ttl:
                    logger.debug(f"[PolicyRAG] Cache hit for query: '{query_str[:40]}...'")
                    return cached_results

        # 3. Formulate filters
        meta = policy_metadata or {}
        filters: Dict[str, Any] = {
            "policy_id": policy_id,
            "insurer": meta.get("insuranceCompany") or meta.get("insurer") or "",
            "product": meta.get("policyName") or meta.get("product") or "",
            "variant": meta.get("policyType") or meta.get("variant") or "",
        }

        # 4. Execute search via provider
        provider = cls.get_provider()
        results = await provider.search(
            query=query_str,
            filters=filters,
            top_k=top_k,
            as_of_date=as_of_date,
        )

        # 5. Store in cache
        if cache_enabled and cache_key:
            _retrieval_cache[cache_key] = (datetime.now(timezone.utc), results)

        logger.info(
            f"[PolicyRAG] Retrieved {len(results)} relevant clauses for policy_id={policy_id} "
            f"(query: '{query_str[:40]}...')"
        )
        return results
