"""
Custom exception classes and FastAPI exception handlers.
Replaces AppError.js with structured error handling.
"""

from fastapi import Request
from fastapi.responses import JSONResponse
from app.core.logging import logger


class AppError(Exception):
    """Base application error with HTTP status code."""

    def __init__(self, message: str, status_code: int = 500):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class PDFExtractionError(AppError):
    """Raised when PDF text extraction fails."""

    def __init__(self, message: str = "PDF extraction failed"):
        super().__init__(message, status_code=500)


class OCRError(AppError):
    """Raised when OCR processing fails."""

    def __init__(self, message: str = "OCR processing failed"):
        super().__init__(message, status_code=500)


class AIError(AppError):
    """Raised when AI/LLM processing fails."""

    def __init__(self, message: str = "AI processing failed"):
        super().__init__(message, status_code=500)


class ValidationError(AppError):
    """Raised when input validation fails."""

    def __init__(self, message: str = "Validation failed"):
        super().__init__(message, status_code=400)


class AuthenticationError(AppError):
    """Raised when authentication fails."""

    def __init__(self, message: str = "Authentication failed"):
        super().__init__(message, status_code=401)


class NotFoundError(AppError):
    """Raised when a resource is not found."""

    def __init__(self, message: str = "Resource not found"):
        super().__init__(message, status_code=404)


class ConflictError(AppError):
    """Raised when a conflict occurs (e.g., duplicate or in-progress)."""

    def __init__(self, message: str = "Conflict"):
        super().__init__(message, status_code=409)


# ─── FastAPI Exception Handlers ──────────────────────────────────────────────


from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """Handle AppError exceptions with structured JSON responses."""
    logger.error(f"[AppError] {exc.status_code}: {exc.message}")
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "message": exc.message},
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Handle standard HTTPExceptions (like 404 Not Found, 401 Unauthorized)."""
    logger.warning(f"[HTTPException] {exc.status_code}: {exc.detail}")
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "message": str(exc.detail)},
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Handle Pydantic request body/query validation errors cleanly."""
    errors = exc.errors()
    first_error_msg = errors[0].get("msg", "Invalid request parameters") if errors else "Validation Error"
    field_loc = " -> ".join([str(loc) for loc in errors[0].get("loc", [])]) if errors else ""
    error_summary = f"{field_loc}: {first_error_msg}" if field_loc else first_error_msg
    logger.warning(f"[ValidationError] {error_summary}")
    return JSONResponse(
        status_code=422,
        content={"success": False, "message": error_summary, "errors": errors},
    )


async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all handler — prevents server crashes and avoids exposing stack traces."""
    logger.error(f"[Unhandled Server Exception] {type(exc).__name__}: {exc}")
    from app.core.config import get_settings

    settings = get_settings()
    message = str(exc) if not settings.is_production else "Internal Server Error"
    return JSONResponse(
        status_code=500,
        content={"success": False, "message": message},
    )
