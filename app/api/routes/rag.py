from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List
from app.middleware.auth import get_current_user
from app.services.rag.retrieval import ask_rag_assistant
from app.core.logging import logger

router = APIRouter(prefix="/rag", tags=["RAG"])

class RagQueryRequest(BaseModel):
    policyId: str
    prescriptionId: str
    question: str

class Source(BaseModel):
    documentType: str
    pageNumber: int

class RagQueryResponse(BaseModel):
    answer: str
    sources: List[Source]

@router.post("/query", response_model=RagQueryResponse)
async def query_rag(request: RagQueryRequest, current_user: dict = Depends(get_current_user)):
    """
    Queries the RAG Assistant using a specific policy and prescription.
    """
    try:
        result = await ask_rag_assistant(
            question=request.question,
            user_id=current_user["id"],
            policy_id=request.policyId,
            prescription_id=request.prescriptionId
        )
        return RagQueryResponse(
            answer=result["answer"],
            sources=[
                Source(
                    documentType=src.get("documentType", "unknown"),
                    pageNumber=src.get("pageNumber", 1)
                ) for src in result["sources"]
            ]
        )
    except ValueError as ve:
        logger.error(f"RAG query validation error: {ve}")
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"RAG query failed: {e}")
        raise HTTPException(status_code=500, detail="An error occurred while processing your request.")
