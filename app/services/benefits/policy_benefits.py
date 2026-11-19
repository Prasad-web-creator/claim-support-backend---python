"""
My Benefits — read-only view of a policy's stored benefit details.

Builds a plain-language summary from the policy JSON that was already extracted
and stored on the Policy record. Nothing here calls an LLM, OCR or the analysis
pipeline, and nothing is written back: values that are not in the stored data
are reported as missing instead of being guessed.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Optional

# ─────────────────────────────────────────────────────────────────────────────
# Small helpers
# ─────────────────────────────────────────────────────────────────────────────

_EMPTY_STRINGS = {"", "null", "none", "n/a", "na", "not specified", "not mentioned", "not available", "-"}


def _text(value: Any) -> Optional[str]:
    """A clean non-empty string, or None."""
    if value is None or isinstance(value, (dict, list)):
        return None
    s = re.sub(r"\s+", " ", str(value)).strip()
    return None if s.lower() in _EMPTY_STRINGS else s


def _number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = _text(value)
    if not s:
        return None
    m = re.search(r"\d[\d,]*(?:\.\d+)?", s)
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _as_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = _text(value)
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(s[:20], fmt).date()
        except ValueError:
            continue
    return None


def _iso(d: Optional[date]) -> Optional[str]:
    return d.isoformat() if d else None


def _add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    # Clamp the day to the last valid day of the target month.
    for day in (d.day, 30, 29, 28):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    return date(year, month, 28)


def _rupees(text: str) -> str:
    """Show 'INR 40,000' / 'Rs. 3,000' / 'Rs 3000' as '₹40,000' / '₹3,000'."""
    return re.sub(r"\b(?:INR|Rs\.?)\s*(?=\d)", "₹", text, flags=re.IGNORECASE)


def _sentence(text: str) -> str:
    text = text.strip().rstrip(".")
    return text[:1].upper() + text[1:] if text else text


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        s = _text(item) if not isinstance(item, dict) else _dict_line(item)
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out


_NAME_KEYS = ("name", "title", "benefit", "treatment", "disease", "condition", "category", "type", "item", "description")
_VALUE_KEYS = ("limit", "amount", "value", "percentage", "percent", "duration", "period", "days", "months", "coverage", "details", "rule")
_REF_CLAUSE_KEYS = ("clause", "clauseNumber", "clauseRef", "section", "sourceClause")
_REF_PAGE_KEYS = ("page", "pageNumber", "sourcePage")
_REF_TEXT_KEYS = ("sourceText", "clauseText", "quote", "originalText", "wording")


def _dict_line(item: dict) -> Optional[str]:
    name = next((_text(item.get(k)) for k in _NAME_KEYS if _text(item.get(k))), None)
    value = next((_text(item.get(k)) for k in _VALUE_KEYS if _text(item.get(k))), None)
    if name and value and value.lower() not in name.lower():
        return f"{name}: {value}"
    return name or value


def _reference(item: Any) -> Optional[dict]:
    """A stored clause / page reference, only when the data actually has one."""
    if not isinstance(item, dict):
        return None
    clause = next((_text(item.get(k)) for k in _REF_CLAUSE_KEYS if _text(item.get(k))), None)
    page = next((_text(item.get(k)) for k in _REF_PAGE_KEYS if _text(item.get(k))), None)
    quote = next((_text(item.get(k)) for k in _REF_TEXT_KEYS if _text(item.get(k))), None)
    if not (clause or page or quote):
        return None
    return {"clause": clause, "page": page, "text": quote}


def _item(key: str, title: str, value: Optional[str], description: Optional[str] = None, *,
          wording: Optional[str] = None, conditions: Optional[list[str]] = None,
          reference: Optional[dict] = None, available: bool = True) -> dict:
    return {
        "key": key,
        "title": title,
        "value": value,
        "description": description,
        "conditions": conditions or [],
        "wording": wording,
        "reference": reference,
        "available": available,
    }


_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_DURATION = re.compile(r"(\d+)\s*(day|month|year)s?\b", re.IGNORECASE)
# A duration that *is* a waiting period ("after 36 months", "expiry of 36 months"),
# not one inside a definition ("diagnosed not more than 36 months prior").
_WAIT_DURATION = re.compile(
    r"(?:after|expiry of|until|completion of|waiting period of|wait of)\s+(?:the\s+)?(?:first\s+)?(\d+)\s*(day|month|year)s?\b",
    re.IGNORECASE,
)
_LIMIT_PHRASE = re.compile(r"^(?P<what>.{3,80}?)\s+(?:is|are|shall be|will be)?\s*(?:limited|capped|restricted)\s+to\s+(?P<limit>.+)$", re.IGNORECASE)
_HAS_AMOUNT = re.compile(r"(?:₹|\bINR\b|\bRs\.?)\s*\d|\d\s*%", re.IGNORECASE)
_NONE_STATEMENT = re.compile(r"^(?:no|nil)\s+(?:special conditions|loadings?)\b[^.]*\.?$", re.IGNORECASE)


def _duration_label(n: int, unit: str) -> str:
    unit = unit.lower()
    return f"{n} {unit}{'' if n == 1 else 's'}"


def _days_label(days: int) -> str:
    if days >= 365 and days % 365 == 0:
        years = days // 365
        return f"{years} year{'s' if years != 1 else ''} ({days:,} days)"
    return f"{days:,} day{'s' if days != 1 else ''}"


# ─────────────────────────────────────────────────────────────────────────────
# Sections
# ─────────────────────────────────────────────────────────────────────────────

def _sum_insured(pj: dict, policy) -> dict:
    amount = _number(pj.get("coverageAmount")) or _number(pj.get("sumInsured"))
    source = "policy document"
    if not amount or amount < 1000:
        stored = _number(getattr(policy, "coverage_amount", None))
        # Tiny values (e.g. 0.15) are not a real sum insured.
        amount = stored if stored and stored >= 1000 else None
        source = "policy record"
    return {
        **_item(
            "sumInsured",
            "Sum Insured",
            None,
            "The most your policy pays, subject to its terms." if amount else "Your policy details don't state the sum insured.",
            available=bool(amount),
        ),
        "amount": amount,
        "source": source if amount else None,
    }


def _co_payment(pj: dict) -> dict:
    raw = _text(pj.get("coPay")) or _text(pj.get("coPayment"))
    rules = pj.get("coPaymentRules") if isinstance(pj.get("coPaymentRules"), list) else []
    rule_lines = [_rupees(s) for s in _string_list(rules)]
    reference = next((_reference(r) for r in rules if _reference(r)), None)

    if not raw and not rule_lines:
        return _item("coPayment", "Co-payment", None, "Your policy details don't mention a co-payment.", available=False)

    text = raw or ""
    low = text.lower()
    percents = sorted({p for p in _PERCENT.findall(text)}, key=float)
    says_none = bool(re.match(r"^(?:nil|none|no\b|not applicable)", low)) or "no co-payment" in low or "no copayment" in low
    conditional = bool(re.search(r"\b(if|when|where|aged|age|above|below|zone|city|network|non-network|in case)\b", low))

    conditions: list[str] = []
    paren = re.search(r"\(([^)]+)\)", text)
    if says_none:
        if percents and paren:
            value = "None*"
            description = "No co-payment in general, but some conditions apply."
            conditions.append(_sentence(paren.group(1)))
        else:
            value = "None"
            description = "You don't pay a share of eligible claims."
    elif len(percents) == 1 and not conditional:
        value = f"{_fmt_pct(percents[0])}%"
        description = f"You pay {value} of the eligible claim amount."
    elif percents:
        value = " / ".join(f"{_fmt_pct(p)}%" for p in percents)
        description = "The share you pay depends on the conditions below."
        conditions.append(_sentence(text))
    else:
        value = "Applies"
        description = "A co-payment applies, but the percentage isn't stated in your policy details."

    conditions.extend(c for c in rule_lines if c not in conditions)
    return _item("coPayment", "Co-payment", value, description, wording=raw, conditions=conditions, reference=reference)


def _fmt_pct(p: str) -> str:
    f = float(p)
    return str(int(f)) if f.is_integer() else str(f)


def _deductible(pj: dict) -> dict:
    raw = pj.get("deductibles") if pj.get("deductibles") not in (None, "") else pj.get("deductible")
    if isinstance(raw, list):
        lines = [_rupees(s) for s in _string_list(raw)]
        if not lines:
            return _item("deductible", "Deductible", None, "Your policy details don't mention a deductible.", available=False)
        return _item("deductible", "Deductible", "Applies" if len(lines) > 1 else _short_value(lines[0]),
                     "Amount you pay before the policy starts paying.", wording="\n".join(lines), conditions=lines if len(lines) > 1 else [])
    text = _text(raw)
    if not text:
        return _item("deductible", "Deductible", None, "Your policy details don't mention a deductible.", available=False)
    if re.match(r"^(?:nil|none|no\b|not applicable)", text.lower()):
        return _item("deductible", "Deductible", "None", "No fixed amount to pay before the policy pays.", wording=text)
    return _item("deductible", "Deductible", _short_value(_rupees(text)),
                 "Amount you pay before the policy starts paying.", wording=text)


def _short_value(text: str) -> str:
    """The leading amount/percentage of a phrase, e.g. '₹10,000 per claim ...' → '₹10,000'."""
    si = re.search(r"(\d+(?:\.\d+)?)\s*%\s*of\s*(?:the\s+)?sum insured", text, re.IGNORECASE)
    if si:
        return f"{_fmt_pct(si.group(1))}% of SI"
    m = re.search(r"₹\s*[\d,]+(?:\.\d+)?(?:\s*(?:lakh|lakhs|crore))?|\d+(?:\.\d+)?\s*%", text, re.IGNORECASE)
    return m.group(0).replace(" ", "") if m else (text if len(text) <= 28 else "Applies")


def _waiting_periods(pj: dict, start: Optional[date], basis: Optional[str], today: date) -> dict:
    periods: list[dict] = []

    def add(key: str, label: str, n: Optional[int], unit: Optional[str], description: str, wording: Optional[str],
            reference: Optional[dict] = None, conditions: Optional[list[str]] = None):
        end = None
        if n and unit and start:
            unit_l = unit.lower()
            if unit_l.startswith("day"):
                from datetime import timedelta
                end = start + timedelta(days=n)
            elif unit_l.startswith("month"):
                end = _add_months(start, n)
            elif unit_l.startswith("year"):
                end = _add_months(start, 12 * n)
        if n and unit:
            duration = _days_label(n) if unit.lower().startswith("day") else _duration_label(n, unit)
        else:
            duration = None
        periods.append({
            "key": key,
            "title": label,
            "duration": duration,
            "description": description,
            "endDate": _iso(end),
            "status": None if not end else ("over" if end <= today else "running"),
            "wording": wording,
            "reference": reference,
            "conditions": conditions or [],
        })

    # 1. Initial waiting period
    initial_days = _number(pj.get("waitingPeriodDays"))
    if initial_days and initial_days > 0:
        add("initial", "Initial waiting period", int(initial_days), "day",
            "Illness claims are not paid during this time from the start of cover.", None)

    # 2. Pre-existing diseases
    ped = _text(pj.get("preExistingDiseases"))
    if ped:
        m = _WAIT_DURATION.search(ped)
        add("preExisting", "Pre-existing diseases",
            int(m.group(1)) if m else None, m.group(2) if m else None,
            "Illnesses you had before buying the policy are covered only after this time.",
            ped)

    # 3. Specific illnesses: only when the stored text states the duration.
    specific: dict[tuple[int, str], list[str]] = {}
    for line in _string_list(pj.get("coveredDiseases")):
        m = _WAIT_DURATION.search(line)
        if m:
            name = re.sub(r"\s*\((?:after|waiting)[^)]*\)\s*", "", line, flags=re.IGNORECASE).strip()
            specific.setdefault((int(m.group(1)), m.group(2).lower()), []).append(name or line)
    for (n, unit), names in sorted(specific.items()):
        add(f"specific_{n}{unit[0]}", "Specific illnesses & treatments", n, unit,
            "Listed illnesses and treatments are covered only after this time.", None, conditions=names)

    # 4. Structured waiting periods, when the extraction filled them in.
    raw_list = pj.get("waitingPeriods") if isinstance(pj.get("waitingPeriods"), list) else []
    for i, wp in enumerate(raw_list):
        line = _dict_line(wp) if isinstance(wp, dict) else _text(wp)
        if not line:
            continue
        name = (next((_text(wp.get(k)) for k in _NAME_KEYS if _text(wp.get(k))), None) if isinstance(wp, dict) else None) or line
        m = _DURATION.search(line)
        add(f"listed_{i}", _sentence(name)[:60], int(m.group(1)) if m else None, m.group(2) if m else None,
            "Waiting period stated in your policy.", line, reference=_reference(wp))

    # Policy notes that change how waiting periods work (accidents, sum-insured increases, etc.).
    notes = [_sentence(s) for s in _string_list(pj.get("specialConditions")) if "waiting period" in s.lower()]

    return {
        "periods": periods,
        "startDate": _iso(start),
        "basis": basis,
        "notes": notes,
    }


def _room_rent(pj: dict, used: set[str]) -> Optional[dict]:
    raw = _text(pj.get("roomEligibility")) or _text(pj.get("roomRent"))
    room_notes = [s for s in _string_list(pj.get("specialConditions")) if re.search(r"\broom\b", s, re.IGNORECASE)]
    used.update(room_notes)
    if not raw:
        if not room_notes:
            return None
        return _item("roomRent", "Room rent", "See conditions", None, conditions=[_sentence(_rupees(s)) for s in room_notes])
    low = raw.lower()
    shown = _rupees(raw)
    if re.search(r"no (?:room rent )?capping|no (?:room )?limit|no restriction|any room|up to sum insured", low):
        value, description = "No limit", "No cap on the room category you choose."
    else:
        pct = re.search(r"(\d+(?:\.\d+)?)\s*%\s*of\s*(?:the\s+)?sum insured", low)
        amt = re.search(r"₹\s*[\d,]+", shown)
        if pct:
            value = f"{_fmt_pct(pct.group(1))}% of SI / day"
            description = f"Up to {amt.group(0).replace(' ', '')} per day." if amt else "Room charges are capped per day."
        elif amt:
            value = f"{amt.group(0).replace(' ', '')} / day"
            description = "Room charges are capped per day."
        else:
            value = _sentence(shown) if len(shown) <= 32 else "Limited"
            description = None if len(shown) <= 32 else _sentence(shown)
    conditions = [_sentence(_rupees(s)) for s in room_notes]
    return _item("roomRent", "Room rent", value, description, wording=raw, conditions=conditions)


def _limits(pj: dict, used: set[str]) -> list[dict]:
    out: list[dict] = []

    icu = _text(pj.get("icu"))
    if icu and not re.fullmatch(r"covered\.?", icu, re.IGNORECASE):
        shown = _rupees(icu)
        pct = re.search(r"(\d+(?:\.\d+)?)\s*%\s*of\s*(?:the\s+)?sum insured", shown, re.IGNORECASE)
        if pct:
            value = f"{_fmt_pct(pct.group(1))}% of SI / day"
        elif re.search(r"actual|up to sum insured", shown, re.IGNORECASE):
            value = "Up to sum insured"
        else:
            value = _short_value(shown)
        out.append(_item("icu", "ICU charges", value, _sentence(shown), wording=icu))

    modern = _text(pj.get("modernTreatmentsLimit"))
    if modern:
        shown = _rupees(modern)
        pct = re.search(r"(\d+(?:\.\d+)?)\s*%\s*of\s*(?:the\s+)?sum insured", shown, re.IGNORECASE)
        value = f"{_fmt_pct(pct.group(1))}% of SI" if pct else ("Up to sum insured" if "sum insured" in shown.lower() else _short_value(shown))
        out.append(_item("modernTreatments", "Modern treatments",
                         value, "Robotic surgery, immunotherapy and similar advanced treatments.", wording=modern))

    # Structured sub-limits (when the extraction filled them in).
    for i, sl in enumerate(pj.get("subLimits") if isinstance(pj.get("subLimits"), list) else []):
        if isinstance(sl, dict):
            name = next((_text(sl.get(k)) for k in _NAME_KEYS if _text(sl.get(k))), None)
            limit = next((_text(sl.get(k)) for k in _VALUE_KEYS if _text(sl.get(k))), None)
            line = _dict_line(sl)
        else:
            line = _text(sl)
            name, limit = None, None
        if not line:
            continue
        if not (name and limit):
            m = _LIMIT_PHRASE.match(line)
            name, limit = (m.group("what"), m.group("limit")) if m else (line, None)
        out.append(_item(f"subLimit_{i}", _sentence(name)[:60], _short_value(_rupees(limit)) if limit else "See details",
                         _sentence(_rupees(limit)) if limit else None, wording=line, reference=_reference(sl)))

    # Treatment limits written as policy conditions ("Cataract is limited to INR 40,000 per eye").
    for s in _string_list(pj.get("specialConditions")):
        if s in used:
            continue
        m = _LIMIT_PHRASE.match(s)
        if not m or not _HAS_AMOUNT.search(s):
            continue
        used.add(s)
        what = re.sub(r"^(?:expenses for|the|claims? for)\s+", "", m.group("what").strip(), flags=re.IGNORECASE)
        limit = _rupees(m.group("limit").strip().rstrip("."))
        out.append(_item(f"condLimit_{len(out)}", _sentence(what)[:60], _short_value(limit), _sentence(limit), wording=s))

    return out


_COVERED_KEYWORDS = [
    ("ayush", "AYUSH treatment", "Ayurveda, Yoga, Unani, Siddha and Homeopathy treatment."),
    ("organ donor", "Organ donor expenses", "Costs of the organ donor's surgery."),
    ("domiciliary", "Treatment at home", "Hospital-level treatment taken at home (domiciliary)."),
    ("maternity", "Maternity", "Childbirth-related expenses."),
    ("restoration", "Restoration benefit", "Sum insured is refilled after it is used up."),
    ("recharge", "Restoration benefit", "Sum insured is refilled after it is used up."),
    ("no claim bonus", "No-claim bonus", "Extra cover for claim-free years."),
    ("cumulative bonus", "No-claim bonus", "Extra cover for claim-free years."),
    ("outpatient", "OPD cover", "Doctor visits and treatment without hospital admission."),
    ("opd", "OPD cover", "Doctor visits and treatment without hospital admission."),
    ("health check", "Health check-up", "Preventive health check-up."),
]


def _additional(pj: dict, used: set[str]) -> list[dict]:
    out: list[dict] = []

    pre = _number(pj.get("preHospitalizationDays"))
    if pre:
        out.append(_item("preHospitalization", "Before hospitalisation", f"{int(pre)} days",
                         f"Tests and medicines up to {int(pre)} days before admission."))
    post = _number(pj.get("postHospitalizationDays"))
    if post:
        out.append(_item("postHospitalization", "After hospitalisation", f"{int(post)} days",
                         f"Follow-up costs up to {int(post)} days after discharge."))

    day_care = _text(pj.get("dayCare"))
    if day_care and not re.match(r"^(?:not covered|no)\b", day_care, re.IGNORECASE):
        out.append(_item("dayCare", "Daycare procedures", "Covered",
                         "Treatments that need less than 24 hours in hospital.", wording=day_care))

    emergency = _text(pj.get("emergency"))
    ambulance_note = next((s for s in _string_list(pj.get("specialConditions"))
                           if "ambulance" in s.lower() and s not in used), None)
    if emergency and "ambulance" in emergency.lower():
        amt = re.search(r"₹\s*[\d,]+", _rupees(emergency))
        out.append(_item("ambulance", "Ambulance", amt.group(0).replace(" ", "") if amt else "Covered",
                         _sentence(_rupees(emergency)), wording=emergency))
    elif ambulance_note:
        used.add(ambulance_note)
        amt = re.search(r"₹\s*[\d,]+", _rupees(ambulance_note))
        out.append(_item("ambulance", "Ambulance", amt.group(0).replace(" ", "") if amt else "Covered",
                         _sentence(_rupees(ambulance_note)), wording=ambulance_note))
    elif emergency and not re.match(r"^(?:not covered|no)\b", emergency, re.IGNORECASE):
        out.append(_item("emergency", "Emergency care", "Covered", None if re.fullmatch(r"covered\.?", emergency, re.IGNORECASE) else _sentence(emergency), wording=emergency))

    network = _text(pj.get("networkHospitalRules"))
    if network and "cashless" in network.lower():
        out.append(_item("cashless", "Cashless hospitalisation", "Available",
                         "At network hospitals, the insurer pays the hospital directly.", wording=network))
    elif network:
        out.append(_item("cashless", "Network hospitals", "See details", _sentence(network), wording=network))

    hosp = _text(pj.get("hospitalization"))
    if hosp:
        hrs = re.search(r"(\d+)\s*(?:consecutive\s+)?(?:in-patient\s+care\s+)?(?:hours|hrs)", hosp, re.IGNORECASE)
        out.append(_item("hospitalization", "In-patient hospitalisation", "Covered",
                         f"Needs at least {hrs.group(1)} hours in hospital." if hrs else _sentence(hosp), wording=hosp))

    # Benefits the policy lists as covered (keyword match on stored lists only).
    covered = _string_list(pj.get("coveredTreatments")) + _string_list(pj.get("coverages"))
    conditions = [s for s in _string_list(pj.get("specialConditions")) if s not in used]
    seen_titles = {o["title"] for o in out}
    for keyword, title, description in _COVERED_KEYWORDS:
        if title in seen_titles or any(keyword in u.lower() for u in used):
            continue  # already shown as a limit above
        hit = next((c for c in covered if keyword in c.lower()), None)
        cond = next((c for c in conditions if keyword in c.lower()), None)
        if keyword == "health check" and not cond:
            tests = _text(pj.get("medicalTestsCoverage")) or ""
            cond = tests if "health check" in tests.lower() else None
        if not (hit or cond):
            continue
        if cond:
            used.add(cond)
        seen_titles.add(title)
        amt = re.search(r"₹\s*[\d,]+", _rupees(cond)) if cond else None
        out.append(_item(f"benefit_{keyword.replace(' ', '_')}", title,
                         amt.group(0).replace(" ", "") if amt else "Covered", description,
                         wording=cond or hit, conditions=[_sentence(_rupees(cond))] if cond else []))

    # Riders / add-ons listed on the policy.
    for i, rider in enumerate(pj.get("riders") if isinstance(pj.get("riders"), list) else []):
        line = _dict_line(rider) if isinstance(rider, dict) else _text(rider)
        if line:
            out.append(_item(f"rider_{i}", _sentence(line.split(":")[0])[:60], "Add-on",
                             _sentence(line) if ":" in line else None, wording=line, reference=_reference(rider)))
    return out


def _conditions(pj: dict, used: set[str]) -> dict:
    special = []
    for s in _string_list(pj.get("specialConditions")):
        if s in used or "waiting period" in s.lower():
            continue
        # Drop filler such as "No special conditions imposed. No loadings applied."
        kept = [x for x in re.split(r"(?<=\.)\s+", s) if x.strip() and not _NONE_STATEMENT.match(x.strip())]
        if kept:
            special.append(_sentence(_rupees(" ".join(kept))))

    exclusions_raw = (_string_list(pj.get("excludedDiseases")) + _string_list(pj.get("exclusions"))
                      + _string_list(pj.get("excludedTreatments")))
    seen: set[str] = set()
    major, items = [], []
    for e in exclusions_raw:
        k = e.lower()
        if k in seen:
            continue
        seen.add(k)
        # Long sentences are real exclusions; short entries are the non-payable item lists.
        (major if len(e) > 60 else items).append(_sentence(_rupees(e)))
    return {"special": special, "exclusions": major, "nonPayableItems": items}


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def build_policy_benefits(policy, *, waiting_start: Optional[str] = None, waiting_basis: Optional[str] = None,
                          today: Optional[date] = None) -> dict:
    """
    Plain-language benefits for one Policy document, from its stored extraction only.

    `waiting_start` / `waiting_basis` are the waiting-period start date and its
    explanation, as worked out by the analysis pipeline from the user's continuity answers.
    """
    today = today or date.today()
    pj: dict = dict(getattr(policy, "extracted_policy_json", None) or {})

    def first_text(*vals):
        return next((t for t in (_text(v) for v in vals) if t and t != "Uploaded Policy"), None)

    start = _as_date(getattr(policy, "policy_start_date", None)) or _as_date(pj.get("policyStartDate"))
    end = _as_date(getattr(policy, "policy_end_date", None)) or _as_date(pj.get("policyEndDate"))

    overview = {
        "policyName": first_text(getattr(policy, "policy_name", None), pj.get("policyName"), pj.get("planName")),
        "insuranceCompany": first_text(getattr(policy, "insurance_company", None), pj.get("insuranceCompany")),
        "policyNumber": first_text(getattr(policy, "policy_number", None), pj.get("policyNumber")),
        "policyHolderName": first_text(getattr(policy, "policy_holder_name", None), pj.get("policyHolder"), pj.get("policyholderName")),
        "policyType": first_text(getattr(policy, "policy_type", None), pj.get("policyType")),
        "startDate": _iso(start),
        "endDate": _iso(end),
        "status": (
            "unknown" if not end and not start else
            "upcoming" if start and start > today else
            "expired" if end and end < today else
            "active" if end else "unknown"
        ),
    }

    has_details = any(
        k in pj and pj.get(k) not in (None, "", [], {})
        for k in ("coverageAmount", "coPay", "roomEligibility", "waitingPeriodDays", "preExistingDiseases",
                  "preHospitalizationDays", "subLimits", "specialConditions", "coveredTreatments")
    )

    used: set[str] = set()
    wp_start = _as_date(waiting_start) or _as_date(pj.get("waitingPeriodStartDate"))
    basis = waiting_basis or _text(pj.get("waitingPeriodBasis"))
    room = _room_rent(pj, used)
    limits = _limits(pj, used)
    additional = _additional(pj, used)

    return {
        "policyId": str(getattr(policy, "id", "") or ""),
        "overview": overview,
        "hasDetails": has_details,
        "keyBenefits": {
            "sumInsured": _sum_insured(pj, policy),
            "coPayment": _co_payment(pj),
            "deductible": _deductible(pj),
        },
        "waitingPeriods": _waiting_periods(pj, wp_start, basis if wp_start else None, today),
        "roomRent": room,
        "limits": limits,
        "additionalBenefits": additional,
        "conditions": _conditions(pj, used),
        # Stored data has no page/clause references unless an item above carries one.
        "hasSourceReferences": False,
    }
