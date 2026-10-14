"""
Policy Continuity Check.

Answers two questions deterministically:

  1. When did the insured's cover originally start?
  2. Has cover continued without interruption up to the claim/hospitalization date?

The LLM only *extracts* facts from each policy document (policy periods, policy
numbers, previous-policy references, renewal statements, "continuous coverage
since" statements and supporting quotes). Every comparison — linking renewals,
ordering periods, measuring gaps and choosing the status — happens here with
plain date arithmetic, so the same documents always produce the same result.

Missing history is never read as a gap. When the documents do not say enough,
the result is "Continuity Cannot Be Established".
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.services.rule_engine.date_utils import parse_flexible_date

ENGINE_VERSION = "1.0.0"

STATUS_VERIFIED = "Continuity Verified"
STATUS_GAP = "Coverage Gap Detected"
STATUS_UNKNOWN = "Continuity Cannot Be Established"
STATUS_CONFLICT = "Continuity Conflict"

STATUS_CODES = {
    STATUS_VERIFIED: "VERIFIED",
    STATUS_GAP: "GAP_DETECTED",
    STATUS_UNKNOWN: "CANNOT_BE_ESTABLISHED",
    STATUS_CONFLICT: "CONFLICT",
}

# A renewal starting on the day the previous period ends, or the day after, is continuous.
MAX_CONTINUOUS_STEP_DAYS = 1
# Typical renewal grace window. Only used to annotate a gap for the reader — it
# never hides a gap or changes the status.
RENEWAL_GRACE_DAYS = 30

# Link kinds, strongest first.
LINK_EXPLICIT = "explicit_reference"
LINK_DECLARED = "declared_previous_period"
LINK_SAME_NUMBER = "same_policy_number"
LINK_CONSECUTIVE = "consecutive_periods"
_LINK_STRENGTH = {LINK_EXPLICIT: 0, LINK_DECLARED: 1, LINK_SAME_NUMBER: 2, LINK_CONSECUTIVE: 3}

_PLACEHOLDERS = {"", "unknown", "none", "n/a", "na", "null", "not specified", "undefined", "---", "-"}

_EXTRA_DATE_FORMATS = (
    "%d %b %Y",
    "%d %B %Y",
    "%d-%b-%y",
    "%d/%m/%y",
    "%d-%m-%y",
    "%b %d %Y",
    "%B %d %Y",
    "%Y.%m.%d",
)

# Prescription fields that can carry the claim date, in order of preference:
# a hospitalization date beats a consultation date.
_CLAIM_DATE_FIELDS = (
    ("admissionDate", "Admission date"),
    ("dateOfAdmission", "Admission date"),
    ("hospitalizationDate", "Hospitalization date"),
    ("admission", "Admission date"),
    ("treatmentDate", "Treatment date"),
    ("visitDate", "Visit date"),
    ("consultationDate", "Consultation date"),
    ("prescriptionDate", "Prescription date"),
)


# ──────────────────────────────────────────────────────────────────────────────
# Value normalization
# ──────────────────────────────────────────────────────────────────────────────

def _text(value: Any) -> str:
    """Display text, or "" for anything that only means 'not stated'."""
    if value is None or isinstance(value, (dict, list)):
        return ""
    text = str(value).strip()
    return "" if text.lower() in _PLACEHOLDERS else text


def _number_key(value: Any) -> str:
    """Policy numbers compared without spacing/punctuation differences."""
    return re.sub(r"[^A-Z0-9]", "", _text(value).upper())


_TITLES = re.compile(r"\b(mr|mrs|ms|miss|dr|shri|smt|kum|master)\b\.?", re.IGNORECASE)


def _name_key(value: Any) -> str:
    text = _TITLES.sub(" ", _text(value).lower())
    return " ".join(re.sub(r"[^a-z ]", " ", text).split())


def _bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    text = _text(value).lower()
    if text in ("true", "yes", "y", "renewal", "renewed"):
        return True
    if text in ("false", "no", "n", "fresh", "new"):
        return False
    return None


def parse_policy_date(value: Any) -> Optional[date]:
    """
    Parse a date as it appears in an Indian policy document (day-first).

    Implausible years are rejected so a stray number is never read as a date.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = _text(value)
    if not text:
        return None

    parsed = None
    # Pure numbers are only dates as epoch timestamps, which documents never contain.
    if not re.fullmatch(r"\d+(\.\d+)?", text):
        parsed = parse_flexible_date(text)
    if parsed is None:
        cleaned = re.sub(r"(\d{1,2})(st|nd|rd|th)\b", r"\1", text, flags=re.IGNORECASE)
        cleaned = " ".join(cleaned.replace(",", " ").split())
        for fmt in _EXTRA_DATE_FORMATS:
            try:
                parsed = datetime.strptime(cleaned, fmt).date()
                break
            except ValueError:
                continue

    if parsed is None or not (1950 <= parsed.year <= 2100):
        return None
    return parsed


def _iso(d: Optional[date]) -> Optional[str]:
    return d.isoformat() if d else None


def _fmt(d: Optional[date]) -> str:
    return d.strftime("%d %b %Y") if d else "an unstated date"


def resolve_claim_date(prescription_json: Optional[dict]) -> Tuple[Optional[date], str]:
    """The claim/hospitalization date and the field it came from."""
    rx = prescription_json or {}
    for key, label in _CLAIM_DATE_FIELDS:
        parsed = parse_policy_date(rx.get(key))
        if parsed:
            return parsed, label
    return None, ""


# ──────────────────────────────────────────────────────────────────────────────
# Periods and links
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class _Period:
    key: str
    policy_id: str
    label: str
    number: str
    insurer: str
    holder_key: str
    start: Optional[date]
    end: Optional[date]
    is_renewal: Optional[bool] = None
    previous_number: str = ""
    previous_start: Optional[date] = None
    previous_end: Optional[date] = None
    previous_insurer: str = ""
    continuous_since: Optional[date] = None
    renewal_info: str = ""
    quotes: List[str] = field(default_factory=list)
    # A period that is only *stated* inside another policy (its document was not provided).
    declared_by: str = ""

    @property
    def number_key(self) -> str:
        return _number_key(self.number)

    @property
    def previous_number_key(self) -> str:
        return _number_key(self.previous_number)

    @property
    def sort_date(self) -> date:
        return self.start or self.end or date.max

    def covers(self, d: date) -> bool:
        return bool(self.start and self.end and self.start <= d <= self.end)


def _label_for(item: dict, policy_json: dict) -> str:
    number = _text(_field(policy_json, "policyNumber"))
    for candidate in (
        item.get("label"),
        policy_json.get("policyName"),
        _field(policy_json, "insuranceCompany"),
    ):
        text = _text(candidate)
        if text:
            return f"{text} ({number})" if number and number not in text else text
    return f"Policy {number}" if number else "Policy"


def _field(policy_json: dict, key: str) -> Any:
    """
    A fact about the policy: the main extraction's (or user-confirmed) top-level
    value first, then the dedicated continuity extraction stored under "continuity".
    """
    value = policy_json.get(key)
    if value is not None and value != "" and value != []:
        return value
    block = policy_json.get("continuity")
    return block.get(key) if isinstance(block, dict) else None


def _quotes(policy_json: dict) -> List[str]:
    raw = _field(policy_json, "continuityEvidence")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out = []
    for q in raw:
        text = _text(q)
        if text and text not in out:
            out.append(text[:300])
    return out[:5]


def _period_from_input(index: int, item: dict) -> _Period:
    pj = item.get("policyJson") or {}
    start = parse_policy_date(_field(pj, "policyStartDate")) or parse_policy_date(item.get("fallbackStartDate"))
    return _Period(
        key=f"p{index}",
        policy_id=str(item.get("policyId") or ""),
        label=_label_for(item, pj),
        number=_text(_field(pj, "policyNumber")),
        insurer=_text(_field(pj, "insuranceCompany")),
        holder_key=_name_key(_field(pj, "policyHolder") or pj.get("policyholderName")),
        start=start,
        end=parse_policy_date(_field(pj, "policyEndDate")),
        is_renewal=_bool(_field(pj, "isRenewal")),
        previous_number=_text(_field(pj, "previousPolicyNumber")),
        previous_start=parse_policy_date(_field(pj, "previousPolicyStartDate")),
        previous_end=parse_policy_date(_field(pj, "previousPolicyEndDate")),
        previous_insurer=_text(_field(pj, "previousInsurer")),
        continuous_since=parse_policy_date(_field(pj, "continuousCoverageSince")),
        renewal_info=_text(_field(pj, "renewalInformation"))[:300],
        quotes=_quotes(pj),
    )


class _Evaluation:
    """Working state for one evaluation; turned into the result dict at the end."""

    def __init__(self):
        self.periods: List[_Period] = []
        self.by_key: Dict[str, _Period] = {}
        self.links: Dict[Tuple[str, str], str] = {}     # (earlier, later) -> kind
        self.conflicts: List[Tuple[set, str]] = []      # (involved keys, text)
        self.notes: List[Tuple[set, str]] = []          # (involved keys, text)
        self.excluded: List[dict] = []                  # undated / duplicate inputs

    def add(self, period: _Period) -> None:
        self.periods.append(period)
        self.by_key[period.key] = period

    def link(self, a: _Period, b: _Period, kind: str) -> None:
        """Record a continuity link between two periods (keeps the strongest kind)."""
        first, second = sorted((a, b), key=lambda p: p.sort_date)
        pair = (first.key, second.key)
        current = self.links.get(pair)
        if current is None or _LINK_STRENGTH[kind] < _LINK_STRENGTH[current]:
            self.links[pair] = kind

    def neighbours(self, key: str) -> List[str]:
        out = []
        for (a, b) in self.links:
            if a == key:
                out.append(b)
            elif b == key:
                out.append(a)
        return out

    def component(self, key: str) -> List[_Period]:
        seen, stack = {key}, [key]
        while stack:
            for nxt in self.neighbours(stack.pop()):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return [p for p in self.periods if p.key in seen]


def _overlaps(a: _Period, b: _Period) -> bool:
    if not (a.start and a.end and b.start and b.end):
        return False
    return max(a.start, b.start) <= min(a.end, b.end)


def _build(policies: List[dict]) -> _Evaluation:
    ev = _Evaluation()

    # 1. One period per provided policy document.
    for index, item in enumerate(policies or []):
        period = _period_from_input(index, item)
        if period.start is None:
            ev.excluded.append({
                "policyId": period.policy_id,
                "label": period.label,
                "policyNumber": period.number,
                "role": "undated",
                "reason": "Policy start date is not stated in the document.",
            })
            continue
        if period.end and period.end < period.start:
            ev.add(period)
            ev.conflicts.append((
                {period.key},
                f"{period.label}: policy end date {_fmt(period.end)} is before its start date {_fmt(period.start)}.",
            ))
            period.end = None
            continue
        duplicate = next(
            (p for p in ev.periods
             if p.number_key and p.number_key == period.number_key
             and p.start == period.start and p.end == period.end),
            None,
        )
        if duplicate:
            ev.excluded.append({
                "policyId": period.policy_id,
                "label": period.label,
                "policyNumber": period.number,
                "startDate": _iso(period.start),
                "endDate": _iso(period.end),
                "role": "duplicate",
                "reason": f"Same policy number and period as {duplicate.label}.",
            })
            continue
        ev.add(period)

    uploaded = list(ev.periods)

    # 2. Previous-policy periods stated inside a document. When that previous
    #    policy was also provided, the statement is checked against it;
    #    otherwise it stands in as a declared period.
    for p in uploaded:
        if not (p.previous_start or p.previous_end):
            continue
        matched = next(
            (q for q in uploaded if q is not p and p.previous_number_key and q.number_key == p.previous_number_key),
            None,
        )
        if matched:
            if p.previous_end and matched.end and p.previous_end != matched.end:
                ev.conflicts.append((
                    {p.key, matched.key},
                    f"{p.label} states the previous policy ended on {_fmt(p.previous_end)}, "
                    f"but {matched.label} ends on {_fmt(matched.end)}.",
                ))
            if p.previous_start and matched.start and p.previous_start != matched.start:
                ev.conflicts.append((
                    {p.key, matched.key},
                    f"{p.label} states the previous policy started on {_fmt(p.previous_start)}, "
                    f"but {matched.label} starts on {_fmt(matched.start)}.",
                ))
            continue
        declared = _Period(
            key=f"{p.key}-prev",
            policy_id="",
            label=(f"Previous policy {p.previous_number}" if p.previous_number else "Previous policy")
            + f" (stated in {p.label})",
            number=p.previous_number,
            insurer=p.previous_insurer,
            holder_key="",
            start=p.previous_start,
            end=p.previous_end,
            declared_by=p.key,
        )
        ev.add(declared)
        ev.link(declared, p, LINK_DECLARED)

    # 3. Links between provided documents.
    for i, a in enumerate(uploaded):
        for b in uploaded[i + 1:]:
            for x, y in ((a, b), (b, a)):
                if y.previous_number_key and y.previous_number_key == x.number_key:
                    ev.link(x, y, LINK_EXPLICIT)
                    if x.holder_key and y.holder_key and x.holder_key != y.holder_key:
                        ev.notes.append((
                            {x.key, y.key},
                            f"{y.label} names {x.label} as its previous policy, but the policyholder names differ.",
                        ))

            if a.number_key and a.number_key == b.number_key:
                ev.link(a, b, LINK_SAME_NUMBER)
                if _overlaps(a, b):
                    ev.conflicts.append((
                        {a.key, b.key},
                        f"{a.label} and {b.label} carry the same policy number but have overlapping, "
                        f"different periods ({_fmt(a.start)} – {_fmt(a.end)} and {_fmt(b.start)} – {_fmt(b.end)}).",
                    ))

            _consider_consecutive(ev, a, b)

    # 4. A policy that declares itself fresh cannot also be a continuation.
    for (k1, k2), kind in list(ev.links.items()):
        later = ev.by_key[k2]
        if later.is_renewal is False and kind in (LINK_EXPLICIT, LINK_SAME_NUMBER, LINK_DECLARED):
            ev.conflicts.append((
                {k1, k2},
                f"{later.label} is described as a fresh (non-renewal) policy, but the documents link it to "
                f"{ev.by_key[k1].label}.",
            ))

    return ev


def _consider_consecutive(ev: _Evaluation, a: _Period, b: _Period) -> None:
    """Link two documents whose periods follow each other, unless the documents say otherwise."""
    first, second = sorted((a, b), key=lambda p: p.sort_date)
    if not (first.end and second.start):
        return
    step = (second.start - first.end).days
    if not (-MAX_CONTINUOUS_STEP_DAYS <= step <= MAX_CONTINUOUS_STEP_DAYS):
        return
    if (first.key, second.key) in ev.links:
        return  # already linked more strongly

    involved = {first.key, second.key}
    if first.holder_key and second.holder_key and first.holder_key != second.holder_key:
        ev.notes.append((
            involved,
            f"{first.label} and {second.label} have consecutive periods but different policyholders, "
            "so they were not treated as continuous cover.",
        ))
        return
    if second.previous_number_key and first.number_key and second.previous_number_key != first.number_key:
        ev.notes.append((
            involved,
            f"{second.label} names {second.previous_number} as its previous policy, not {first.label}, "
            "so their consecutive periods were not treated as continuous cover.",
        ))
        return
    if second.is_renewal is False:
        ev.notes.append((
            involved,
            f"{second.label} is described as a fresh policy, so it was not treated as a continuation of {first.label}.",
        ))
        return

    ev.link(first, second, LINK_CONSECUTIVE)
    if first.insurer and second.insurer and first.insurer.lower() != second.insurer.lower():
        ported = second.previous_insurer and second.previous_insurer.lower() == first.insurer.lower()
        if not ported:
            ev.notes.append((
                involved,
                f"{first.label} and {second.label} are with different insurers and the documents do not "
                "mention portability; continuity is supported by dates only.",
            ))


# ──────────────────────────────────────────────────────────────────────────────
# Evaluation
# ──────────────────────────────────────────────────────────────────────────────

def _link_evidence(a: _Period, b: _Period, kind: str) -> dict:
    if kind == LINK_EXPLICIT:
        text = f"{b.label} names {a.label} as its previous policy."
    elif kind == LINK_DECLARED:
        text = (
            f"{b.label} states a previous policy period of {_fmt(a.start)} – {_fmt(a.end)}"
            + (f" (policy {a.number})." if a.number else ".")
        )
    elif kind == LINK_SAME_NUMBER:
        text = f"{a.label} and {b.label} carry the same policy number {b.number or a.number}."
    else:
        text = (
            f"{b.label} starts on {_fmt(b.start)}, immediately after {a.label} ends on {_fmt(a.end)}."
        )
    return {"type": kind, "description": text, "policyLabels": [a.label, b.label]}


def _period_row(p: _Period, role: str, reason: str = "") -> dict:
    row = {
        "policyId": p.policy_id,
        "label": p.label,
        "policyNumber": p.number,
        "insuranceCompany": p.insurer,
        "startDate": _iso(p.start),
        "endDate": _iso(p.end),
        "role": role,
        "isRenewal": p.is_renewal,
        "previousPolicyNumber": p.previous_number,
        "continuousCoverageSince": _iso(p.continuous_since),
    }
    if reason:
        row["reason"] = reason
    return row


def evaluate_policy_continuity(
    policies: List[dict],
    prescription_json: Optional[dict],
    current_policy_id: Optional[str] = None,
) -> dict:
    """
    Evaluate continuity of cover up to the claim date.

    `policies` is a list of {"policyId", "label", "policyJson", "fallbackStartDate"}
    — every policy document available for this claim. `current_policy_id` is the
    policy the report is about; when omitted the first policy is used.
    """
    ev = _build(policies)
    claim_date, claim_source = resolve_claim_date(prescription_json)

    target = None
    if current_policy_id:
        target = next((p for p in ev.periods if p.policy_id == str(current_policy_id) and not p.declared_by), None)
    elif ev.periods:
        target = next((p for p in ev.periods if not p.declared_by), None)

    component = ev.component(target.key) if target else []
    component_keys = {p.key for p in component}

    evidence: List[dict] = []
    missing: List[str] = []
    gaps: List[dict] = []

    if claim_date is None:
        missing.append("Claim/hospitalization date is not available from the prescription.")
    if target is None:
        missing.append("The policy's start date is not stated, so its period cannot be placed on a timeline.")

    # Conflicts/notes only matter when they touch this policy's lineage.
    scope = component_keys or ({target.key} if target else set())
    conflicts = [text for keys, text in ev.conflicts if keys & scope]
    for keys, text in ev.notes:
        if keys & scope:
            evidence.append({"type": "note", "description": text, "policyLabels": []})

    current = None
    timeline: List[_Period] = []
    later: List[_Period] = []
    claim_covered = False
    unverified_links: List[str] = []
    continuous_from = None

    if target is not None and claim_date is not None:
        covering = [p for p in component if p.covers(claim_date)]
        current = max(covering, key=lambda p: p.start) if covering else target
        claim_covered = bool(covering)

        timeline = sorted(
            (p for p in component if p.sort_date <= claim_date),
            key=lambda p: p.sort_date,
        )
        later = [p for p in component if p.sort_date > claim_date]

        # Walk the periods in order, tracking how far known cover extends.
        frontier: Optional[date] = None
        started = False
        for p in timeline:
            if not started:
                frontier, continuous_from, started = p.end, p.start, True
                continue
            if frontier is None:
                unverified_links.append(
                    f"The end date before {p.label} is not stated, so the step into it cannot be checked."
                )
            elif p.start and (p.start - frontier).days > MAX_CONTINUOUS_STEP_DAYS:
                _add_gap(gaps, frontier, p.start, p, timeline)
                continuous_from = p.start
            if p.end is None:
                frontier = None
            elif frontier is not None:
                frontier = max(frontier, p.end)
            else:
                frontier = p.end

        if not claim_covered:
            next_start = min((p.start for p in later if p.start), default=None)
            if frontier and frontier < claim_date and next_start:
                # The claim date falls between two linked periods.
                gaps.append({
                    "from": _iso(frontier + timedelta(days=1)),
                    "to": _iso(next_start - timedelta(days=1)),
                    "days": (next_start - frontier).days - 1,
                    "betweenPolicies": [
                        next((q.label for q in reversed(timeline) if q.end == frontier), ""),
                        next((q.label for q in later if q.start == next_start), ""),
                    ],
                    "coversClaimDate": True,
                    "withinRenewalGraceWindow": (next_start - frontier).days - 1 <= RENEWAL_GRACE_DAYS,
                    "description": (
                        f"No cover between {_fmt(frontier + timedelta(days=1))} and "
                        f"{_fmt(next_start - timedelta(days=1))}, which includes the claim date."
                    ),
                })
            elif frontier is None and timeline and timeline[-1].end is None:
                missing.append(
                    f"The end date of {timeline[-1].label} is not stated, so cover on the claim date cannot be confirmed."
                )
            else:
                missing.append("None of the evaluated policies covers the claim/hospitalization date.")

        if claim_covered:
            evidence.append({
                "type": "claim_date_in_period",
                "description": (
                    f"{claim_source or 'Claim date'} {_fmt(claim_date)} falls within {current.label} "
                    f"({_fmt(current.start)} – {_fmt(current.end)})."
                ),
                "policyLabels": [current.label],
            })

    # Evidence from each link along the lineage, oldest first.
    for (k1, k2), kind in sorted(ev.links.items(), key=lambda kv: ev.by_key[kv[0][0]].sort_date):
        if k1 in component_keys and k2 in component_keys:
            evidence.append(_link_evidence(ev.by_key[k1], ev.by_key[k2], kind))

    # Statements made by the documents themselves.
    origin_candidates: List[Tuple[date, str]] = []
    for p in (component or ([target] if target else [])):
        if p.declared_by:
            if p.start:
                origin_candidates.append((p.start, f"previous policy period stated in {ev.by_key[p.declared_by].label}"))
            continue
        if p.start and (claim_date is None or p.start <= claim_date):
            origin_candidates.append((p.start, f"start of {p.label}"))
        if p.continuous_since:
            evidence.append({
                "type": "continuous_coverage_statement",
                "description": f"{p.label} states continuous coverage since {_fmt(p.continuous_since)}.",
                "policyLabels": [p.label],
            })
            if p.start and p.continuous_since > p.start:
                conflicts.append(
                    f"{p.label} states continuous coverage since {_fmt(p.continuous_since)}, "
                    f"which is after its own start date {_fmt(p.start)}."
                )
            else:
                contradicting = [
                    g for g in gaps
                    if not g.get("coversClaimDate")
                    and p.start and parse_policy_date(g["from"]) >= p.continuous_since
                    and parse_policy_date(g["to"]) < p.start
                ]
                if contradicting:
                    g = contradicting[0]
                    conflicts.append(
                        f"{p.label} states continuous coverage since {_fmt(p.continuous_since)}, but the "
                        f"policy periods show a gap from {_fmt(parse_policy_date(g['from']))} to "
                        f"{_fmt(parse_policy_date(g['to']))}."
                    )
                else:
                    origin_candidates.append((p.continuous_since, f"continuous coverage date stated in {p.label}"))
        if p.is_renewal is True:
            evidence.append({
                "type": "renewal_statement",
                "description": f"{p.label} is described as a renewal"
                + (f": {p.renewal_info.rstrip('.')}." if p.renewal_info else ".")
                + (f" Previous policy: {p.previous_number}." if p.previous_number else ""),
                "policyLabels": [p.label],
            })
        elif p.is_renewal is False:
            evidence.append({
                "type": "fresh_policy_statement",
                "description": f"{p.label} is described as a fresh (first-year) policy"
                + (f": {p.renewal_info.rstrip('.')}." if p.renewal_info else "."),
                "policyLabels": [p.label],
            })
        elif p.previous_number:
            evidence.append({
                "type": "previous_policy_reference",
                "description": f"{p.label} references previous policy {p.previous_number}.",
                "policyLabels": [p.label],
            })
        for quote in p.quotes:
            evidence.append({"type": "document_quote", "description": quote, "policyLabels": [p.label], "quote": quote})

    original_start, original_basis = (min(origin_candidates, key=lambda c: c[0]) if origin_candidates else (None, ""))
    if continuous_from is None or (original_start and not gaps and original_start < continuous_from):
        continuous_from = original_start

    # Is there anything showing cover *before* the current policy started?
    has_history = False
    if current is not None and current.start:
        has_history = any(
            c_date < current.start for c_date, _ in origin_candidates
        ) or (current.continuous_since is not None and current.continuous_since <= current.start)
        is_fresh_origin = (
            current.is_renewal is False
            and not any(p for p in timeline if p is not current)
        )
        if is_fresh_origin:
            has_history = True
            original_start, original_basis = current.start, f"start of {current.label} (a fresh policy)"
        if not has_history:
            if current.is_renewal is True or current.previous_number:
                missing.append(
                    f"{current.label} is a renewal, but the previous policy period is not documented, "
                    "so the original start date cannot be confirmed."
                )
            else:
                missing.append(
                    f"{current.label} does not state whether it is a renewal, and no earlier policy was provided."
                )

    # ─── Status ──────────────────────────────────────────────────────────────
    if claim_date is None or target is None:
        status = STATUS_UNKNOWN
    elif conflicts:
        status = STATUS_CONFLICT
    elif gaps:
        status = STATUS_GAP
    elif not claim_covered or unverified_links or not has_history:
        status = STATUS_UNKNOWN
    else:
        status = STATUS_VERIFIED
    missing.extend(unverified_links)

    established = status in (STATUS_VERIFIED, STATUS_GAP)
    earliest_documented = min(
        (p.start for p in (component or ([target] if target else [])) if p.start),
        default=None,
    )

    policies_evaluated = _policies_evaluated(ev, target, current, component_keys, timeline, later)

    return {
        "status": status,
        "statusCode": STATUS_CODES[status],
        "summary": _summary(status, original_start, claim_date, gaps, conflicts, missing),
        # Only stated as the original start when the history supports it; the
        # earliest date the documents show is always available separately.
        "originalPolicyStartDate": _iso(original_start) if established else None,
        "originalStartDateBasis": original_basis if established else "",
        "earliestDocumentedStartDate": _iso(earliest_documented),
        "continuousCoverageFrom": _iso(continuous_from) if status in (STATUS_VERIFIED, STATUS_GAP) else None,
        "currentPolicy": _current_row(current or target),
        "claimDate": _iso(claim_date),
        "claimDateSource": claim_source,
        "coverageGaps": gaps,
        "continuityConflicts": conflicts,
        "policiesEvaluated": policies_evaluated,
        "continuityEvidence": evidence,
        "missingInformation": missing,
        "engineVersion": ENGINE_VERSION,
        "evaluatedAt": datetime.now(timezone.utc).isoformat(),
    }


def _current_row(p: Optional[_Period]) -> dict:
    if p is None:
        return {}
    return {
        "policyId": p.policy_id,
        "label": p.label,
        "policyNumber": p.number,
        "startDate": _iso(p.start),
        "endDate": _iso(p.end),
    }


def _add_gap(gaps: List[dict], frontier: date, next_start: date, p: _Period, timeline) -> None:
    gap_from = frontier + timedelta(days=1)
    gap_to = next_start - timedelta(days=1)
    days = (gap_to - gap_from).days + 1
    previous = next((q for q in reversed(timeline) if q.end == frontier), None)
    gaps.append({
        "from": _iso(gap_from),
        "to": _iso(gap_to),
        "days": days,
        "betweenPolicies": [previous.label if previous else "", p.label],
        "coversClaimDate": False,
        "withinRenewalGraceWindow": days <= RENEWAL_GRACE_DAYS,
        "description": (
            f"No cover for {days} day{'s' if days != 1 else ''} between "
            f"{previous.label + ' (ended ' + _fmt(frontier) + ')' if previous else _fmt(frontier)} "
            f"and {p.label} (started {_fmt(next_start)})."
            + (
                " This is within a typical renewal grace window; confirm with the insurer whether "
                "continuity benefits were retained."
                if days <= RENEWAL_GRACE_DAYS
                else ""
            )
        ),
    })


def _policies_evaluated(ev, target, current, component_keys, timeline, later) -> List[dict]:
    rows = []
    timeline_keys = {p.key for p in timeline}
    later_keys = {p.key for p in later}
    for p in sorted(ev.periods, key=lambda x: x.sort_date):
        if p.declared_by:
            if p.key in component_keys:
                rows.append(_period_row(p, "declared", "Stated in another policy document; not provided."))
            continue
        if current is not None and p is current:
            rows.append(_period_row(p, "current"))
        elif p.key in timeline_keys:
            rows.append(_period_row(p, "previous"))
        elif p.key in later_keys:
            rows.append(_period_row(p, "later", "Starts after the claim date."))
        elif p.key in component_keys or (target is not None and p is target):
            rows.append(_period_row(p, "current" if current is None and p is target else "linked"))
        else:
            rows.append(_period_row(
                p, "not_linked",
                "No renewal reference, matching policy number or consecutive period links it to this policy.",
            ))
    rows.extend(ev.excluded)
    return rows


def _summary(status, original_start, claim_date, gaps, conflicts, missing) -> str:
    if status == STATUS_VERIFIED:
        return (
            f"Cover has continued without interruption from {_fmt(original_start)} "
            f"to the claim date {_fmt(claim_date)}."
        )
    if status == STATUS_GAP:
        claim_gap = next((g for g in gaps if g.get("coversClaimDate")), None)
        if claim_gap:
            return "The claim date falls in a period with no cover between the evaluated policies."
        count = len(gaps)
        total = sum(g.get("days") or 0 for g in gaps)
        return (
            f"{count} coverage gap{'s' if count != 1 else ''} ({total} day{'s' if total != 1 else ''} "
            "without cover) found before the claim date."
        )
    if status == STATUS_CONFLICT:
        return "The policy documents disagree about the coverage history: " + conflicts[0]
    reason = missing[0] if missing else "The documents do not contain enough information."
    return "Continuity cannot be established from the documents provided. " + reason
