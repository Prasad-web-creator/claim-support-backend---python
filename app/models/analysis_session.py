from datetime import datetime, timezone
from typing import Any, Optional, List, Union
from beanie import Document, Indexed, before_event, Insert, Replace, SaveChanges, Update
from pydantic import Field, BaseModel, field_validator


def _sanitize_nulls(data: Any) -> Any:
    """Recursively converts None values in dictionaries and lists to empty strings or empty structures."""
    if isinstance(data, dict):
        return {k: _sanitize_nulls(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [_sanitize_nulls(item) for item in data]
    elif data is None:
        return ""
    return data


class ClarificationOption(BaseModel):
    label: str = ""
    value: str = ""


class ClarificationQuestion(BaseModel):
    id: str = Field(alias="id") # The stable question ID (e.g., hospitalization)
    title: str = ""
    question: str = ""
    type: str = "single_choice" # single_choice, multi_choice, text, number, date, boolean
    category: Optional[str] = ""
    required: bool = True
    options: Optional[List[str]] = Field(default_factory=list)
    reason: str = ""


class ClarificationAnswer(BaseModel):
    question_id: str = Field(alias="questionId")
    answer: Any = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AnalysisSession(Document):
    """
    Interactive Analysis Session to handle clarification loops before final report generation.
    """
    user_id: Indexed(str) = Field(alias="userId") # type: ignore[valid-type]
    policy_id: Optional[str] = Field(default="", alias="policyId")
    prescription_id: Optional[str] = Field(default="", alias="prescriptionId")

    status: str = Field(default="created") # created | extracting_documents | analyzing | waiting_for_user | reanalyzing | completed | failed | manual_review_required | cancelled | expired
    
    round_count: int = Field(default=0, alias="roundCount")
    expires_at: Optional[Union[datetime, str]] = Field(default=None, alias="expiresAt")
    
    questions: List[ClarificationQuestion] = Field(default_factory=list)
    answers: List[ClarificationAnswer] = Field(default_factory=list)
    
    # Store extraction results to avoid re-extracting
    policy_text: Optional[str] = Field(default="", alias="policyText")
    prescription_text: Optional[str] = Field(default="", alias="prescriptionText")
    business_rules: Optional[dict] = Field(default_factory=dict, alias="businessRules")
    policy_json: Optional[dict] = Field(default_factory=dict, alias="policyJson")
    prescription_json: Optional[dict] = Field(default_factory=dict, alias="prescriptionJson")

    extraction_time_ms: int = Field(default=0, alias="extractionTimeMs")
    accumulated_processing_time_ms: int = Field(default=0, alias="accumulatedProcessingTimeMs")

    cost_steps: List[dict] = Field(default_factory=list, alias="costSteps")
    report_id: Optional[str] = Field(default="", alias="reportId") # Once completed
    
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), alias="createdAt")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), alias="updatedAt")

    @field_validator("policy_id", "prescription_id", "status", "policy_text", "prescription_text", "report_id", mode="before")
    @classmethod
    def sanitize_string_fields(cls, v):
        if v is None:
            return ""
        return str(v)

    @field_validator("business_rules", "policy_json", "prescription_json", mode="before")
    @classmethod
    def sanitize_dict_fields(cls, v):
        if v is None:
            return {}
        if isinstance(v, dict):
            return _sanitize_nulls(v)
        return v

    class Settings:
        name = "analysissessions"
        use_state_management = True

    class Config:
        populate_by_name = True

    @before_event([Insert, Replace, SaveChanges, Update])
    def sanitize_null_fields(self):
        """Ensure no string or dictionary fields are stored as null in MongoDB."""
        string_fields = [
            "policy_id", "prescription_id", "status",
            "policy_text", "prescription_text", "report_id"
        ]
        for field in string_fields:
            if getattr(self, field, None) is None:
                setattr(self, field, "")
        if self.business_rules is None:
            self.business_rules = {}
        else:
            self.business_rules = _sanitize_nulls(self.business_rules)
        if self.policy_json is None:
            self.policy_json = {}
        else:
            self.policy_json = _sanitize_nulls(self.policy_json)
        if self.prescription_json is None:
            self.prescription_json = {}
        else:
            self.prescription_json = _sanitize_nulls(self.prescription_json)
