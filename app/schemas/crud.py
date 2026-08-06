"""
Schemas for CRUD operations on models.
"""

from typing import Optional
from pydantic import BaseModel
from datetime import datetime

class AgreementSchema(BaseModel):
    termsAccepted: bool
    dataProcessingConsent: Optional[bool] = None
    signature: Optional[str] = None
    termsVersion: Optional[str] = None
    appVersion: Optional[str] = None
    platform: Optional[str] = None

class PolicyCreate(BaseModel):
    policyNumber: Optional[str] = None
    policyName: Optional[str] = None
    insuranceCompany: Optional[str] = None
    policyType: Optional[str] = None
    policyStartDate: Optional[datetime] = None
    policyEndDate: Optional[datetime] = None
    coverageAmount: Optional[float] = None
    gridFsFileId: Optional[str] = None
    originalFileName: Optional[str] = None
    mimeType: Optional[str] = None
    fileSize: Optional[int] = None
    agreement: Optional[AgreementSchema] = None

class PolicyUpdate(PolicyCreate):
    pass

class PrescriptionCreate(BaseModel):
    hospitalName: Optional[str] = None
    doctorName: Optional[str] = None
    patientName: Optional[str] = None
    visitDate: Optional[datetime] = None
    diagnosis: Optional[str] = None
    gridFsFileId: Optional[str] = None
    originalFileName: Optional[str] = None
    mimeType: Optional[str] = None
    fileSize: Optional[int] = None
    isManual: Optional[bool] = False
    manualText: Optional[str] = None
    prescriptionSource: Optional[str] = None
    agreement: Optional[AgreementSchema] = None

class PrescriptionUpdate(PrescriptionCreate):
    pass

