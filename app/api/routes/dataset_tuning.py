"""
REST API Endpoints for Dataset Pipeline, Tuning Preparation & Evaluation Framework.
Provides:
- Quality gate audits (which cases qualify, which are excluded and why)
- Dataset export triggers with deterministic train/dev/test splits
- Version manifests and file downloads
- Evaluation runner and historical performance reporting
- Vertex AI tuning readiness checklists
"""

import os
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.services.tuning_pipeline.dataset_service import DatasetService
from app.services.tuning_pipeline.evaluator import ClaimEvaluationRunner
from app.services.tuning_pipeline.vertex_adapter import VertexAiTuningPlaceholder

router = APIRouter(prefix="/tuning", tags=["Dataset Pipeline & Model Evaluation"])


class CreateDatasetRequest(BaseModel):
    version_tag: str = Field(default="v1.0.0", alias="versionTag")
    created_by: str = Field(default="claims-engineer", alias="createdBy")
    train_ratio: float = Field(default=0.70, ge=0.1, le=0.9, alias="trainRatio")
    val_ratio: float = Field(default=0.15, ge=0.05, le=0.5, alias="valRatio")
    test_ratio: float = Field(default=0.15, ge=0.05, le=0.5, alias="testRatio")

    class Config:
        populate_by_name = True


class RunEvaluationRequest(BaseModel):
    dataset_version: str = Field(default="benchmark-v1", alias="datasetVersion")
    model_name: str = Field(default="gemini-2.5-flash", alias="modelName")
    prompt_version: str = Field(default="1.2.0", alias="promptVersion")
    rule_engine_version: str = Field(default="2.0.0-deterministic", alias="ruleEngineVersion")

    class Config:
        populate_by_name = True


@router.get("/quality-audit", status_code=status.HTTP_200_OK)
async def get_dataset_quality_audit():
    """
    Answers:
    - Which expert-approved cases can be used for training?
    - Which cases are excluded and for what specific reasons?
    """
    audit = await DatasetService.audit_database_readiness()
    return {
        "success": True,
        "audit": audit.model_dump(by_alias=True),
    }


@router.post("/datasets/create", status_code=status.HTTP_201_CREATED)
async def create_dataset_version(payload: CreateDatasetRequest):
    """
    Applies quality gates, partitions into Train/Val/Test with zero leakage,
    exports Standard JSONL and Vertex AI formats, and records manifest.
    """
    split_ratios = (payload.train_ratio, payload.val_ratio, payload.test_ratio)
    manifest = await DatasetService.create_and_export_dataset(
        version_tag=payload.version_tag,
        created_by=payload.created_by,
        split_ratios=split_ratios,
    )
    return {
        "success": True,
        "message": f"Dataset version '{payload.version_tag}' created and exported.",
        "manifest": manifest.model_dump(by_alias=True),
    }


@router.get("/datasets/versions", status_code=status.HTTP_200_OK)
async def list_dataset_versions():
    """Lists all created dataset versions and manifests."""
    manifests = await DatasetService.list_manifests()
    return {
        "count": len(manifests),
        "versions": [m.model_dump(by_alias=True) for m in manifests],
    }


@router.get("/datasets/{version}/download", status_code=status.HTTP_200_OK)
async def download_dataset_file(
    version: str,
    file_type: str = Query(default="train", regex="^(train|val|test|vertex_train|vertex_val|policy|corrections)$")
):
    """Downloads an exported dataset file."""
    manifest = await DatasetService.get_manifest(version)
    if not manifest:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Dataset version '{version}' not found.")

    path_map = {
        "train": manifest.train_file_path,
        "val": manifest.validation_file_path,
        "test": manifest.test_file_path,
        "vertex_train": manifest.vertex_train_file_path,
        "vertex_val": manifest.vertex_validation_file_path,
        "policy": os.path.join(DatasetService.POLICY_DIR, f"policy_knowledge_{version}.jsonl"),
        "corrections": os.path.join(DatasetService.CORRECTIONS_DIR, f"corrections_{version}.jsonl"),
    }

    file_path = path_map.get(file_type)
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"File type '{file_type}' for version '{version}' not found.")

    filename = os.path.basename(file_path)
    return FileResponse(path=file_path, filename=filename, media_type="application/x-ndjson")


@router.post("/evaluation/run", status_code=status.HTTP_200_OK)
async def run_evaluation_benchmark(payload: RunEvaluationRequest):
    """
    Executes the evaluation benchmark across all 12 insurance categories.
    Computes overall accuracy, false approvals (critical risk), false rejections,
    and category breakdowns.
    """
    report = await ClaimEvaluationRunner.run_evaluation(
        dataset_version=payload.dataset_version,
        model_name=payload.model_name,
        prompt_version=payload.prompt_version,
        rule_engine_version=payload.rule_engine_version,
    )
    return {
        "success": True,
        "runId": report.run_id,
        "metrics": {
            "overallAccuracy": report.overall_accuracy,
            "coveredCaseAccuracy": report.covered_case_accuracy,
            "exclusionAccuracy": report.exclusion_accuracy,
            "waitingPeriodAccuracy": report.waiting_period_accuracy,
            "totalFalseApprovals": report.total_false_approvals,
            "falseApprovalRate": report.false_approval_rate,
            "totalFalseRejections": report.total_false_rejections,
            "falseRejectionRate": report.false_rejection_rate,
            "manualReviewRate": report.manual_review_rate,
            "evidenceAccuracy": report.evidence_accuracy,
            "totalCasesEvaluated": report.total_cases_evaluated,
        },
        "categoryMetrics": {k: v.model_dump() for k, v in report.category_metrics.items()},
        "report": report.model_dump(by_alias=True),
    }


@router.get("/evaluation/history", status_code=status.HTTP_200_OK)
async def get_evaluation_history(limit: int = Query(default=20, ge=1, le=100)):
    """Retrieves historical evaluation runs to compare model and rule improvements."""
    runs = await ClaimEvaluationRunner.get_run_history(limit=limit)
    return {
        "count": len(runs),
        "history": [r.model_dump(by_alias=True) for r in runs],
    }


@router.get("/vertex/readiness", status_code=status.HTTP_200_OK)
async def get_vertex_tuning_readiness():
    """Returns governance checklist and readiness status for future Vertex AI tuning."""
    audit = await DatasetService.audit_database_readiness()
    checklist = VertexAiTuningPlaceholder.get_readiness_checklist(audit.passedCount)
    return {
        "success": True,
        "readiness": checklist,
    }
