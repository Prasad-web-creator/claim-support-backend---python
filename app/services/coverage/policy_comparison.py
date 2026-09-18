"""
Multi-Policy Comparison Summary.

Pure aggregation over already-computed per-policy results. This layer NEVER
merges clauses, limits, exclusions or calculations across policies, and never
changes an individual policy decision — it only counts and ranks what each
independent analysis already concluded.
"""

from typing import Any, List

# Buckets used for the at-a-glance comparison view.
OUTCOME_COVERED = "covered"
OUTCOME_PARTIAL = "partial"
OUTCOME_NOT_COVERED = "not_covered"
OUTCOME_REVIEW = "review"
OUTCOME_WAITING = "waiting_for_user"
OUTCOME_FAILED = "failed"
OUTCOME_PENDING = "pending"


def classify_outcome(status: str, overall_status: str) -> str:
    """
    Bucket one policy result using its own status + overallStatus.

    Reads the existing vocabulary produced by the single-policy pipeline; it
    does not re-derive or override any decision.
    """
    status = (status or "").lower()
    overall = (overall_status or "").lower()

    if status == "waiting_for_user":
        return OUTCOME_WAITING
    if status in ("failed", "invalid"):
        return OUTCOME_FAILED
    if status == "manual_review_required":
        return OUTCOME_REVIEW
    if status in ("queued", "extracting", "analyzing", "reanalyzing"):
        return OUTCOME_PENDING

    if not overall:
        return OUTCOME_REVIEW
    if overall.startswith("invalid"):
        return OUTCOME_FAILED
    if "partial" in overall:
        return OUTCOME_PARTIAL
    if "not covered" in overall or "rejected" in overall:
        return OUTCOME_NOT_COVERED
    if "covered" in overall or "approved" in overall:
        return OUTCOME_COVERED
    if "review" in overall:
        return OUTCOME_REVIEW
    return OUTCOME_REVIEW


# Ranking preference when suggesting which policy looks strongest.
_OUTCOME_RANK = {
    OUTCOME_COVERED: 0,
    OUTCOME_PARTIAL: 1,
    OUTCOME_REVIEW: 2,
    OUTCOME_WAITING: 3,
    OUTCOME_NOT_COVERED: 4,
    OUTCOME_PENDING: 5,
    OUTCOME_FAILED: 6,
}


def build_comparison_summary(entries: List[Any]) -> dict:
    """
    Aggregate per-policy outcomes into a side-by-side summary.

    `entries` are PolicyAnalysisEntry objects. Returns counts, a per-policy row
    list and — only when at least one policy is actually covered — the id of the
    best-scoring policy as a display hint.
    """
    rows = []
    counts = {
        OUTCOME_COVERED: 0,
        OUTCOME_PARTIAL: 0,
        OUTCOME_NOT_COVERED: 0,
        OUTCOME_REVIEW: 0,
        OUTCOME_WAITING: 0,
        OUTCOME_FAILED: 0,
        OUTCOME_PENDING: 0,
    }

    for entry in entries:
        outcome = classify_outcome(entry.status, entry.overall_status or "")
        counts[outcome] = counts.get(outcome, 0) + 1
        rows.append({
            "policyId": entry.policy_id,
            "policyName": entry.policy_name or "",
            "insuranceCompany": entry.insurance_company or "",
            "outcome": outcome,
            "status": entry.status,
            "overallStatus": entry.overall_status or "",
            "dominanceScore": entry.dominance_score or 0.0,
            "reportId": entry.report_id or "",
        })

    # Display hint only — never alters any individual policy decision.
    best_policy_id = ""
    rankable = [r for r in rows if r["outcome"] in (OUTCOME_COVERED, OUTCOME_PARTIAL)]
    if rankable:
        best = sorted(
            rankable,
            key=lambda r: (_OUTCOME_RANK.get(r["outcome"], 99), -float(r["dominanceScore"] or 0.0)),
        )[0]
        best_policy_id = best["policyId"]

    return {
        "totalPolicies": len(rows),
        "counts": counts,
        "bestPolicyId": best_policy_id,
        "results": rows,
    }
