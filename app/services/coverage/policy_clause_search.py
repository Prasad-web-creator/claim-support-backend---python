"""
AI Policy Clause Search.
Reads the ENTIRE policy text for the medical items being checked, finds every passage
that mentions them (or a related term), adds the general clauses that govern them when
the policy does not name them, and decides coverage from those passages.

Every quote the AI returns is checked against the policy text; a quote that is not in
the policy is dropped, so only the policy's own wording is ever shown.
"""

import json
import re
from datetime import datetime
from typing import Any, List, Optional

from app.core.logging import logger
from app.services.llm.ai_client import extract_json_with_retry

# Large enough for a full policy wording with annexures.
MAX_POLICY_CHARS = 200_000

CLAUSE_EFFECTS = ("Excludes", "Covers", "Waiting Period", "Condition", "Limit", "Definition")
NOT_COVERED_EFFECTS = ("Excludes", "Waiting Period", "Condition", "Limit")

POLICY_CLAUSE_SEARCH_PROMPT = """You are an insurance policy wording analyst. You receive the FULL text of one health
insurance policy and the medical items a person wants to check. Find, in the policy text, every passage that
decides whether each item is covered, and explain the decision using only those passages.

Return ONLY valid JSON. No markdown, no text outside the object.

STEP 1 - ITEMS
Identify the medical items to check from CLAIM DETAILS: the condition / diagnosis, procedures or surgeries,
and tests. Never create an item for a medicine. Use the person's own wording as the item name. If the text names
one condition, return exactly one item.

STEP 2 - RELATED TERMS
For each item, list the words the policy may use for it: synonyms, medical and common names, abbreviations,
the treatment category and the broader benefit category.
  Maternity -> pregnancy, childbirth, delivery, caesarean section, newborn, obstetric, miscarriage, termination of pregnancy
  Influenza -> flu, viral fever, infection, communicable disease, illness

STEP 3 - DIRECT MENTIONS
Read the ENTIRE policy text: schedule tables, benefits, waiting periods, exclusions, definitions, annexures and
lists. Collect EVERY passage that mentions the item or a related term, including table rows (for example
"Maternity / Newborn Not covered under this plan") and numbered clauses (for example
"D.18 Maternity (Excl18). Medical treatment expenses traceable to childbirth ..."). Do not stop at the first
match. directMention = true when at least one passage names the item or a related term.

STEP 4 - GOVERNING CLAUSES
When the item is NOT excluded by a passage from STEP 3, add EVERY one of these that exists in the policy:
  a) the benefit that would pay for it (for example in-patient hospitalisation, day care treatment)
  b) the definition of hospitalisation, including its minimum admission period (for example 24 hours)
  c) the definition the item falls under (for example illness, injury)
  d) each waiting period that could apply (initial 30 days, specified diseases / procedures, pre-existing diseases)
  e) the out-patient / OPD exclusion, and any other exclusion that could apply to it
When the item IS excluded by a STEP 3 passage, add only the governing clauses that matter to the decision.
When the policy does not name the item, these clauses are the whole basis of the decision, and the reason MUST
state the conditions they set (for example "covered only when admitted to hospital for at least 24 hours;
out-patient treatment is excluded").

STEP 5 - EXACT QUOTES
Every "quote" MUST be copied EXACTLY from the policy text, word for word. A numbered clause is quoted from its
start, including its number, heading and code (for example "D.18 Maternity (Excl18). Medical treatment expenses
..."), not from the middle. For a table row, write its cells in order separated by a single space. Never
paraphrase, never shorten with "...", never join passages from different places. Keep each quote to the passage
that matters (at most about 600 characters). If you cannot quote it exactly, leave it out.

STEP 6 - DECISION FOR EACH ITEM
Use the dates in CLAIM DETAILS.
  - A permanent exclusion that names the item -> "Not Covered", however long the cover has run, unless the item
    is a stated exception to that exclusion (for example ectopic pregnancy under a maternity exclusion).
  - A schedule row saying the item is not covered under the plan -> "Not Covered".
  - Otherwise decide from the governing benefit and its conditions, and check every applicable waiting period
    against the EVALUATION DATE counted from the WAITING PERIOD START. A waiting period that is not complete ->
    "Not Covered", and state the date from which the item becomes covered. A completed waiting period is
    reported as satisfied, never as a reason for refusal.
  - When cover depends on a condition (for example admission for 24 hours or more), state that condition in the
    reason.
  decision is exactly one of "Covered", "Not Covered", "Partially Covered".
  reason is plain, friendly English that names the clauses relied on.

For each clause:
  section     - the clause number and heading, or the table name, as written in the policy
                (for example "D.18 Maternity (Excl18)" or "Waiting Periods")
  quote       - the exact policy text (STEP 5)
  effect      - one of "Excludes", "Covers", "Waiting Period", "Condition", "Limit", "Definition"
  explanation - plain English: how this passage applies to the item on the evaluation date
  decisive    - true when this passage decides the outcome
List direct mentions first, then decisive clauses, then the rest.

OUTPUT
{
  "items": [
    {
      "item": "",
      "relatedTerms": [],
      "directMention": true,
      "decision": "",
      "reason": "",
      "clauses": [
        {"section": "", "quote": "", "effect": "", "explanation": "", "decisive": true}
      ]
    }
  ]
}"""


def _names(values: Any) -> List[str]:
    names = []
    for v in values or []:
        name = v.get("name") if isinstance(v, dict) else v
        if isinstance(name, str) and name.strip():
            names.append(name.strip())
    return names


def _claim_details(prescription_json: dict, policy_json: dict, is_manual: bool) -> str:
    rx = prescription_json or {}
    pol = policy_json or {}
    today = datetime.now().strftime("%Y-%m-%d")
    lines = []
    if is_manual:
        lines += [
            "SOURCE: Self-entered text. The person is checking, before going to hospital, whether this would be "
            "covered if they were treated for it today. There is no patient name, doctor or admission date.",
            f"EVALUATION DATE: {today} (today)",
            f"TEXT ENTERED: {rx.get('manualText') or rx.get('diagnosis') or ''}",
        ]
    else:
        lines += [
            "SOURCE: Uploaded prescription / medical document.",
            f"EVALUATION DATE: {rx.get('admissionDate') or rx.get('visitDate') or rx.get('consultationDate') or rx.get('treatmentDate') or today}",
            f"PATIENT: {rx.get('patientName') or 'not stated'}; age {rx.get('patientAge') or rx.get('age') or 'not stated'}; "
            f"gender {rx.get('patientGender') or rx.get('gender') or 'not stated'}",
            f"ADMISSION: {rx.get('admission') or rx.get('admissionDate') or 'not stated'}; "
            f"DISCHARGE: {rx.get('discharge') or 'not stated'}",
        ]
    lines += [
        f"DIAGNOSIS: {rx.get('diagnosis') or 'not stated'}",
        f"SYMPTOMS: {', '.join(str(s) for s in (rx.get('symptoms') or [])) or 'none'}",
        f"PROCEDURES: {', '.join(_names(rx.get('procedures'))) or 'none'}",
        f"TESTS: {', '.join(_names(rx.get('medicalTests'))) or 'none'}",
    ]
    hosp = rx.get("hospitalizationRequired")
    if rx.get("admissionAssumed"):
        lines.append("HOSPITALISATION: evaluate as if admitted to hospital (in-patient or day care).")
    elif hosp is True:
        lines.append("HOSPITALISATION: admitted to hospital.")
    elif hosp is False:
        lines.append("HOSPITALISATION: out-patient only, no admission.")
    if rx.get("isPreExisting") is True:
        lines.append("PRE-EXISTING: yes, the condition existed before cover started.")
    elif rx.get("isPreExisting") is False:
        lines.append("PRE-EXISTING: no, this is a new condition.")
    lines += [
        f"POLICY PERIOD: {pol.get('policyStartDate') or 'not stated'} to {pol.get('policyEndDate') or 'not stated'}",
        f"WAITING PERIOD START: {pol.get('waitingPeriodStartDate') or pol.get('policyStartDate') or 'not stated'}"
        + (f" ({pol['waitingPeriodBasis']})" if pol.get("waitingPeriodBasis") else ""),
    ]
    return "\n".join(lines)


def _alnum(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text).lower())


def quote_in_policy(quote: str, policy_alnum: str) -> bool:
    """Exact-wording check that ignores spacing, line breaks, punctuation and table borders."""
    q = _alnum(quote)
    return len(q) >= 8 and q in policy_alnum


def _clean_result(raw: dict, policy_text: str) -> List[dict]:
    policy_alnum = _alnum(policy_text)
    items = []
    for it in (raw or {}).get("items") or []:
        if not isinstance(it, dict) or not str(it.get("item") or "").strip():
            continue
        clauses, seen = [], set()
        dropped = 0
        for cl in it.get("clauses") or []:
            if not isinstance(cl, dict):
                continue
            quote = re.sub(r"\s+", " ", str(cl.get("quote") or "")).strip()
            key = _alnum(quote)
            if not quote_in_policy(quote, policy_alnum):
                dropped += 1
                continue
            if key in seen:
                continue
            seen.add(key)
            effect = str(cl.get("effect") or "").strip().title()
            clauses.append({
                "section": str(cl.get("section") or "").strip(),
                "quote": quote,
                "effect": effect if effect in CLAUSE_EFFECTS else "Condition",
                "explanation": str(cl.get("explanation") or "").strip(),
                "decisive": cl.get("decisive") is True,
            })
        if dropped:
            logger.info(f"[Policy Clause Search] Dropped {dropped} quote(s) for '{it.get('item')}' not found in the policy text.")
        items.append({
            "item": str(it["item"]).strip(),
            "relatedTerms": [str(t) for t in (it.get("relatedTerms") or []) if t],
            "directMention": it.get("directMention") is True,
            "decision": str(it.get("decision") or "").strip(),
            "reason": str(it.get("reason") or "").strip(),
            "clauses": clauses,
        })
    return items


async def search_policy_clauses(
    policy_text: str,
    prescription_json: dict,
    policy_json: dict,
    is_manual: bool,
) -> List[dict]:
    """Returns one entry per checked item with its decision and verified policy clauses. Never raises."""
    if not policy_text or not prescription_json:
        return []
    user_content = (
        f"CLAIM DETAILS\n{_claim_details(prescription_json, policy_json, is_manual)}\n\n"
        f"==================================================\n\n"
        f"FULL POLICY TEXT\n{policy_text[:MAX_POLICY_CHARS]}"
    )
    try:
        result = await extract_json_with_retry(
            system_prompt=POLICY_CLAUSE_SEARCH_PROMPT,
            user_content=user_content,
            max_tokens=8192,
            operation_name="Policy Clause Search",
        )
        items = _clean_result(result.get("extractedJson") or {}, policy_text)
        logger.info(
            f"[Policy Clause Search] {len(items)} item(s): "
            + "; ".join(f"{i['item']} -> {i['decision']} ({len(i['clauses'])} clauses)" for i in items)
        )
        return items
    except Exception as e:
        logger.warning(f"[Policy Clause Search] Failed: {e}")
        return []


def format_for_prompt(items: List[dict]) -> str:
    """The search result as an evidence block for the main coverage analysis."""
    if not items:
        return "No clause search result available."
    return json.dumps(items, indent=2, ensure_ascii=False)


def _norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", str(name).lower()).strip()


def match_search_item(item_name: str, items: List[dict], only_one_card: bool = False) -> Optional[dict]:
    """The search entry for a comparison card, matched by name."""
    target = _norm_name(item_name)
    if not target:
        return None
    for it in items:
        if _norm_name(it["item"]) == target:
            return it
    for it in items:
        name = _norm_name(it["item"])
        if name and (name in target or target in name):
            return it
    if only_one_card and len(items) == 1:
        return items[0]
    return None


def attach_policy_clauses(comparison: List[dict], items: List[dict]) -> List[dict]:
    """Adds `policyClauses` (and the related terms used) to each card that has a search result."""
    if not items:
        return comparison
    for card in comparison:
        found = match_search_item(card.get("item") or card.get("name") or "", items, only_one_card=len(comparison) == 1)
        if found:
            card["policyClauses"] = found["clauses"]
            card["relatedTerms"] = found["relatedTerms"]
            card["directPolicyMention"] = found["directMention"]
    return comparison


def comparison_from_search(items: List[dict]) -> List[dict]:
    """Cards built from the search when the main analysis returned none."""
    cards = []
    for it in items:
        if it["decision"] not in ("Covered", "Not Covered", "Partially Covered"):
            continue
        cards.append({
            "item": it["item"],
            "itemType": "Diagnosis",
            "coverageStatus": it["decision"],
            "coverageStatusReason": it["reason"],
            "explanation": it["reason"],
            "policyEvidence": "; ".join(
                f"{c['section']}: \"{c['quote']}\"" for c in it["clauses"] if c["decisive"]
            ),
            "policyClauses": it["clauses"],
            "relatedTerms": it["relatedTerms"],
            "directPolicyMention": it["directMention"],
        })
    return cards
