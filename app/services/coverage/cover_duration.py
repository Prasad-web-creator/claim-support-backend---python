"""
Cover duration: how long cover has run on the treatment date.

Which waiting period applies to a treatment (initial, pre-existing, specified
illness, and any exception such as an accident) is decided by the AI coverage
analysis from the policy's own wording. This module only does the date
arithmetic, so the AI is given exact numbers instead of calculating them.
"""

from datetime import date, datetime
from typing import Optional

from app.services.rule_engine.date_utils import parse_flexible_date


def _span(earlier: date, later: date) -> tuple[int, int, int]:
    """Whole years, months and days from `earlier` to `later` (calendar based)."""
    months = (later.year - earlier.year) * 12 + (later.month - earlier.month)
    if later.day < earlier.day:
        months -= 1
    year, month = divmod(earlier.month - 1 + months, 12)
    anchor_year, anchor_month = earlier.year + year, month + 1
    # The same day-of-month after `months` months, clamped to the month's length.
    import calendar
    anchor_day = min(earlier.day, calendar.monthrange(anchor_year, anchor_month)[1])
    days = (later - date(anchor_year, anchor_month, anchor_day)).days
    return months // 12, months % 12, days


def _date(value) -> Optional[date]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    parsed = parse_flexible_date(value)
    if isinstance(parsed, datetime):
        return parsed.date()
    return parsed


def treatment_date(prescription: dict) -> tuple[Optional[date], str]:
    """The date the claim is evaluated on, and where it came from."""
    rx = prescription or {}
    for key, label in (
        ("admissionDate", "admission date"),
        ("treatmentDate", "treatment date"),
        ("visitDate", "visit date"),
        ("consultationDate", "consultation date"),
    ):
        d = _date(rx.get(key))
        if d:
            return d, label
    if rx.get("isManual") or rx.get("manualText") or rx.get("prescriptionSource") == "Self-entered Prescription":
        return date.today(), "today (self-entered check)"
    return None, ""


def cover_duration(policy: dict, prescription: dict) -> Optional[dict]:
    """
    Cover duration from the start of continuous cover (the waiting-period start
    date) to the treatment date, or None when either date is unknown.
    """
    pj = policy or {}
    start = _date(pj.get("waitingPeriodStartDate")) or _date(pj.get("policyStartDate"))
    on, source = treatment_date(prescription)
    if not start or not on:
        return None

    days = (on - start).days
    years, months, rest_days = _span(start, on) if days >= 0 else _span(on, start)
    months_total = years * 12 + months
    parts = []
    if years:
        parts.append(f"{years} year{'s' if years != 1 else ''}")
    if months:
        parts.append(f"{months} month{'s' if months != 1 else ''}")
    if rest_days or not parts:
        parts.append(f"{rest_days} day{'s' if rest_days != 1 else ''}")
    text = " ".join(parts)

    return {
        "coverStartDate": start.isoformat(),
        "coverStartBasis": pj.get("waitingPeriodBasis") or "start date of the current policy period",
        "treatmentDate": on.isoformat(),
        "treatmentDateSource": source,
        "daysActive": days,
        "completedMonths": months_total if days >= 0 else -months_total,
        "summary": (
            f"Cover has run for {text} ({days} days) from {start:%d-%b-%Y} to the {source} {on:%d-%b-%Y}."
            if days >= 0 else
            f"The {source} {on:%d-%b-%Y} is {text} BEFORE cover started on {start:%d-%b-%Y}."
        ),
    }
