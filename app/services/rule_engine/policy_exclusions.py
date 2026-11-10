"""
Policy Exclusion Matching.
Matches the claimed condition against the exclusions extracted from the customer's
own policy (excludedDiseases / excludedTreatments / exclusions) and locates the
policy clause that states the exclusion, so the decision can quote the policy wording.
"""

import re
from typing import Any, Dict, Iterable, List, Optional

# Words too generic to identify an exclusion on their own.
GENERIC_TERMS = {
    "treatment", "treatments", "surgery", "surgeries", "expenses", "disease", "diseases",
    "disorder", "disorders", "care", "therapy", "illness", "condition", "procedure",
}

# A numbered clause heading such as "D.18 Maternity (Excl18). <wording>".
_CLAUSE_HEAD = re.compile(r"(?m)^[ \t]*([A-Z]{1,2}\.\d+(?:\.\d+)*)[ \t]+([^\n.]{2,90}?)\.[ \t]")

MAX_CLAUSE_CHARS = 600

# Patterns that indicate a time-bounded waiting-period exclusion (Section D.2 / Excl02 style).
# Entries containing these phrases are NOT permanent exclusions — they are already evaluated
# by the WaitingPeriodEvaluator and must not be re-classified as permanently excluded.
_WAITING_PERIOD_ENTRY_PATTERNS = [
    # "during first 24 months", "during the first 2 years", "within first 24 months"
    r"\bduring\s+(?:the\s+)?first\s+\d+\s+(?:months?|years?)\b",
    # "first 24 months", "first 2 years" (without explicit "during")
    r"\bfirst\s+\d+\s+(?:months?|years?)\b",
    # "within \d months/years"
    r"\bwithin\s+\d+\s+(?:months?|years?)\b",
    # "for \d months/years", "for the first …"
    r"\bfor\s+(?:the\s+)?first\s+\d+\s+(?:months?|years?)\b",
    # "initial \d months/years waiting", "initial waiting period"
    r"\binitial\s+\d+\s+(?:months?|years?)\b",
    # "waiting period" spelled out
    r"\bwaiting\s+period\b",
    # "excluded for … months/years" or "excluded until …"
    r"\bexcluded\s+(?:for|until)\b",
    # "first-year exclusion", "two-year exclusion"
    r"\b(?:one|two|three|four|five|\d+)[- ]year\s+exclusion\b",
]

_WAITING_PERIOD_ENTRY_RE = re.compile(
    "|".join(_WAITING_PERIOD_ENTRY_PATTERNS),
    re.IGNORECASE,
)

# Canonical specified disease waiting-period keywords (Section D.2 / Excl02)
# Any exclusion entry or claimed condition naming these is subject to a 24-month
# waiting period, NOT a permanent exclusion, even if extracted under "Section D: Exclusions".
SPECIFIED_DISEASE_WAITING_TERMS = {
    "cataract", "phacoemulsification", "intraocular lens", "iol",
    "sinusitis", "deviated nasal septum", "septoplasty", "fess", "sinus", "dns",
    "benign prostatic hypertrophy", "benign prostate hypertrophy", "prostatomegaly", "turp", "prostate",
    "hernia", "hydrocele", "varicocele", "herniorrhaphy", "hernioplasty",
    "fissure", "fistula", "haemorrhoids", "hemorrhoids", "piles", "pilonidal sinus", "fistulectomy", "hemorrhoidectomy",
    "cholelithiasis", "gallstone", "gallbladder stone", "kidney stone", "renal calculi", "ureteric calculus",
    "urinary calculi", "nephrolithiasis", "calculus diseases", "cholecystectomy", "lithotripsy",
    "tonsil", "tonsils", "tonsillitis", "tonsillectomy", "adenoid", "adenoids", "adenoidectomy",
    "joint replacement", "knee replacement", "hip replacement", "arthroplasty", "tkr", "thr", "osteoarthritis",
    "spondylosis", "spondylitis", "intervertebral disc", "disc prolapse", "disc herniation", "slip disc", "sciatica", "radiculopathy",
    "varicose", "varicose veins", "vein stripping", "endovenous laser", "sclerotherapy",
    "hysterectomy", "fibroid", "uterine fibroid", "myomectomy", "endometriosis", "ovarian cyst", "adenomyosis",
    "lipoma", "sebaceous cyst", "ganglion", "dermoid", "benign lump", "benign cyst",
    "mastoidectomy", "tympanoplasty",
}

WAITING_PERIOD_CLAUSE_CODES = {"EXCL01", "EXCL02", "EXCL03", "EXCL1", "EXCL2", "EXCL3"}


def is_waiting_period_entry(entry: str) -> bool:
    """
    Returns True when an exclusion entry from the policy JSON is a time-bounded
    waiting-period restriction (e.g. Section D.2 / Excl02 — Specified Disease /
    Procedure Waiting Period) rather than a permanent exclusion.

    Matches either:
      1. Temporal qualifiers like "during first 24 months", "first 2 years", "waiting period".
      2. Known Section D.2 / Excl02 specified disease conditions (e.g., Sinusitis, Cataract,
         Hernia, BPH, Stones, Piles, DNS, Tonsils) that are frequently placed under
         "Section D: Exclusions" in policy schedules but are only excluded for a waiting period.

    They are evaluated by the WaitingPeriodEvaluator and MUST NOT be
    reclassified as permanent exclusions by the ExclusionEvaluator.
    """
    if _WAITING_PERIOD_ENTRY_RE.search(entry):
        return True
    entry_lower = entry.lower()
    for term in SPECIFIED_DISEASE_WAITING_TERMS:
        if _has_phrase(term, entry_lower):
            return True
    return False


def is_waiting_period_clause(clause: Dict[str, str]) -> bool:
    """True if the clause is a waiting period clause (D.1, D.2, D.3, Excl01/02/03) rather than permanent."""
    code = (clause.get("code") or "").upper().replace(" ", "")
    title = (clause.get("title") or "").lower()
    section = (clause.get("section") or "").upper()
    if code in WAITING_PERIOD_CLAUSE_CODES:
        return True
    if any(k in title for k in ("waiting period", "pre-existing", "specified disease")):
        return True
    if section in ("D.1", "D.2", "D.3"):
        return True
    return False


def _has_phrase(phrase: str, text: str) -> bool:
    return bool(re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text))


def _entry_terms(entry: str) -> List[str]:
    """'Maternity (except ectopic pregnancy)' -> ['maternity']; 'Maternity / Newborn' -> ['maternity', 'newborn']."""
    core = re.sub(r"\([^)]*\)", " ", entry)
    terms = [re.sub(r"\s+", " ", t).strip().lower() for t in re.split(r"[/,;]", core)]
    return [t for t in terms if len(t) >= 4 and t not in GENERIC_TERMS]


def claim_texts(prescription_data: Dict[str, Any]) -> List[str]:
    """The clinical wording that names what is being claimed (not symptoms)."""
    texts: List[str] = []
    for key in ("diagnosis", "manualText", "orderTitle", "procedure"):
        val = prescription_data.get(key)
        if isinstance(val, str) and val.strip():
            texts.append(val.strip())
    for proc in prescription_data.get("procedures") or []:
        name = proc.get("name") if isinstance(proc, dict) else proc
        if isinstance(name, str) and name.strip():
            texts.append(name.strip())
    return texts


def policy_exclusion_entries(policy_data: Dict[str, Any]) -> List[str]:
    """
    Collects the list of permanently excluded diseases/treatments from the policy.

    Entries that carry temporal qualifiers (e.g. "during first 24 months",
    "waiting period") are time-bounded Section D.2 / Excl02 restrictions, not
    permanent exclusions.  They are already evaluated by the WaitingPeriodEvaluator
    and are therefore excluded from this list so that they are never mis-classified
    as permanent policy exclusions.
    """
    entries: List[str] = []
    for key in ("excludedDiseases", "excludedTreatments", "exclusions"):
        for item in policy_data.get(key) or []:
            text = item.get("name") or item.get("title") if isinstance(item, dict) else item
            if not isinstance(text, str) or not text.strip():
                continue
            text = text.strip()
            # Skip time-bounded waiting-period entries; they are not permanent exclusions.
            if is_waiting_period_entry(text):
                continue
            if text not in entries:
                entries.append(text)
    return entries


def match_policy_exclusion(texts: Iterable[str], entries: Iterable[str]) -> Optional[Dict[str, str]]:
    """
    Returns {"entry", "term", "claimText"} for the first policy exclusion naming the
    claimed condition. Text in brackets (e.g. "except ectopic pregnancy") is an
    exception to the exclusion, so it never counts as a match.
    """
    texts = [t for t in texts if t]
    for entry in entries:
        core = re.sub(r"\([^)]*\)", " ", entry).lower()
        for text in texts:
            text_lower = text.lower()
            for term in _entry_terms(entry):
                if _has_phrase(term, text_lower):
                    return {"entry": entry, "term": term, "claimText": text}
            phrase = re.sub(r"\s+", " ", text_lower).strip()
            if len(phrase) >= 4 and phrase not in GENERIC_TERMS and _has_phrase(phrase, core):
                return {"entry": entry, "term": phrase, "claimText": text}
    return None


def find_exclusion_clause(policy_text: Optional[str], term: str) -> Optional[Dict[str, str]]:
    """
    Finds the numbered policy clause stating a permanent exclusion, e.g.
    {"section": "D.18", "title": "Maternity (Excl18)", "code": "Excl18", "wording": "..."}.
    A clause whose heading names the term is preferred over one that only mentions it.

    Clauses that establish time-bound waiting periods (such as D.1 Pre-existing diseases / Excl01,
    D.2 Specified disease waiting period / Excl02, D.3 30-day waiting period / Excl03) are skipped
    because they are evaluated by the WaitingPeriodEvaluator, never as permanent exclusions.
    """
    if not policy_text or not term:
        return None
    heads = list(_CLAUSE_HEAD.finditer(policy_text))
    clauses = []
    for i, head in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(policy_text)
        wording = re.sub(r"\s+", " ", policy_text[head.end():end]).strip()
        if len(wording) > MAX_CLAUSE_CHARS:
            wording = wording[:MAX_CLAUSE_CHARS].rsplit(" ", 1)[0] + "..."
        title = re.sub(r"\s+", " ", head.group(2)).strip()
        code = re.search(r"\((?:Code\s*[-–]\s*)?(Excl\s*\d+)\)", title, re.IGNORECASE)
        clause_obj = {
            "section": head.group(1),
            "title": title,
            "code": code.group(1) if code else "",
            "wording": wording,
        }
        # Skip waiting-period clauses (D.1, D.2, D.3 / Excl01, Excl02, Excl03)
        if not is_waiting_period_clause(clause_obj):
            clauses.append(clause_obj)

    term = term.lower()
    for clause in clauses:
        if _has_phrase(term, clause["title"].lower()):
            return clause
    for clause in clauses:
        if clause["code"] and _has_phrase(term, clause["wording"].lower()):
            return clause
    return None


def find_schedule_not_covered(policy_text: Optional[str], term: str) -> Optional[str]:
    """A schedule row such as 'Maternity / Newborn' followed by 'Not covered under this plan'."""
    if not policy_text or not term:
        return None
    pattern = re.compile(
        rf"(?im)^[ \t]*([^\n]{{0,60}}(?<!\w){re.escape(term)}(?!\w)[^\n]{{0,60}}?)[ \t]*\n[ \t]*(not covered[^\n]*)"
    )
    m = pattern.search(policy_text)
    if not m:
        return None
    return f"{m.group(1).strip()}: {m.group(2).strip().rstrip('.')}"
