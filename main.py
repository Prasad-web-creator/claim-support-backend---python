"""
FastAPI application entry point.
Replaces server.js and app.js.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler

from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import get_settings
from app.core.database import connect_to_mongodb, close_mongodb_connection
from app.core.exceptions import (
    AppError,
    app_error_handler,
    http_exception_handler,
    validation_exception_handler,
    generic_exception_handler,
)
from app.core.logging import logger
from app.middleware.request_id import RequestIdMiddleware
from app.middleware.rate_limiter import limiter
from app.api.router import api_router


from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import timezone as dt_timezone

from app.models.activity_log import ActivityLog
from app.models.analysis_audit_log import AnalysisAuditLog
from app.models.analysis_report import AnalysisReport
from app.models.analysis_session import AnalysisSession
from app.models.policy import Policy
from app.models.prescription import Prescription
from app.models.stored_file import StoredFile

from app.services.ttl_service import ensure_ttl_indexes
from app.services.cleanup_service import run_cleanup

scheduler = AsyncIOScheduler()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle events: startup and shutdown."""
    settings = get_settings()
    
    # Startup
    logger.info(f"Starting Claim Support API in {settings.ENVIRONMENT} mode")
    logger.info(f"Data retention enabled: {settings.DATA_CLEANUP_DAYS} days ({settings.ttl_seconds} seconds)")
    await connect_to_mongodb()
    
    # Initialize / Seed Knowledge Base if needed
    try:
        from app.services.knowledge_base.policy_knowledge_service import PolicyKnowledgeService
        await PolicyKnowledgeService.seed_knowledge_base()
    except Exception as e:
        logger.warning(f"Knowledge Base initialization notice: {e}")
    
    # Ensure TTL Indexes on all non-permanent models
    models = [
        Policy, Prescription, AnalysisReport, AnalysisSession, 
        AnalysisAuditLog, ActivityLog, StoredFile
    ]
    await ensure_ttl_indexes(models)
    
    # Start Background Cleanup Scheduler (UTC Midnight)
    scheduler.add_job(
        run_cleanup, 
        CronTrigger(hour=0, minute=0, timezone=dt_timezone.utc), 
        id="daily_cleanup", 
        replace_existing=True
    )
    scheduler.start()
    logger.info("Started background APScheduler for daily GridFS cleanup (UTC 00:00).")
    
    yield
    
    # Shutdown
    logger.info("Shutting down...")
    scheduler.shutdown()
    await close_mongodb_connection()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application instance."""
    settings = get_settings()
    
    app = FastAPI(
        title="Claim Support API",
        version="2.0.0",
        description="AI-powered medical insurance claim analysis",
        lifespan=lifespan,
        docs_url="/api/docs" if not settings.is_production else None,
        redoc_url="/api/redoc" if not settings.is_production else None,
    )
    
    # ─── Middleware ──────────────────────────────────────────────────────────
    
    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Request ID for tracing
    app.add_middleware(RequestIdMiddleware)
    
    # Rate Limiting setup
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    
    # ─── Exception Handlers ──────────────────────────────────────────────────
    
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, generic_exception_handler)
    
    # ─── API Routes ──────────────────────────────────────────────────────────
    
    app.include_router(api_router)
    
    @app.get("/health", tags=["Health"])
    async def health_check():
        """Basic health check endpoint."""
        from datetime import datetime, timezone
        return {
            "status": "healthy",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
    return app


# Create the global app instance
app = create_app()
