"""
API Router configuration.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.routes import analysis, app_config, auth, dashboard, policy_custom, upload
from app.api.routes.crud_factory import create_crud_router
from app.models.analysis_report import AnalysisReport
from app.models.policy import Policy
from app.models.prescription import Prescription
from app.schemas.crud import (
    PolicyCreate,
    PolicyUpdate,
    PrescriptionCreate,
    PrescriptionUpdate,
)

api_router = APIRouter(prefix="/api")

# Include static routers
api_router.include_router(app_config.router)
api_router.include_router(auth.router)
api_router.include_router(upload.router)
api_router.include_router(analysis.router)
api_router.include_router(dashboard.router)

# Include custom policy routes FIRST so they override generic CRUD
api_router.include_router(policy_custom.router)

# Include dynamic CRUD routers
policy_router = create_crud_router(
    entity_name="Policy",
    model_class=Policy,
    schema_create=PolicyCreate,
    schema_update=PolicyUpdate,
    searchable_fields=["policyNumber", "policyName", "insuranceCompany"],
    prefix_override="/policies"
)
api_router.include_router(policy_router)

prescription_router = create_crud_router(
    entity_name="Prescription",
    model_class=Prescription,
    schema_create=PrescriptionCreate,
    schema_update=PrescriptionUpdate,
    searchable_fields=["hospitalName", "doctorName", "diagnosis"]
)
api_router.include_router(prescription_router)

# Basic schemas for read-only / minimal models
class EmptyCreate(BaseModel): pass
class EmptyUpdate(BaseModel): pass

report_router = create_crud_router(
    entity_name="AnalysisReport",
    model_class=AnalysisReport,
    schema_create=EmptyCreate,
    schema_update=EmptyUpdate,
    searchable_fields=["status", "overallStatus"]
)
api_router.include_router(report_router)
