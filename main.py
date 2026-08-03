"""
FastAPI application entry point.
Replaces server.js and app.js.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler

from app.core.config import get_settings
from app.core.database import connect_to_mongodb, close_mongodb_connection
from app.core.exceptions import AppError, app_error_handler, generic_exception_handler
from app.core.logging import logger
from app.middleware.request_id import RequestIdMiddleware
from app.middleware.rate_limiter import limiter
from app.api.router import api_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle events: startup and shutdown."""
    settings = get_settings()
    
    # Startup
    logger.info(f"Starting Claim Support API in {settings.ENVIRONMENT} mode")
    await connect_to_mongodb()
    
    yield
    
    # Shutdown
    logger.info("Shutting down...")
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
