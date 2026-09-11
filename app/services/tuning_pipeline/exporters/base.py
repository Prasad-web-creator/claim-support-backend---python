"""
Abstract Base Class for Dataset Exporters.
Decouples export formats (JSONL, Vertex AI, Evaluation benchmarks) from core business logic.
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from app.models.dataset_tuning import TrainingExample


class BaseDatasetExporter(ABC):
    """Abstract interface for serializing training/evaluation examples into disk files."""

    @abstractmethod
    def export(
        self,
        examples: List[TrainingExample],
        output_path: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Exports list of training examples to the designated path on disk.
        Returns the absolute output filepath.
        """
        pass
