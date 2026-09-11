"""
Dataset Management and Versioning Service.
Coordinates:
- Quality gate validation and database readiness audits
- Deterministic Train / Validation / Test dataset splitting (zero data leakage)
- Disk serialization into dataset/ folder structure
- Multi-format exports (Standard JSONL, Vertex AI Supervised Tuning, Evaluation Benchmarks)
- DatasetManifest persistence and version tracking
"""

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple

from app.core.config import settings
from app.core.logging import logger
from app.models.dataset_tuning import (
    DatasetManifest,
    DatasetQualityGateAudit,
    DatasetSplitType,
    TrainingExample,
)
from app.models.expert_review import (
    ClaimCase,
    ApprovedExpertCase,
    ExpertCorrection,
)
from app.models.rag_chunk import PolicyChunk
from app.services.tuning_pipeline.quality_gates import DatasetQualityGateService
from app.services.tuning_pipeline.data_cleaner import TrainingDataCleaner
from app.services.tuning_pipeline.exporters import (
    StandardJsonlExporter,
    VertexAiGeminiTuningExporter,
    EvaluationDatasetExporter,
)


class DatasetService:
    """Manages the full lifecycle of training, validation, and evaluation datasets."""

    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    DATASET_DIR = os.path.join(PROJECT_ROOT, "dataset")

    POLICY_DIR = os.path.join(DATASET_DIR, "policy")
    EXPERT_CASES_DIR = os.path.join(DATASET_DIR, "expert_cases")
    CORRECTIONS_DIR = os.path.join(DATASET_DIR, "corrections")
    EVALUATION_DIR = os.path.join(DATASET_DIR, "evaluation")
    VERSIONS_DIR = os.path.join(DATASET_DIR, "versions")

    standard_exporter = StandardJsonlExporter()
    vertex_exporter = VertexAiGeminiTuningExporter()
    eval_exporter = EvaluationDatasetExporter()

    @classmethod
    def ensure_directories(cls):
        """Ensures all 5 dataset storage directories exist."""
        for d in (
            cls.DATASET_DIR,
            cls.POLICY_DIR,
            cls.EXPERT_CASES_DIR,
            cls.CORRECTIONS_DIR,
            cls.EVALUATION_DIR,
            cls.VERSIONS_DIR,
        ):
            os.makedirs(d, exist_ok=True)

    @classmethod
    async def audit_database_readiness(cls) -> DatasetQualityGateAudit:
        """
        Scans all claim cases in MongoDB against the 8 quality gates.
        Returns a complete audit breakdown:
        - Which cases qualify for training.
        - Which cases are excluded, with specific reasons.
        """
        all_cases = await ClaimCase.find_all().to_list()
        all_approved = await ApprovedExpertCase.find_all().to_list()

        # Combine unique cases (prioritizing ApprovedExpertCase if present)
        case_map: Dict[str, Any] = {}
        for c in all_cases:
            case_map[c.case_id] = c
        for a in all_approved:
            case_map[a.case_id] = a

        passed = 0
        excluded = 0
        exclusion_breakdown: Dict[str, int] = {}
        excluded_details = []

        for cid, case_obj in case_map.items():
            res = DatasetQualityGateService.evaluate_case(case_obj)
            if res.is_eligible:
                passed += 1
            else:
                excluded += 1
                for r in res.exclusion_reasons:
                    exclusion_breakdown[r] = exclusion_breakdown.get(r, 0) + 1
                excluded_details.append({
                    "caseId": cid,
                    "reasons": res.exclusion_reasons,
                    "failedGates": res.failed_gates,
                })

        return DatasetQualityGateAudit(
            totalScanned=len(case_map),
            passedCount=passed,
            excludedCount=excluded,
            exclusionBreakdown=exclusion_breakdown,
            excludedCaseIds=excluded_details,
        )

    @classmethod
    def _deterministic_split(cls, identifier: str, split_ratios: Tuple[float, float, float]) -> DatasetSplitType:
        """
        Deterministically assigns an example to TRAIN, VALIDATION, or TEST using SHA256 hash.
        Guarantees:
        - Zero data leakage between train and test.
        - Same record is always assigned to the same split.
        """
        h = int(hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:8], 16)
        normalized = (h % 10000) / 10000.0

        train_ratio, val_ratio, _ = split_ratios
        if normalized < train_ratio:
            return DatasetSplitType.TRAIN
        elif normalized < (train_ratio + val_ratio):
            return DatasetSplitType.VALIDATION
        else:
            return DatasetSplitType.TEST

    @classmethod
    async def create_and_export_dataset(
        cls,
        version_tag: str,
        created_by: str = "claims-engineer",
        split_ratios: Tuple[float, float, float] = (0.70, 0.15, 0.15),
    ) -> DatasetManifest:
        """
        Executes full dataset creation pipeline:
        1. Runs quality gates.
        2. Filters only approved, high-quality cases.
        3. Anonymizes and formats examples.
        4. Partitions into Train / Dev / Test splits without data leakage.
        5. Exports Standard JSONL and Vertex AI Supervised Tuning formats.
        6. Snapshots policy knowledge and expert corrections.
        7. Persists DatasetManifest in MongoDB and disk.
        """
        cls.ensure_directories()

        audit = await cls.audit_database_readiness()
        logger.info(
            f"[DatasetService] Database audit complete: {audit.passedCount} passed quality gates, "
            f"{audit.excludedCount} excluded out of {audit.totalScanned} scanned."
        )

        # Collect qualifying cases from ApprovedExpertCase
        approved_cases = await ApprovedExpertCase.find(
            ApprovedExpertCase.is_active == True
        ).to_list()

        train_examples: List[TrainingExample] = []
        val_examples: List[TrainingExample] = []
        test_examples: List[TrainingExample] = []
        cat_dist: Dict[str, int] = {}

        idx = 1
        for ac in approved_cases:
            gate_res = DatasetQualityGateService.evaluate_case(ac)
            if not gate_res.is_eligible:
                continue

            split = cls._deterministic_split(ac.case_id, split_ratios)
            ex = TrainingDataCleaner.clean_and_format_example(ac, index=idx, split=split)
            idx += 1

            cat_key = ex.category.value
            cat_dist[cat_key] = cat_dist.get(cat_key, 0) + 1

            if split == DatasetSplitType.TRAIN:
                train_examples.append(ex)
            elif split == DatasetSplitType.VALIDATION:
                val_examples.append(ex)
            else:
                test_examples.append(ex)

        # File paths
        train_file = os.path.join(cls.EXPERT_CASES_DIR, f"train_{version_tag}.jsonl")
        val_file = os.path.join(cls.EXPERT_CASES_DIR, f"val_{version_tag}.jsonl")
        test_file = os.path.join(cls.EVALUATION_DIR, f"test_{version_tag}.jsonl")
        vertex_train = os.path.join(cls.EXPERT_CASES_DIR, f"vertex_train_{version_tag}.jsonl")
        vertex_val = os.path.join(cls.EXPERT_CASES_DIR, f"vertex_val_{version_tag}.jsonl")

        # Serializations
        cls.standard_exporter.export(train_examples, train_file)
        cls.standard_exporter.export(val_examples, val_file)
        cls.eval_exporter.export(test_examples, test_file)
        cls.vertex_exporter.export(train_examples, vertex_train)
        cls.vertex_exporter.export(val_examples, vertex_val)

        # ─── Snapshot Policy Knowledge & Corrections ──────────────────────────
        policy_file = os.path.join(cls.POLICY_DIR, f"policy_knowledge_{version_tag}.jsonl")
        chunks = await PolicyChunk.find_all().to_list()
        with open(policy_file, "w", encoding="utf-8") as pf:
            for chk in chunks:
                pf.write(json.dumps({
                    "chunkId": chk.chunk_id,
                    "policyId": chk.policy_id,
                    "insurer": chk.insurer,
                    "product": chk.product,
                    "variant": chk.variant,
                    "ruleType": chk.rule_type,
                    "clauseText": chk.text,
                    "authorityLevel": chk.authority_level,
                }, ensure_ascii=False) + "\n")

        corr_file = os.path.join(cls.CORRECTIONS_DIR, f"corrections_{version_tag}.jsonl")
        corrections = await ExpertCorrection.find_all().to_list()
        with open(corr_file, "w", encoding="utf-8") as cf:
            for corr in corrections:
                cf.write(json.dumps({
                    "correctionId": corr.correction_id,
                    "caseId": corr.case_id,
                    "originalDecision": corr.original_deterministic_decision,
                    "correctedDecision": corr.corrected_decision,
                    "originalReasonCode": corr.original_reason_code,
                    "correctedReasonCode": corr.corrected_reason_code,
                    "reason": corr.correction_reason,
                    "expertComment": corr.expert_comment,
                }, ensure_ascii=False) + "\n")

        # ─── Create & Persist Manifest ────────────────────────────────────────
        manifest = DatasetManifest(
            datasetVersion=version_tag,
            datasetType="TUNING_SPLIT",
            createdAt=datetime.now(timezone.utc),
            createdBy=created_by,
            sourceVersion="1.0.0",
            policyKnowledgeVersion="1.0.0",
            ruleEngineVersion="2.0.0-deterministic",
            promptVersion="1.2.0",
            modelVersion="gemini-2.5-flash",
            totalExamples=len(train_examples) + len(val_examples) + len(test_examples),
            trainCount=len(train_examples),
            validationCount=len(val_examples),
            testCount=len(test_examples),
            trainFilePath=train_file,
            validationFilePath=val_file,
            testFilePath=test_file,
            vertexTrainFilePath=vertex_train,
            vertexValidationFilePath=vertex_val,
            qualityAudit=audit,
            categoryDistribution=cat_dist,
        )

        existing_manifest = await DatasetManifest.find_one(DatasetManifest.dataset_version == version_tag)
        if existing_manifest:
            await existing_manifest.delete()
        await manifest.insert()

        # Save manifest JSON on disk
        manifest_path = os.path.join(cls.VERSIONS_DIR, f"manifest_{version_tag}.json")
        with open(manifest_path, "w", encoding="utf-8") as mf:
            mf.write(manifest.model_dump_json(indent=2, by_alias=True))

        logger.info(
            f"[DatasetService] Successfully exported dataset version '{version_tag}' with "
            f"{manifest.train_count} train, {manifest.validation_count} val, {manifest.test_count} test examples."
        )
        return manifest

    @classmethod
    async def get_manifest(cls, version_tag: str) -> Optional[DatasetManifest]:
        return await DatasetManifest.find_one(DatasetManifest.dataset_version == version_tag)

    @classmethod
    async def list_manifests(cls) -> List[DatasetManifest]:
        return await DatasetManifest.find_all().sort("-created_at").to_list()
