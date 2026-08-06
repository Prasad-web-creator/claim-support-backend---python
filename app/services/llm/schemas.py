from typing import List, Optional, Any
from pydantic import BaseModel, Field

class MedicineSchema(BaseModel):
    name: Optional[str] = None
    dosage: Optional[str] = None
    frequency: Optional[str] = None
    duration: Optional[str] = None
    cost: Optional[float] = None
    instructions: Optional[str] = None

class TestSchema(BaseModel):
    name: Optional[str] = None
    cost: Optional[float] = None

class ProcedureSchema(BaseModel):
    name: Optional[str] = None
    cost: Optional[float] = None

class PrescriptionSchema(BaseModel):
    patientName: Optional[str] = None
    age: Optional[float] = None
    gender: Optional[str] = None
    doctor: Optional[str] = None
    hospital: Optional[str] = None
    visitDate: Optional[str] = None
    diagnosis: Optional[str] = None
    symptoms: Optional[List[str]] = Field(default_factory=list)
    medicines: Optional[List[MedicineSchema]] = Field(default_factory=list)
    dosage: Optional[str] = None
    frequency: Optional[str] = None
    medicalTests: Optional[List[TestSchema]] = Field(default_factory=list)
    admission: Optional[str] = None
    discharge: Optional[str] = None
    hospitalizationRequired: Optional[bool] = None
    procedures: Optional[List[ProcedureSchema]] = Field(default_factory=list)
    estimatedCost: Optional[float] = None
    followUp: Optional[str] = None
    doctorNotes: Optional[str] = None
    medicalNotes: Optional[str] = None
