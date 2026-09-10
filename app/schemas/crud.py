"""
Schemas for CRUD operations on models.
"""

from typing import Optional, Union
from pydantic import BaseModel, field_validator
from datetime import datetime

class AgreementSchema(BaseModel):
    termsAccepted: bool
    dataProcessingConsent: Optional[bool] = None
    signature: Optional[str] = ""
    termsVersion: Optional[str] = "1.0"
    appVersion: Optional[str] = "1.0.0"
    platform: Optional[str] = ""

class PolicyCreate(BaseModel):
    policyNumber: Optional[str] = ""
    policyName: Optional[str] = ""
    policyHolderName: Optional[str] = ""
    insuranceCompany: Optional[str] = ""
    policyType: Optional[str] = ""
    policyStartDate: Optional[Union[datetime, str]] = None
    policyEndDate: Optional[Union[datetime, str]] = None
    coverageAmount: Optional[float] = 0.0
    status: Optional[str] = "Active"
    gridFsFileId: Optional[str] = ""
    originalFileName: Optional[str] = ""
    mimeType: Optional[str] = ""
    fileSize: Optional[int] = 0
    extractedPolicyText: Optional[str] = ""
    agreement: Optional[AgreementSchema] = None

    @field_validator(
        "policyNumber",
        "policyName",
        "policyHolderName",
        "insuranceCompany",
        "policyType",
        "status",
        "gridFsFileId",
        "originalFileName",
        "mimeType",
        "extractedPolicyText",
        mode="before",
    )
    @classmethod
    def sanitize_string_fields(cls, v):
        if v is None:
            return ""
        return str(v)

    @field_validator("fileSize", mode="before")
    @classmethod
    def sanitize_file_size(cls, v):
        if v is None:
            return 0
        return v

    @field_validator("coverageAmount", mode="before")
    @classmethod
    def sanitize_coverage_amount(cls, v):
        if v is None:
            return 0.0
        return float(v)

class PolicyUpdate(PolicyCreate):
    pass

from typing import Optional, Union
from pydantic import BaseModel, field_validator

class PrescriptionCreate(BaseModel):
    hospitalName: Optional[str] = ""
    doctorName: Optional[str] = ""
    patientName: Optional[str] = ""
    visitDate: Optional[Union[datetime, str]] = ""
    diagnosis: Optional[str] = ""
    gridFsFileId: Optional[str] = ""
    originalFileName: Optional[str] = ""
    mimeType: Optional[str] = ""
    fileSize: Optional[int] = 0
    isManual: Optional[bool] = False
    extractedPrescriptionText: Optional[str] = ""
    agreement: Optional[AgreementSchema] = None

    @field_validator(
        "hospitalName",
        "doctorName",
        "patientName",
        "diagnosis",
        "gridFsFileId",
        "originalFileName",
        "mimeType",
        "extractedPrescriptionText",
        mode="before",
    )
    @classmethod
    def sanitize_none_strings(cls, v):
        if v is None:
            return ""
        return str(v)

    @field_validator("visitDate", mode="before")
    @classmethod
    def sanitize_visit_date(cls, v):
        if v is None:
            return ""
        return v

    @field_validator("fileSize", mode="before")
    @classmethod
    def sanitize_file_size(cls, v):
        if v is None:
            return 0
        return v

class PrescriptionUpdate(PrescriptionCreate):
    pass

