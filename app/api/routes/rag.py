"""
Policy RAG / File Search Endpoints.
Provides RESTful APIs for policy-aware clause retrieval, document indexing,
chunk inspection, and cache management.
"""

from typing import Optional, List, Dict, Any
from datetime import datetime
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.services.rag.policy_retrieval_service import PolicyRetrievalService
from app.models.rag_chunk import PolicyChunk
from app.models.policy import Policy

router = APIRouter(prefix="/rag", tags=["Policy RAG & File Search"])


class ClaimContextRequest(BaseModel):
    policyId: Optional[str] = Field(default=None, description="Customer policy ID or leave empty for global reference")
    claim: Dict[str, Any] = Field(..., description="Diagnosis, symptoms, procedures, and medicines")
    topK: Optional[int] = Field(default=5, ge=1, le=20)
    asOfDate: Optional[datetime] = None
    policyMetadata: Optional[Dict[str, Any]] = None


class IndexPolicyRequest(BaseModel):
    policyId: str
    documentType: Optional[str] = "policy wording"
    sourceName: Optional[str] = None


@router.post("/retrieve", summary="Retrieve relevant policy clauses for a claim")
async def retrieve_clauses(payload: ClaimContextRequest):
    try:
        results = await PolicyRetrievalService.retrieve_policy_context(
            policy_id=payload.policyId,
            claim=payload.claim,
            top_k=payload.topK or 5,
            as_of_date=payload.asOfDate,
            policy_metadata=payload.policyMetadata,
        )
        return {
            "success": True,
            "policyId": payload.policyId or "GLOBAL_REFERENCE",
            "count": len(results),
            "clauses": results,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Retrieval failed: {str(e)}")


@router.post("/index-policy", summary="Index an existing Policy document into RAG chunks")
async def index_policy(payload: IndexPolicyRequest):
    try:
        from bson import ObjectId
        policy_doc = await Policy.get(ObjectId(payload.policyId))
    except Exception:
        policy_doc = await Policy.find_one(Policy.policy_number == payload.policyId)

    if not policy_doc:
        raise HTTPException(status_code=404, detail=f"Policy '{payload.policyId}' not found")

    count = await PolicyRetrievalService.index_policy_document(
        policy_doc=policy_doc,
        document_type=payload.documentType or "policy wording",
        source_name=payload.sourceName,
    )
    return {"success": True, "policyId": str(policy_doc.id), "indexedChunks": count}


@router.get("/chunks/{policy_id}", summary="Inspect indexed chunks for a policy (auditability)")
async def get_policy_chunks(policy_id: str):
    chunks = await PolicyChunk.find(PolicyChunk.policy_id == policy_id).to_list()
    return {"success": True, "policyId": policy_id, "count": len(chunks), "chunks": chunks}


@router.post("/seed-reference", summary="Seed reference benchmark documents into RAG index")
async def seed_reference_data(force: bool = False):
    count = await PolicyRetrievalService.index_knowledge_base_reference_data(force_refresh=force)
    return {"success": True, "message": "Reference benchmark documents indexed", "chunksIndexed": count}


@router.delete("/cache", summary="Clear the in-memory retrieval cache")
async def clear_cache():
    cleared = PolicyRetrievalService.clear_cache()
    return {"success": True, "message": f"Cleared {cleared} cached retrieval queries"}
