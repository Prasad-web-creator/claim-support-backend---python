"""Normalize policy evidence saved by earlier analysis reports."""

import re
from typing import List, Optional

CLAUSE_EFFECTS = ("Excludes", "Covers", "Waiting Period", "Condition", "Limit", "Definition")
NOT_COVERED_EFFECTS = ("Excludes", "Waiting Period", "Condition", "Limit")


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
        for cl in it.get("clauses") or []:
            if not isinstance(cl, dict):
                continue
            quote = re.sub(r"\s+", " ", str(cl.get("quote") or "")).strip()
            key = _alnum(quote)
            if not quote_in_policy(quote, policy_alnum):
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
        items.append({
            "item": str(it["item"]).strip(),
            "relatedTerms": [str(t) for t in (it.get("relatedTerms") or []) if t],
            "directMention": it.get("directMention") is True,
            "decision": str(it.get("decision") or "").strip(),
            "reason": str(it.get("reason") or "").strip(),
            "clauses": clauses,
        })
    return items


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
            # The search's own decision, used by the report's consistency checks.
            card["policyClauseDecision"] = found.get("decision") or ""
    return comparison


def comparison_from_search(items: List[dict]) -> List[dict]:
    """Cards built from the search when the main analysis returned none."""
    cards = []
    for it in items:
        if it["decision"] not in ("Covered", "Not Covered", "Partially Covered", "Needs Verification"):
            continue
        card = {
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
            "policyClauseDecision": it["decision"],
        }
        if it["decision"] == "Needs Verification":
            # No decisive question here; the reason states the condition.
            card["verificationCondition"] = it["reason"]
            decisive = next((c for c in it["clauses"] if c["decisive"]), None)
            if decisive and decisive.get("section"):
                card["verificationClause"] = decisive["section"]
        cards.append(card)
    return cards
