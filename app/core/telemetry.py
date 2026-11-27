"""Send allowlisted operational diagnostics to Sentry."""

import re
from pathlib import Path
from traceback import walk_tb

from app.core.config import Settings


_OPERATIONS = frozenset({"backend_exception", "analysis", "ocr", "authentication"})
_BASE = Path(__file__).resolve().parents[2]
_client_enabled = False


def _safe_event(event, _hint):
    operation = event.get("message")
    if operation not in _OPERATIONS:
        return None
    safe = {key: event[key] for key in ("event_id", "timestamp", "environment", "level", "platform") if key in event}
    safe["message"] = operation
    frames = event.get("extra", {}).get("application_frames", [])
    if frames:
        safe["fingerprint"] = [operation, frames[-1]["file"], frames[-1]["function"]]
        safe["extra"] = {"application_frames": frames}
    return safe


def _safe_transaction(event, _hint):
    if event.get("transaction") not in _OPERATIONS:
        return None
    context = event.get("contexts", {}).get("trace", {})
    safe = {key: event[key] for key in (
        "event_id", "transaction", "type", "start_timestamp", "timestamp", "environment", "platform",
    ) if key in event}
    safe["contexts"] = {"trace": {
        key: context[key] for key in ("trace_id", "span_id", "op", "status") if key in context
    }}
    safe["spans"] = []
    return safe


def configure_telemetry(settings: Settings) -> None:
    global _client_enabled
    _client_enabled = False
    if not settings.SENTRY_ENABLED:
        return
    import sentry_sdk

    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.ENVIRONMENT,
        default_integrations=False,
        auto_enabling_integrations=False,
        send_default_pii=False,
        sample_rate=settings.SENTRY_ERROR_SAMPLE_RATE,
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE or None,
        before_send=_safe_event,
        before_send_transaction=_safe_transaction,
    )
    _client_enabled = True


def record_failure(operation: str, exc: Exception | None = None) -> None:
    if not _client_enabled or operation not in _OPERATIONS:
        return
    import sentry_sdk

    frames = []
    if exc is not None:
        for frame, lineno in walk_tb(exc.__traceback__):
            source = Path(frame.f_code.co_filename)
            if not source.is_relative_to(_BASE):
                continue
            path = source.relative_to(_BASE).as_posix()
            function = frame.f_code.co_name
            if re.fullmatch(r"(?:app/[\w/]+|main)\.py", path) and re.fullmatch(r"[\w<>]+", function):
                frames.append({"file": path, "function": function, "line": lineno})
    sentry_sdk.capture_event({"message": operation, "level": "error", "extra": {"application_frames": frames[-12:]}})
