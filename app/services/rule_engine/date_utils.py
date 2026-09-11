"""
Deterministic Calendar Date Arithmetic Utility for Insurance Rules.
Handles exact calendar months, leap years, month-end boundaries,
policy anniversaries, and missing date scenarios without third-party dependencies.
"""

import calendar
import re
from datetime import date, datetime, timezone
from typing import Optional, Tuple, Any

from app.services.rule_engine.models import ReasonCode


def parse_flexible_date(val: Any) -> Optional[date]:
    """
    Deterministically parses diverse date representations into a standard datetime.date.
    Accepts date, datetime, ISO strings, Indian formats (DD/MM/YYYY, DD-MM-YYYY),
    named months (01-Jan-2025), and epoch timestamps.
    """
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val

    # Clean string representation
    s = str(val).strip()
    if not s or s.lower() in ("unknown", "null", "none", "n/a", "undefined", ""):
        return None

    # Handle ISO timestamps with time/tz parts
    clean_s = s.split("T")[0].split(" ")[0].strip()

    formats = (
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d.%m.%Y",
        "%m/%d/%Y",
        "%Y/%m/%d",
        "%d-%b-%Y",
        "%d-%B-%Y",
        "%b %d, %Y",
        "%B %d, %Y",
    )

    for fmt in formats:
        try:
            return datetime.strptime(clean_s, fmt).date()
        except Exception:
            continue

    # Try epoch timestamp
    try:
        ts = float(s)
        if ts > 1e11:  # milliseconds
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc).date()
    except Exception:
        pass

    return None


def add_calendar_months(d: date, months: int) -> date:
    """
    Adds exact calendar months to a date, respecting month boundaries and leap years.
    Examples:
        2024-02-29 + 12 months = 2025-02-28 (non-leap anniversary)
        2025-01-31 + 1 month   = 2025-02-28 (month-end clamping)
        2025-01-15 + 17 months = 2026-06-15
    """
    total_months = d.month - 1 + months
    target_year = d.year + (total_months // 12)
    target_month = (total_months % 12) + 1
    max_days = calendar.monthrange(target_year, target_month)[1]
    target_day = min(d.day, max_days)
    return date(target_year, target_month, target_day)


def completed_calendar_months(start_date: date, eval_date: date) -> int:
    """
    Computes exact completed full calendar months between start_date and eval_date
    using policy anniversary day rules.

    Examples:
        2025-01-01 to 2026-06-01: Exactly 17 completed months
        2024-02-29 to 2025-02-28: Exactly 12 completed months (leap year anniversary)
        2025-01-31 to 2025-02-28: Exactly 1 completed month (month boundary)
        2025-01-15 to 2025-02-14: 0 completed months (anniversary not reached)
    """
    if eval_date < start_date:
        return 0

    m_diff = (eval_date.year - start_date.year) * 12 + (eval_date.month - start_date.month)
    anniversary = add_calendar_months(start_date, m_diff)
    if eval_date < anniversary:
        m_diff -= 1

    return max(0, m_diff)


def days_between(start_date: date, eval_date: date) -> int:
    """Returns number of elapsed days between two dates."""
    return (eval_date - start_date).days


def check_policy_period(
    start_date: Optional[date],
    end_date: Optional[date],
    eval_date: date,
) -> Tuple[bool, str, ReasonCode]:
    """
    Validates whether eval_date falls strictly within [start_date, end_date].
    Returns (is_valid, description, reason_code).
    """
    if start_date is None:
        return (
            False,
            "Policy start date is missing; cannot verify temporal validity.",
            ReasonCode.MISSING_POLICY_DATES,
        )

    if eval_date < start_date:
        return (
            False,
            f"Treatment date ({eval_date.isoformat()}) occurred before policy inception ({start_date.isoformat()}).",
            ReasonCode.POLICY_NOT_YET_EFFECTIVE,
        )

    if end_date is not None and eval_date > end_date:
        return (
            False,
            f"Policy expired: Treatment date ({eval_date.isoformat()}) occurred after policy expiration date ({end_date.isoformat()}).",
            ReasonCode.POLICY_EXPIRED,
        )

    return (
        True,
        f"Policy was active on treatment date ({eval_date.isoformat()}).",
        ReasonCode.POLICY_ACTIVE,
    )
