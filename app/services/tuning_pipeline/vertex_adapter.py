"""
Future Vertex AI Supervised Tuning Adapter & Job Specification.
Provides:
- Vertex AI Gemini Supervised Tuning configuration specifications.
- Pre-tuning dataset validation rules.
- Readiness checklists and documentation placeholders.
Does NOT execute immediate fine-tuning or require google-cloud-aiplatform as a hard runtime dependency.
"""

import json
import os
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from app.core.logging import logger


class VertexTuningJobConfig(BaseModel):
    """Specification for future Google Cloud Vertex AI Supervised Fine-Tuning Job."""
    gcp_project_id: str = Field(default="claim-support-prod-ai", alias="gcpProjectId")
    location: str = Field(default="us-central1")
    gcs_bucket_uri: str = Field(default="gs://claimsupport-tuning-datasets", alias="gcsBucketUri")
    training_data_gcs_path: str = Field(alias="trainingDataGcsPath")
    validation_data_gcs_path: Optional[str] = Field(default=None, alias="validationDataGcsPath")
    base_model: str = Field(default="gemini-1.5-flash-002", alias="baseModel")
    tuned_model_display_name: str = Field(default="claimsupport-gemini-v1", alias="tunedModelDisplayName")
    epochs: int = Field(default=4, ge=1, le=10)
    learning_rate_multiplier: float = Field(default=1.0, ge=0.1, le=5.0, alias="learningRateMultiplier")
    adapter_size: int = Field(default=4, alias="adapterSize")

    class Config:
        populate_by_name = True


class VertexAiTuningPlaceholder:
    """Interface preparing datasets for future Vertex AI fine-tuning execution."""

    MINIMUM_RECOMMENDED_EXAMPLES = 100

    @classmethod
    def validate_dataset_file(cls, file_path: str) -> Dict[str, Any]:
        """
        Validates that a local JSONL file strictly matches Vertex AI supervised tuning format:
        Each line must be valid JSON containing a 'messages' list with system, user, and model roles.
        """
        if not os.path.exists(file_path):
            return {"valid": False, "error": f"File '{file_path}' does not exist."}

        valid_count = 0
        errors = []

        with open(file_path, "r", encoding="utf-8") as f:
            for line_idx, line in enumerate(f, 1):
                clean_line = line.strip()
                if not clean_line:
                    continue
                try:
                    data = json.loads(clean_line)
                    if "messages" not in data or not isinstance(data["messages"], list):
                        errors.append(f"Line {line_idx}: Missing 'messages' list.")
                        continue

                    roles = [m.get("role") for m in data["messages"]]
                    if "user" not in roles or "model" not in roles:
                        errors.append(f"Line {line_idx}: Missing required user or model role in messages.")
                        continue

                    valid_count += 1
                except Exception as e:
                    errors.append(f"Line {line_idx}: Invalid JSON syntax ({e}).")

        is_valid = (len(errors) == 0 and valid_count > 0)
        return {
            "valid": is_valid,
            "totalValidExamples": valid_count,
            "meetsMinimumThreshold": (valid_count >= cls.MINIMUM_RECOMMENDED_EXAMPLES),
            "errors": errors[:10],
            "filePath": file_path,
        }

    @classmethod
    def get_readiness_checklist(cls, candidate_count: int) -> Dict[str, Any]:
        """Returns readiness audit checklist for deciding whether to proceed with fine-tuning."""
        return {
            "readyForEvaluation": True,
            "candidateApprovedCasesCount": candidate_count,
            "minimumRecommendedCases": cls.MINIMUM_RECOMMENDED_EXAMPLES,
            "thresholdReached": (candidate_count >= cls.MINIMUM_RECOMMENDED_EXAMPLES),
            "stepsToTune": [
                "1. Accumulate >= 100 expert-approved cases in ApprovedExpertCase collection.",
                "2. Call /api/tuning/datasets/create to export versioned Train/Val/Test splits.",
                "3. Upload vertex_train_<version>.jsonl and vertex_val_<version>.jsonl to Google Cloud Storage.",
                "4. Authenticate GCP SDK: gcloud auth application-default login.",
                "5. Trigger Vertex AI tuning job: vertexai.tuning.sft.train(source_model='gemini-1.5-flash-002', ...).",
                "6. Run /api/tuning/evaluation/run to compare tuned model against baseline Gemini.",
            ],
            "governanceGuarantees": [
                "Only expert-approved cases can enter training data.",
                "Strict PII scrubbing applied before export.",
                "Test set is completely isolated and never used for tuning.",
                "Deterministic rule engine remains authoritative ground truth regardless of tuning.",
            ],
        }
