"""
REST API Endpoints for Expert Review and Feedback Learning Workflow.
Provides APIs for listing pending reviews, viewing original AI and deterministic outputs,
accepting/correcting cases, viewing full audit history, and querying approved cases.
"""

from typing import Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.services.expert_learning.expert_case_service import ExpertCaseService

router = APIRouter(prefix="/expert-review", tags=["Expert Review & Learning"])


class AcceptCaseRequest(BaseModel):
    expert_id: str = Field(default="expert-reviewer", alias="expertId")
    comment: Optional[str] = Field(default="Approved without changes.")

    class Config:
        populate_by_name = True


class CorrectCaseRequest(BaseModel):
    corrected_decision: str = Field(alias="correctedDecision")
    corrected_reason_code: str = Field(alias="correctedReasonCode")
    correction_reason: str = Field(alias="correctionReason")
    expert_comment: Optional[str] = Field(default="", alias="expertComment")
    expert_id: str = Field(default="expert-reviewer", alias="expertId")

    class Config:
        populate_by_name = True


class RejectCaseRequest(BaseModel):
    expert_id: str = Field(default="expert-reviewer", alias="expertId")
    comment: Optional[str] = Field(default="Rejected by expert.")

    class Config:
        populate_by_name = True


@router.get("/pending", status_code=status.HTTP_200_OK)
async def list_pending_reviews(
    limit: int = Query(default=50, ge=1, le=200),
    skip: int = Query(default=0, ge=0),
):
    """Lists claim cases waiting for expert review."""
    cases = await ExpertCaseService.list_pending_cases(limit=limit, skip=skip)
    return {
        "count": len(cases),
        "cases": [c.model_dump(by_alias=True) for c in cases],
    }


@router.get("/cases/{case_id}", status_code=status.HTTP_200_OK)
async def get_case_details(case_id: str):
    """Retrieves full case details, original AI output, deterministic result, and evidence."""
    details = await ExpertCaseService.get_case_details(case_id)
    if not details:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case with ID '{case_id}' was not found.",
        )
    return details


@router.post("/cases/{case_id}/accept", status_code=status.HTTP_200_OK)
async def accept_case(case_id: str, payload: AcceptCaseRequest):
    """
    Expert accepts the initial AI / deterministic analysis.
    Case transitions to APPROVED and enters the trusted expert knowledge base.
    """
    try:
        updated = await ExpertCaseService.accept_case(
            case_id=case_id,
            expert_id=payload.expert_id,
            comment=payload.comment or "Approved without changes.",
        )
        return {
            "status": "success",
            "message": f"Case '{case_id}' accepted and indexed into expert knowledge base.",
            "case": updated.model_dump(by_alias=True),
        }
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.post("/cases/{case_id}/correct", status_code=status.HTTP_200_OK)
async def correct_case(case_id: str, payload: CorrectCaseRequest):
    """
    Expert corrects the claim decision.
    Original decision is preserved, correction delta is stored, and corrected case enters knowledge base.
    """
    try:
        updated = await ExpertCaseService.correct_case(
            case_id=case_id,
            corrected_decision=payload.corrected_decision,
            corrected_reason_code=payload.corrected_reason_code,
            correction_reason=payload.correction_reason,
            expert_comment=payload.expert_comment or "",
            expert_id=payload.expert_id,
        )
        return {
            "status": "success",
            "message": f"Case '{case_id}' corrected and updated in expert knowledge base.",
            "case": updated.model_dump(by_alias=True),
        }
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.post("/cases/{case_id}/reject", status_code=status.HTTP_200_OK)
async def reject_case(case_id: str, payload: RejectCaseRequest):
    """
    Expert rejects the case. Strictly excluded from future expert knowledge retrieval.
    """
    try:
        updated = await ExpertCaseService.reject_case(
            case_id=case_id,
            expert_id=payload.expert_id,
            comment=payload.comment or "Rejected by expert.",
        )
        return {
            "status": "success",
            "message": f"Case '{case_id}' rejected. Excluded from knowledge base.",
            "case": updated.model_dump(by_alias=True),
        }
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.get("/cases/{case_id}/history", status_code=status.HTTP_200_OK)
async def get_case_history(case_id: str):
    """Retrieves full immutable audit trail of versions, reviews, and corrections for a case."""
    details = await ExpertCaseService.get_case_details(case_id)
    if not details:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case with ID '{case_id}' was not found.",
        )
    return {
        "caseId": case_id,
        "versions": details["versions"],
        "reviews": details["reviews"],
        "corrections": details["corrections"],
    }


@router.get("/approved", status_code=status.HTTP_200_OK)
async def list_approved_cases(
    limit: int = Query(default=50, ge=1, le=200),
    skip: int = Query(default=0, ge=0),
):
    """Lists all de-identified approved expert cases in the knowledge base."""
    cases = await ExpertCaseService.list_approved_cases(limit=limit, skip=skip)
    return {
        "count": len(cases),
        "approvedCases": [c.model_dump(by_alias=True) for c in cases],
    }
