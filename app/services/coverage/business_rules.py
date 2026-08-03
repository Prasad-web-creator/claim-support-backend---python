"""
Business Rule Engine — migrated from BusinessRuleEngine.js.
Executes deterministic business rules on extracted JSON data.
"""

from datetime import datetime


def enforce_business_rules(policy: dict, prescription: dict) -> dict:
    """
    Executes a suite of deterministic business rules against the extracted data.

    Returns:
        {
            "rules": dict,
            "overallEligible": bool,
            "blockers": list[str],
            "warnings": list[str]
        }
    """
    results = {
        "rules": {},
        "overallEligible": True,
        "blockers": [],
        "warnings": [],
    }

    if not policy or not prescription:
        results["overallEligible"] = False
        results["blockers"].append("Missing policy or prescription data")
        return results

    # Helper function to evaluate and log a rule
    def evaluate_rule(name: str, passed: bool, reason: str, blocker: bool = False):
        results["rules"][name] = {"passed": passed, "reason": reason}
        if not passed:
            if blocker:
                results["overallEligible"] = False
                results["blockers"].append(reason)
            else:
                results["warnings"].append(reason)

    # 1. Policy Expiry Check
    try:
        policy_end = _parse_date(policy.get("policyEndDate"))
        visit_date = _parse_date(prescription.get("visitDate"))

        if policy_end and visit_date:
            passed = visit_date <= policy_end
            evaluate_rule(
                "policyActive",
                passed,
                "Policy active during visit" if passed else "Policy expired before visit date",
                blocker=True,
            )
        else:
            evaluate_rule(
                "policyActive",
                False,
                "Missing policy end date or visit date for active check",
                blocker=True,
            )
    except Exception as e:
        evaluate_rule("policyActive", False, f"Date parse error: {e}", blocker=True)

    # 2. Waiting Period Check
    try:
        policy_start = _parse_date(policy.get("policyStartDate"))
        visit_date = _parse_date(prescription.get("visitDate"))
        waiting_days = policy.get("waitingPeriodDays")

        if policy_start and visit_date and waiting_days is not None:
            # waiting_days is assumed to be an integer (e.g. 30, 90, 1440)
            days_active = (visit_date - policy_start).days
            passed = days_active >= int(waiting_days)
            evaluate_rule(
                "waitingPeriodCompleted",
                passed,
                f"Active for {days_active} days (Required: {waiting_days})" if passed else f"Waiting period not met. Active for {days_active} days (Required: {waiting_days})",
                blocker=True,
            )
        else:
            evaluate_rule(
                "waitingPeriodCompleted",
                False,
                "Could not verify waiting period due to missing dates or waiting period value",
                blocker=False, # Treat as warning instead of blocker if unknown
            )
    except Exception as e:
        evaluate_rule("waitingPeriodCompleted", False, f"Waiting period check error: {e}")

    # 3. Diagnosis Covered Check
    diagnosis = (prescription.get("diagnosis") or "").lower()
    covered_diseases = [d.lower() for d in (policy.get("coveredDiseases") or []) if d]
    
    if diagnosis and covered_diseases:
        # Simple substring matching
        passed = any(d in diagnosis or diagnosis in d for d in covered_diseases)
        if passed:
             evaluate_rule("diagnosisCovered", True, f"Diagnosis '{prescription.get('diagnosis')}' found in covered list")
        else:
             evaluate_rule("diagnosisCovered", False, f"Diagnosis '{prescription.get('diagnosis')}' not explicitly found in covered list", blocker=False)
    else:
        evaluate_rule("diagnosisCovered", False, "Missing diagnosis or covered diseases list", blocker=False)

    # 4. Diagnosis Excluded Check
    excluded_diseases = [d.lower() for d in (policy.get("excludedDiseases") or []) if d]
    if diagnosis and excluded_diseases:
        failed = any(d in diagnosis or diagnosis in d for d in excluded_diseases)
        if failed:
             evaluate_rule("diagnosisExcluded", False, f"Diagnosis '{prescription.get('diagnosis')}' is explicitly excluded", blocker=True)
        else:
             evaluate_rule("diagnosisExcluded", True, "Diagnosis is not in the excluded list")
    else:
         evaluate_rule("diagnosisExcluded", True, "No excluded diseases list to check against")

    # 5. Hospitalization Check
    hosp_req = prescription.get("hospitalizationRequired")
    hosp_cov_raw = policy.get("hospitalization")
    hosp_cov = (hosp_cov_raw or "").lower()

    if hosp_req is True:
        if hosp_cov_raw is None:
             evaluate_rule("hospitalizationCovered", False, "Policy does not cover hospitalization (Outpatient Policy)", blocker=True)
        elif "covered" in hosp_cov or "yes" in hosp_cov:
            evaluate_rule("hospitalizationCovered", True, "Hospitalization is covered")
        elif "not covered" in hosp_cov or "no" in hosp_cov:
             evaluate_rule("hospitalizationCovered", False, "Policy explicitly does not cover hospitalization", blocker=True)
        else:
             evaluate_rule("hospitalizationCovered", False, "Unclear if hospitalization is covered", blocker=False)
    else:
        evaluate_rule("hospitalizationCovered", True, "Hospitalization not required")
        
    # 6. Coverage Amount Check
    est_cost = prescription.get("estimatedCost")
    cov_amt = policy.get("coverageAmount")
    
    if est_cost is not None and cov_amt is not None:
        try:
             est = float(est_cost)
             cov = float(cov_amt)
             if est > cov:
                 evaluate_rule("coverageAmountSufficient", False, f"Estimated cost ({est}) exceeds coverage amount ({cov})", blocker=False) # Not a strict blocker, user pays diff
             else:
                 evaluate_rule("coverageAmountSufficient", True, "Estimated cost within coverage limits")
        except ValueError:
             evaluate_rule("coverageAmountSufficient", False, "Could not parse cost or coverage amounts", blocker=False)
    else:
         evaluate_rule("coverageAmountSufficient", False, "Missing estimated cost or coverage amount", blocker=False)

    return results

def _parse_date(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    
    from dateutil import parser
    try:
        # Ignore timezone info for basic comparisons
        return parser.parse(date_str).replace(tzinfo=None)
    except Exception:
        return None
