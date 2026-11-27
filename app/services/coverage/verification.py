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

  1. collects those questions so the user is asked once,
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
        **{
            field: dq[field] for field in (
                "localizedTitle", "localizedQuestion", "localizedReason", "displayOptions",
            ) if field in dq
        },
    }


def client_question(dq: dict) -> dict:
    """Expose the status per answer while keeping the decision details on the server."""
    statuses = {}
    for option in dq["options"]:
        outcome = next((o for o in dq["outcomes"] if o["option"] == option), None)
        status = (outcome or {}).get("coverageStatus")
        if is_unknown_answer(option):
            statuses[option] = NEEDS_VERIFICATION
        elif status in (*_COVERED_STATUSES, "Not Covered", NEEDS_VERIFICATION):
            statuses[option] = status
    return {**{k: v for k, v in dq.items() if k != "outcomes"}, "optionStatuses": statuses}


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
        if status not in (*_COVERED_STATUSES, "Not Covered", NEEDS_VERIFICATION):
            if dq["id"] not in unresolved:
                unresolved.append(dq["id"])
            updated.append(item)
            continue

        item["coverageStatus"] = item["status"] = status
        item["verificationResolved"] = status != NEEDS_VERIFICATION
        if item["verificationResolved"]:
            # A conditionally payable amount cannot carry over into a final decision.
            item.pop("financialDecision", None)
        for key in ("coverageStatusReason", "policyEvidence", "financialDecision"):
            if str(outcome.get(key) or "").strip():
                item[key] = outcome[key]
        if "_localizationSource" in item:
            # The selected outcome replaces these fields. Its translations
            # may only accompany those same source values.
            source = item["_localizationSource"]
            outcome_source = outcome.get("_localizationSource", {})
            for key in ("localizedStatusReason", "localizedPolicyEvidence", "localizedFinancialStatus"):
                item.pop(key, None)
                source.pop(key, None)
                if key in outcome_source:
                    item[key] = outcome[key]
                    source[key] = outcome_source[key]
            item["_localizationStatus"] = status.lower()
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
        explanation = str(item.get("policyClauseReason") or (clause or {}).get("explanation") or "").strip()
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


_REFRACTIVE_EXCLUSION = re.compile(
    r"\brefractive error\s+(?:of\s+)?(less than(?: or equal to)?|below|up to|under)\s+"
    r"(\d+(?:\.\d+)?)\s+(diopt(?:re|er)s?)\b",
    re.IGNORECASE,
)
_DIOPTRE_VALUE = re.compile(r"(\d+(?:\.\d+)?)\s+diopt(?:re|er)s?\b", re.IGNORECASE)


def _refractive_range(option: str) -> str:
    """Normalize common wording for the four sides of a cutoff."""
    wording = _DIOPTRE_VALUE.sub("cutoff", " ".join(option.lower().split()))
    ranges = {
        "less than cutoff": "below",
        "under cutoff": "below",
        "below cutoff": "below",
        "cutoff or less": "at_or_below",
        "up to cutoff": "at_or_below",
        "less than or equal to cutoff": "at_or_below",
        "more than cutoff": "above",
        "greater than cutoff": "above",
        "above cutoff": "above",
        "cutoff or more": "at_or_above",
        "at least cutoff": "at_or_above",
        "cutoff or higher": "at_or_above",
        "greater than or equal to cutoff": "at_or_above",
    }
    return ranges.get(wording, "")


def _prepare_verification(item: dict) -> dict:
    """Recover a missing question, or correct a sample cutoff using the policy quote."""
    clauses = [
        clause for clause in item.get("policyClauses") or []
        if isinstance(clause, dict) and clause.get("decisive") is True
    ]
    questions = item_questions(item)
    clause = clauses[0] if clauses else {}
    if is_needs_verification(item.get("policyClauseDecision")) and len(clauses) == 1 and clause.get("effect") == "Excludes":
        match = _REFRACTIVE_EXCLUSION.search(str(clause.get("quote") or ""))
        if match:
            operator, value, unit = match.groups()
            bound = f"{value} {unit.lower()}"
            inclusive = operator.lower() in ("less than or equal to", "up to")
            excluded = f"{bound} or less" if inclusive else f"Less than {bound}"
            other = f"More than {bound}" if inclusive else f"{bound} or more"
            wrong_question = False
            if len(questions) == 1:
                question = questions[0]
                options = question["options"]
                thresholds = set(_DIOPTRE_VALUE.findall(" ".join([question["question"], *options])))
                expected_ranges = {"at_or_below", "above"} if inclusive else {"below", "at_or_above"}
                ranges = {_refractive_range(option) for option in options if not is_unknown_answer(option)}
                wrong_question = bool(thresholds) and (thresholds != {value} or ranges != expected_ranges)
            if not questions or wrong_question:
                condition = (
                    f"The policy excludes refractive error correction at this eye power: {excluded.lower()}. "
                    f"At {other.lower()}, coverage depends on the rest of the policy terms."
                )
                reason = f"Your eye power (refractive error) is needed to apply this exclusion. {condition}"
                item.update(
                    coverageStatusReason=reason,
                    explanation=reason,
                    reason=reason,
                    verificationCondition=condition,
                    verificationClause=clause.get("section") or "",
                    decisiveQuestion={
                        "id": questions[0]["id"] if questions else "verify_refractive_error",
                        "title": "Eye power",
                        "question": f"What is the eye power (refractive error) being corrected for {item.get('item') or 'this treatment'}?",
                        "reason": reason,
                        "options": [excluded, other],
                        "outcomes": [{
                            "option": excluded,
                            "coverageStatus": "Not Covered",
                            "coverageStatusReason": f"The policy excludes refractive error correction at this eye power: {excluded.lower()}.",
                        }],
                        "localizedTitle": "கண்ணின் ஒளிவிலகல் அளவு",
                        "localizedQuestion": "சிகிச்சை அளிக்கப்படும் கண்ணின் ஒளிவிலகல் அளவு என்ன?",
                        "localizedReason": (
                            f"{value} டையாப்டர்கள்{' அல்லது அதற்குக் குறைவான' if inclusive else ' அளவுக்குக் குறைவான'} "
                            "ஒளிவிலகல் பிழைக்கான சிகிச்சையை பாலிசி விலக்குகிறது."
                        ),
                        "displayOptions": {
                            excluded: f"{value} டையாப்டர்கள்{' அல்லது அதற்குக் குறைவு' if inclusive else ' அளவுக்குக் குறைவு'}",
                            other: f"{value} டையாப்டர்கள்{' அளவுக்கு மேல்' if inclusive else ' அல்லது அதற்கு மேல்'}",
                        },
                    },
                )
                item.pop("decisiveQuestions", None)
                return item
    if not item.get("verificationCondition"):
        question_reason = questions[0]["reason"] if questions else ""
        item["verificationCondition"] = (
            item.get("policyClauseReason") or clause.get("explanation") or item.get("coverageStatusReason") or question_reason
        )
    if not item.get("verificationClause") and clause.get("section"):
        item["verificationClause"] = clause["section"]
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
                checked.append(_prepare_verification(_to_needs_verification(item, clause)))
                continue

        if is_needs_verification(status):
            checked.append(_prepare_verification(item))
            continue

        if status == "Covered":
            text = f"{item.get('coverageStatusReason') or ''} {item.get('financialDecision') or ''}"
            if _states_copay(text):
                item["consistencyCheck"] = "copay_applies"
                item["coverageStatus"] = item["status"] = "Partially Covered"

        if status in _COVERED_STATUSES and _zero_payable_on_a_cost(item):
            # "Covered" yet nothing payable: the amount is unknown, not zero.
            item["consistencyCheck"] = "zero_payable_conflict"
            item["estimatedPayableAmount"] = None
            item["estimatedPatientPayable"] = None
            note = "The amount payable depends on the final itemised bill."
            fin = str(item.get("financialDecision") or "").strip()
            item["financialDecision"] = f"{fin} {note}".strip() if note not in fin else fin

        checked.append(item)
    return checked


def _zero_payable_on_a_cost(item: dict) -> bool:
    try:
        cost = float(item.get("prescriptionCost") or 0)
        payable = item.get("estimatedPayableAmount")
        return cost > 0 and payable is not None and float(payable) == 0
    except (TypeError, ValueError):
        return False
