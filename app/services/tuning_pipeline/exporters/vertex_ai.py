"""
Vertex AI Gemini Supervised Tuning Dataset Exporter.
Serializes examples in Google Cloud Vertex AI Gemini Supervised Fine-Tuning JSONL schema:
{
  "messages": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "..."},
    {"role": "model", "content": "..."}
  ]
}
"""

import json
import os
from typing import List, Dict, Any, Optional
from app.models.dataset_tuning import TrainingExample
from app.services.tuning_pipeline.exporters.base import BaseDatasetExporter

SYSTEM_PROMPT = """You are an authoritative health insurance claim adjudication engine.
Adjudicate the claim according to contractual policy clauses, deterministic rule checks, and clinical evidence.
Return your decision strictly as valid JSON matching the schema:
{
  "overallStatus": "Covered" | "Not Covered" | "Partially Covered" | "Manual Review",
  "reasonCode": "<STANDARDIZED_REASON_CODE>",
  "explanation": "<clinical rationale and policy clause citation>",
  "isEligible": true | false
}"""


class VertexAiGeminiTuningExporter(BaseDatasetExporter):
    """Exports dataset directly into Vertex AI Supervised Tuning format."""

    def export(
        self,
        examples: List[TrainingExample],
        output_path: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as f:
            for ex in examples:
                # Build user content prompt from input
                inp = ex.input
                evidence_text = "\n".join([
                    f"- Source: {ev.documentName} (Page {ev.pageNumber}): \"{ev.textSnippet}\""
                    for ev in inp.retrievedEvidence
                ]) or "No clauses cited."

                user_prompt = f"""CLAIM DETAILS:
- Diagnosis: {inp.diagnosis}
- Normalized Diagnosis: {inp.normalizedDiagnosis}
- Treatment / Procedure: {inp.treatment}
- Insurer: {inp.insurer} | Product: {inp.product} | Plan Variant: {inp.variant} (v{inp.policyVersion})
- Policy Active Duration: {f'{inp.policyDurationMonths} completed months' if inp.policyDurationMonths is not None else 'Active'}

RETRIEVED CONTRACTUAL EVIDENCE:
{evidence_text}

APPLICABLE BUSINESS RULES:
{', '.join(inp.applicableRules) or 'Standard Policy Validity and Inclusions'}

Adjudicate coverage, provide ground-truth reason code, and cite relevant clauses."""

                # Build model response content
                exp = ex.expectedOutput
                model_response = json.dumps({
                    "overallStatus": "Covered" if exp.status == "COVERED" else ("Not Covered" if exp.status == "NOT_COVERED" else ("Partially Covered" if exp.status == "PARTIALLY_COVERED" else "Manual Review")),
                    "reasonCode": exp.reasonCode,
                    "explanation": exp.explanation,
                    "isEligible": (exp.status == "COVERED"),
                }, ensure_ascii=False)

                vertex_line = {
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                        {"role": "model", "content": model_response},
                    ]
                }
                f.write(json.dumps(vertex_line, ensure_ascii=False) + "\n")

        return os.path.abspath(output_path)
