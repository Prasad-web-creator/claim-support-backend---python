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
    entries: List[str] = []
    for key in ("excludedDiseases", "excludedTreatments", "exclusions"):
        for item in policy_data.get(key) or []:
            text = item.get("name") or item.get("title") if isinstance(item, dict) else item
            if isinstance(text, str) and text.strip() and text.strip() not in entries:
                entries.append(text.strip())
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
    Finds the numbered policy clause stating the exclusion, e.g.
    {"section": "D.18", "title": "Maternity (Excl18)", "code": "Excl18", "wording": "..."}.
    A clause whose heading names the term is preferred over one that only mentions it.
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
        code = re.search(r"\((Excl\s*\d+)\)", title, re.IGNORECASE)
        clauses.append({
            "section": head.group(1),
            "title": title,
            "code": code.group(1) if code else "",
            "wording": wording,
        })
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
