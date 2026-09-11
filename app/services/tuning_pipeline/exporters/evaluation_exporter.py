"""
Evaluation Dataset Exporter.
Exports structured evaluation test sets with category metadata and ground-truth validation criteria.
"""

import json
import os
from typing import List, Dict, Any, Optional
from app.models.dataset_tuning import TrainingExample
from app.services.tuning_pipeline.exporters.base import BaseDatasetExporter


class EvaluationDatasetExporter(BaseDatasetExporter):
    """Exports test and benchmark cases with evaluation parameters."""

    def export(
        self,
        examples: List[TrainingExample],
        output_path: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as f:
            for ex in examples:
                record = {
                    "exampleId": ex.exampleId,
                    "category": ex.category.value,
                    "sourceCaseId": ex.sourceCaseId,
                    "input": ex.input.model_dump(by_alias=True),
                    "expectedOutput": ex.expectedOutput.model_dump(by_alias=True),
                    "scoringRules": {
                        "checkStatus": True,
                        "checkReasonCode": True,
                        "checkEvidencePresence": True,
                        "trackFalseApproval": (ex.expectedOutput.status == "NOT_COVERED"),
                        "trackFalseRejection": (ex.expectedOutput.status == "COVERED"),
                    },
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

        return os.path.abspath(output_path)
