from datetime import datetime, timezone
from typing import Any, Optional, List
from beanie import Document, Indexed
from pydantic import Field

class AnalysisAuditLog(Document):
    """
    Audit log for each round of interaction with the LLM during coverage analysis.
    """
    session_id: Indexed(str) = Field(alias="sessionId") # type: ignore[valid-type]
    analysis_round: int = Field(alias="analysisRound")
    prompt_version: str = Field(default="2.0.0", alias="promptVersion")
    
    prescription_id: Optional[str] = Field(default=None, alias="prescriptionId")
    policy_id: Optional[str] = Field(default=None, alias="policyId")
    
    retrieved_context: Optional[str] = Field(default=None, alias="retrievedContext")
    clarification_context: Optional[dict] = Field(default=None, alias="clarificationContext")
    
    llm_request: Optional[str] = Field(default=None, alias="llmRequest")
    llm_response: Optional[dict] = Field(default=None, alias="llmResponse")
    
    generated_questions: Optional[List[dict]] = Field(default=None, alias="generatedQuestions")
    user_answers: Optional[List[dict]] = Field(default=None, alias="userAnswers")
    
    confidence_score: Optional[int] = Field(default=None, alias="confidenceScore")
    final_decision: Optional[str] = Field(default=None, alias="finalDecision")
    
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "analysisauditlogs"

    class Config:
        populate_by_name = True
