"""
Standard JSONL Exporter.
Serializes training and validation examples in canonical input-expectedOutput schema.
"""

import json
import os
from typing import List, Dict, Any, Optional
from app.models.dataset_tuning import TrainingExample
from app.services.tuning_pipeline.exporters.base import BaseDatasetExporter


class StandardJsonlExporter(BaseDatasetExporter):
    """Exports examples in standard {input: ..., expectedOutput: ...} JSONL lines."""

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
                    "input": ex.input.model_dump(by_alias=True),
                    "expectedOutput": ex.expectedOutput.model_dump(by_alias=True),
                    "category": ex.category.value,
                    "exampleId": ex.exampleId,
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

        return os.path.abspath(output_path)
