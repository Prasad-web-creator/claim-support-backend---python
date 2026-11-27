"""
FastAPI application entry point.
Replaces server.js and app.js.
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler

from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import get_settings
from app.core.database import connect_to_mongodb, close_mongodb_connection, get_database
from app.core.exceptions import (
    AppError,
    app_error_handler,
    http_exception_handler,
    validation_exception_handler,
    generic_exception_handler,
)
from app.core.logging import logger
from app.core.telemetry import configure_telemetry, record_failure
from app.middleware.request_id import RequestIdMiddleware
from app.middleware.rate_limiter import limiter
from app.api.router import api_router


from apscheduler.schedulers.asyncio import AsyncIOScheduler

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle events: startup and shutdown."""
    settings = get_settings()
    app.state.ready = False
    if not settings.GEMINI_API_KEY.strip():
        raise RuntimeError("GEMINI_API_KEY is required to start the API.")

    # Startup
    logger.info(f"Starting Claim Support API in {settings.ENVIRONMENT} mode")
    configure_telemetry(settings)
    await connect_to_mongodb()
    scheduler = AsyncIOScheduler()
    worker = None
    try:
        from app.services.analysis_jobs import (
            initialize_analysis_jobs, start_analysis_job_worker, stop_analysis_job_worker,
        )
        from app.services.maintenance import run_due_maintenance

        await initialize_analysis_jobs()
        worker = start_analysis_job_worker()
        app.state.analysis_worker = worker
        if settings.ORPHAN_CLEANUP_ENABLED or settings.STORAGE_MONITOR_ENABLED or settings.DOCUMENT_RETENTION_DAYS:
            scheduler.add_job(
                run_due_maintenance,
                "interval",
                minutes=settings.MAINTENANCE_CHECK_INTERVAL_MINUTES,
                next_run_time=datetime.now(timezone.utc) + timedelta(seconds=10),
            )
            scheduler.start()
        app.state.ready = True
        yield
    finally:
        app.state.ready = False
        if scheduler.running:
            scheduler.shutdown()
        if worker is not None:
            await stop_analysis_job_worker(worker)
        await close_mongodb_connection()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application instance."""
    settings = get_settings()
    
    app = FastAPI(
        title="Claim Support API",
        version="2.0.0",
        description="Medical insurance claim analysis",
        lifespan=lifespan,
        docs_url="/api/docs" if not settings.is_production else None,
        redoc_url="/api/redoc" if not settings.is_production else None,
    )

    @app.middleware("http")
    async def report_operation_failure(request, call_next):
        response = await call_next(request)
        route = getattr(request.scope.get("route"), "path", "")
        if route.startswith("/api/auth/") and response.status_code >= 400:
            record_failure("authentication")
        elif route.startswith("/api/analysis/") and response.status_code >= 500:
            record_failure("analysis")
        return response
    
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
    from app.api.routes.policy_custom import router as policy_custom_router
    app.include_router(policy_custom_router)
    
    @app.get("/health", tags=["Health"])
    async def health_check():
        """Basic health check endpoint."""
        from datetime import datetime, timezone
        return {
            "status": "healthy",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    @app.get("/ready", tags=["Health"])
    async def readiness_check():
        database_ready = False
        worker = getattr(app.state, "analysis_worker", None)
        if getattr(app.state, "ready", False) and worker is not None and not worker.done():
            try:
                await asyncio.wait_for(get_database().command("ping"), timeout=3)
                database_ready = True
            except Exception:
                pass
        model_configured = bool(settings.GEMINI_API_KEY.strip())
        ready = database_ready and model_configured
        return JSONResponse(
            status_code=200 if ready else 503,
            content={"ready": ready, "database": database_ready, "model": model_configured},
        )

    return app


# Create the global app instance
app = create_app()
