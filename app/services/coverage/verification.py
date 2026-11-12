"""
"Needs Verification": coverage that depends on one fact the documents do not give.

Some policy clauses decide coverage from a fact that a typed diagnosis or a
prescription rarely states, for example:

  • a number   - refractive error >= 7.5 dioptres (LASIK), BMI >= 40 (bariatric surgery)
  • a cause    - accident vs illness (dental treatment, plastic surgery)
  • a purpose  - cosmetic vs reconstructive
  • a setting  - admitted for 24 hours or more

For such an item the coverage analysis returns the status "Needs Verification",
the condition in plain words, and ONE decisive question with the outcome for
every answer. This module:

  1. collects those questions so the user is asked once (at most 3 questions),
  2. applies the user's answers using the outcomes returned with the question,
     so no second AI call is needed,
  3. runs consistency checks on every item so a self-contradicting result
     (an exclusion that decides the item, yet the status says Covered) is never
     shown as Covered.

Everything here is deterministic and never raises on malformed AI output.
"""

import re
from typing import Any, Dict, List, Optional, Tuple

NEEDS_VERIFICATION = "Needs Verification"

# Questions asked in one round, at most.
MAX_DECISIVE_QUESTIONS = 5

# Decisive question ids are prefixed so they never collide with the
# clarification ids the resume flow parses (hospitalization_status, ...).
DECISIVE_ID_PREFIX = "verify_"

DONT_KNOW_OPTION = "Don't know"

_UNKNOWN_TERMS = (
    "don't know", "dont know", "do not know", "don’t know", "not sure", "unsure",
    "no idea", "not known", "unknown", "skip", "can't say", "cannot say",
)

_COVERED_STATUSES = ("Covered", "Partially Covered")


def is_needs_verification(status: Any) -> bool:
    return "verif" in str(status or "").strip().lower()


def is_unknown_answer(answer: Any) -> bool:
    text = str(answer or "").strip().lower()
    return not text or any(term in text for term in _UNKNOWN_TERMS)


def _norm(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def _slug(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")[:48]


# ──────────────────────────────────────────────────────────────────────────────
# Decisive questions
# ──────────────────────────────────────────────────────────────────────────────

def _outcomes(dq: dict) -> List[dict]:
    """Outcomes as a list of {"option", "coverageStatus", ...}, from a list or an option-keyed dict."""
    raw = dq.get("outcomes")
    result = []
    if isinstance(raw, list):
        for o in raw:
            if isinstance(o, dict) and str(o.get("option") or o.get("answer") or "").strip():
                o = dict(o)
                o["option"] = str(o.get("option") or o.get("answer")).strip()
                result.append(o)
    elif isinstance(raw, dict):
        for option, o in raw.items():
            if isinstance(o, dict) and str(option).strip():
                o = dict(o)
                o["option"] = str(option).strip()
                result.append(o)
    return result


def normalize_decisive_question(dq: Any, item_name: str = "") -> Optional[dict]:
    """A clean decisive question, or None when the AI output is not usable."""
    if not isinstance(dq, dict):
        return None
    question = str(dq.get("question") or "").strip()
    options = [str(o).strip() for o in (dq.get("options") or []) if str(o or "").strip()]
    outcomes = _outcomes(dq)
    if not options and outcomes:
        options = [o["option"] for o in outcomes]
    if not question or len(options) < 2:
        return None

    if not any(is_unknown_answer(o) for o in options):
        options.append(DONT_KNOW_OPTION)

    raw_id = str(dq.get("id") or "").strip() or f"{item_name}_{question}"
    qid = raw_id if raw_id.startswith(DECISIVE_ID_PREFIX) else DECISIVE_ID_PREFIX + _slug(raw_id)
    if qid == DECISIVE_ID_PREFIX:
        qid = DECISIVE_ID_PREFIX + _slug(item_name or "detail")

    return {
        "id": qid,
        "category": "verification",
        "title": str(dq.get("title") or "").strip() or "Detail Needed to Confirm Coverage",
        "question": question,
        "type": "single_choice",
        "required": True,
        "options": options,
        "reason": str(dq.get("reason") or "").strip()
        or (f"Coverage for {item_name} depends on this detail." if item_name else "Coverage depends on this detail."),
        "outcomes": outcomes,
    }


def client_question(dq: dict) -> dict:
    """The question as sent to the app (outcomes stay on the server)."""
    return {k: v for k, v in dq.items() if k != "outcomes"}


def item_questions(item: dict) -> List[dict]:
    """
    Every decisive question of an item, normalized: the single "decisiveQuestion"
    and/or the "decisiveQuestions" array (one question per missing fact).
    """
    name = str(item.get("item") or "")
    raw = []
    if isinstance(item.get("decisiveQuestions"), list):
        raw.extend(item["decisiveQuestions"])
    if item.get("decisiveQuestion"):
        raw.append(item["decisiveQuestion"])
    questions, seen = [], set()
    for dq in raw:
        norm = normalize_decisive_question(dq, name)
        if norm and norm["id"] not in seen:
            seen.add(norm["id"])
            questions.append(norm)
    return questions


def pending_decisive_questions(comparison: List[dict], asked_ids=()) -> List[dict]:
    """
    Decisive questions still to ask for the "Needs Verification" items, one per
    distinct fact, at most MAX_DECISIVE_QUESTIONS, never one already asked.
    """
    asked = set(asked_ids or ())
    questions: List[dict] = []
    seen = set()
    for item in comparison or []:
        if not is_needs_verification(item.get("coverageStatus") or item.get("status")):
            continue
        for dq in item_questions(item):
            if dq["id"] in asked or dq["id"] in seen:
                continue
            seen.add(dq["id"])
            questions.append(dq)
            if len(questions) >= MAX_DECISIVE_QUESTIONS:
                return questions
    return questions


def _match_outcome(answer: Any, dq: dict) -> Optional[dict]:
    outcomes = dq.get("outcomes") or []
    target = _norm(answer)
    if not target:
        return None
    for o in outcomes:
        if _norm(o["option"]) == target:
            return o
    for o in outcomes:
        name = _norm(o["option"])
        if name and (name in target or target in name):
            return o
    # Outcomes listed in the same order as the options, without matching labels.
    options = [_norm(o) for o in dq.get("options") or []]
    if target in options and len(outcomes) == len(options):
        return outcomes[options.index(target)]
    return None


def apply_decisive_answers(comparison: List[dict], answers: Dict[str, Any]) -> Tuple[List[dict], List[str]]:
    """
    Apply the user's answers to the "Needs Verification" items, using the outcome
    returned with each question.

    Returns (updated comparison, ids of answered questions that have no usable
    outcome). A "Don't know" answer keeps the item as Needs Verification.
    """
    updated: List[dict] = []
    unresolved: List[str] = []
    for item in comparison or []:
        item = dict(item)
        questions = item_questions(item)
        answered = [q for q in questions if q["id"] in answers]
        if not answered:
            updated.append(item)
            continue

        if len(questions) > 1:
            # Several facts decide this item: the analysis is re-run with all the
            # answers, unless every answer is "Don't know".
            item["verificationAnswers"] = {q["id"]: str(answers[q["id"]]) for q in answered}
            if all(is_unknown_answer(answers[q["id"]]) for q in answered):
                item["coverageStatus"] = item["status"] = NEEDS_VERIFICATION
            else:
                unresolved.extend(q["id"] for q in answered if q["id"] not in unresolved)
            updated.append(item)
            continue

        dq = questions[0]

        answer = answers[dq["id"]]
        item["verificationAnswer"] = str(answer)
        outcome = _match_outcome(answer, dq)

        if is_unknown_answer(answer):
            item["coverageStatus"] = item["status"] = NEEDS_VERIFICATION
            if outcome and outcome.get("coverageStatusReason"):
                item["coverageStatusReason"] = item["explanation"] = item["reason"] = outcome["coverageStatusReason"]
            updated.append(item)
            continue

        status = str((outcome or {}).get("coverageStatus") or "").strip()
        if not outcome or not status:
            if dq["id"] not in unresolved:
                unresolved.append(dq["id"])
            updated.append(item)
            continue

        item["coverageStatus"] = item["status"] = status
        item["verificationResolved"] = True
        for key in ("coverageStatusReason", "policyEvidence", "financialDecision"):
            if str(outcome.get(key) or "").strip():
                item[key] = outcome[key]
        if str(outcome.get("coverageStatusReason") or "").strip():
            item["explanation"] = item["reason"] = outcome["coverageStatusReason"]
        updated.append(item)
    return updated, unresolved


# ──────────────────────────────────────────────────────────────────────────────
# Consistency checks
# ──────────────────────────────────────────────────────────────────────────────

_COPAY_RE = re.compile(
    r"(\d{1,2}(?:\.\d+)?)\s*%\s*(?:[a-z\-]+\s+){0,2}co-?\s?pay", re.IGNORECASE
)
_COPAY_NEGATION_RE = re.compile(
    r"\b(nil|not applicable|does not apply|doesn't apply|not apply|no co-?\s?pay|waived|"
    r"only if|applies if|would apply|if the insured|aged \d+ (?:years )?or above)\b",
    re.IGNORECASE,
)


def _states_copay(text: str) -> bool:
    """True when the text says a co-payment above 0% applies to this treatment."""
    for sentence in re.split(r"(?<=[.;])\s+", text or ""):
        for m in _COPAY_RE.finditer(sentence):
            try:
                pct = float(m.group(1))
            except ValueError:
                continue
            if pct > 0 and not _COPAY_NEGATION_RE.search(sentence):
                return True
    return False


def _deciding_exclusion(item: dict) -> Optional[dict]:
    """
    The exclusion clause that, per the policy clause search, decides this item
    when the search itself did not conclude Covered.
    """
    decision = str(item.get("policyClauseDecision") or "").strip()
    if decision == "Covered":
        return None
    for cl in item.get("policyClauses") or []:
        if isinstance(cl, dict) and cl.get("effect") == "Excludes" and cl.get("decisive") is True:
            return cl
    return None


def _to_needs_verification(item: dict, clause: Optional[dict]) -> dict:
    name = str(item.get("item") or "this treatment")
    section = str((clause or {}).get("section") or item.get("verificationClause") or "").strip()
    condition = str(item.get("verificationCondition") or "").strip()
    if not condition:
        explanation = str((clause or {}).get("explanation") or "").strip()
        condition = (
            f"An exclusion in your policy{f' ({section})' if section else ''} may apply to {name}. "
            f"Coverage depends on whether this exclusion applies to your case."
        )
        if explanation:
            condition = f"{condition} {explanation}"
    reason = f"Coverage for {name} could not be confirmed. {condition}"
    item["coverageStatus"] = item["status"] = NEEDS_VERIFICATION
    item["isCovered"] = False
    item["verificationCondition"] = condition
    if section:
        item["verificationClause"] = section
    item["coverageStatusReason"] = item["explanation"] = item["reason"] = reason
    item["financialDecision"] = "The payable amount depends on the detail that needs verification."
    return item


def apply_consistency_checks(comparison: List[dict]) -> List[dict]:
    """
    Fix results that contradict their own evidence:

      • a decisive exclusion clause (and a clause search that did not conclude
        Covered), yet the item says Covered / Partially Covered
        → Needs Verification;
      • the item's reason or financial status states a co-payment above 0%,
        yet the item says Covered → Partially Covered.

    Items settled by the user's answer to a decisive question are left as they are.
    """
    checked = []
    for item in comparison or []:
        if not isinstance(item, dict):
            checked.append(item)
            continue
        item = dict(item)
        status = str(item.get("coverageStatus") or item.get("status") or "").strip()

        if status in _COVERED_STATUSES and not item.get("verificationResolved"):
            clause = _deciding_exclusion(item)
            if clause is not None or is_needs_verification(item.get("policyClauseDecision")):
                item["consistencyCheck"] = "exclusion_conflict"
                checked.append(_to_needs_verification(item, clause))
                continue

        if status == "Covered":
            text = f"{item.get('coverageStatusReason') or ''} {item.get('financialDecision') or ''}"
            if _states_copay(text):
                item["consistencyCheck"] = "copay_applies"
                item["coverageStatus"] = item["status"] = "Partially Covered"

        checked.append(item)
    return checked
