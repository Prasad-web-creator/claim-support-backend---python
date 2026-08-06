from datetime import datetime
from typing import Any, Optional, List
from beanie import Document, Indexed
from pydantic import Field, BaseModel

class ClarificationOption(BaseModel):
    label: str
    value: str

class ClarificationQuestion(BaseModel):
    id: str = Field(alias="id") # The stable question ID (e.g., hospitalization)
    title: str
    question: str
    type: str # single_choice, multi_choice, text, number, date, boolean
    category: Optional[str] = None
    required: bool = True
    options: Optional[List[str]] = None
    reason: str

class ClarificationAnswer(BaseModel):
    question_id: str = Field(alias="questionId")
    answer: Any
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class AnalysisSession(Document):
    """
    Interactive Analysis Session to handle clarification loops before final report generation.
    """
    user_id: Indexed(str) = Field(alias="userId") # type: ignore[valid-type]
    policy_id: Optional[str] = Field(default=None, alias="policyId")
    prescription_id: Optional[str] = Field(default=None, alias="prescriptionId")

    status: str = Field(default="created") # created | extracting_documents | analyzing | waiting_for_user | reanalyzing | completed | failed | manual_review_required | cancelled | expired
    
    round_count: int = Field(default=0, alias="roundCount")
    expires_at: Optional[datetime] = Field(default=None, alias="expiresAt")
    
    questions: List[ClarificationQuestion] = Field(default_factory=list)
    answers: List[ClarificationAnswer] = Field(default_factory=list)
    
    # Store extraction results to avoid re-extracting
    policy_text: Optional[str] = Field(default=None, alias="policyText")
    prescription_text: Optional[str] = Field(default=None, alias="prescriptionText")
    business_rules: Optional[dict] = Field(default=None, alias="businessRules")
    policy_json: Optional[dict] = Field(default=None, alias="policyJson")
    prescription_json: Optional[dict] = Field(default=None, alias="prescriptionJson")

    accumulated_processing_time_ms: int = Field(default=0, alias="accumulatedProcessingTimeMs")

    report_id: Optional[str] = Field(default=None, alias="reportId") # Once completed
    
    created_at: datetime = Field(default_factory=datetime.utcnow, alias="createdAt")
    updated_at: datetime = Field(default_factory=datetime.utcnow, alias="updatedAt")

    class Settings:
        name = "analysissessions"
        use_state_management = True

    class Config:
        populate_by_name = True
