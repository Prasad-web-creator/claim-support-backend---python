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
        visit_date = _parse_date(prescription.get("visitDate") or prescription.get("consultationDate"))

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
        visit_date = _parse_date(prescription.get("visitDate") or prescription.get("consultationDate"))
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
        
    # 7. Patient Name Match Check
    p_name = (prescription.get("patientName") or "").strip().lower()
    members = policy.get("insuredMembers") or policy.get("familyMembers") or []
    policyholder = (policy.get("policyholderName") or "").strip().lower()

    if p_name:
        matched_member = None
        for m in members:
            m_name = (m.get("name") or m.get("fullName") or "").strip().lower()
            if m_name and (m_name in p_name or p_name in m_name):
                matched_member = m
                break

        if not matched_member and policyholder and (policyholder in p_name or p_name in policyholder):
            matched_member = {"name": policyholder}

        if not matched_member and (members or policyholder):
            evaluate_rule(
                "patientNameMatch",
                False,
                f"Patient '{prescription.get('patientName')}' is not listed as an insured member on this policy.",
                blocker=True,
            )
        else:
            evaluate_rule(
                "patientNameMatch",
                True,
                f"Patient '{prescription.get('patientName')}' is an active insured member.",
            )

    # 8. Patient Age Match Check
    p_age_raw = prescription.get("patientAge")
    if p_age_raw is not None:
        try:
            import re
            p_age_digits = re.sub(r"\D", "", str(p_age_raw))
            presc_age = int(p_age_digits) if p_age_digits else None

            matched_member = None
            for m in members:
                m_name = (m.get("name") or m.get("fullName") or "").strip().lower()
                if m_name and p_name and (m_name in p_name or p_name in m_name):
                    matched_member = m
                    break
            if not matched_member and len(members) == 1:
                matched_member = members[0]

            if matched_member and matched_member.get("age") is not None:
                pol_age_digits = re.sub(r"\D", "", str(matched_member.get("age")))
                pol_age = int(pol_age_digits) if pol_age_digits else None

                if presc_age is not None and pol_age is not None and presc_age != pol_age:
                    evaluate_rule(
                        "patientAgeMatch",
                        False,
                        f"Patient age mismatch: Prescription lists {presc_age} Yrs, but policy records {pol_age} Yrs for {matched_member.get('name', 'insured member')}.",
                        blocker=True,
                    )
                else:
                    evaluate_rule(
                        "patientAgeMatch",
                        True,
                        f"Patient age ({presc_age} Yrs) matches policy records.",
                    )
        except Exception as e:
            evaluate_rule("patientAgeMatch", True, f"Could not parse age: {e}")

    # 9. Patient Gender Match Check
    p_gender = (prescription.get("patientGender") or "").strip().lower()
    if p_gender:
        matched_member = None
        for m in members:
            m_name = (m.get("name") or m.get("fullName") or "").strip().lower()
            if m_name and p_name and (m_name in p_name or p_name in m_name):
                matched_member = m
                break

        if matched_member and matched_member.get("gender"):
            pol_gender = str(matched_member.get("gender")).strip().lower()
            p_is_male = p_gender.startswith("m")
            pol_is_male = pol_gender.startswith("m")

            if p_is_male != pol_is_male:
                evaluate_rule(
                    "patientGenderMatch",
                    False,
                    f"Patient gender mismatch: Prescription lists {prescription.get('patientGender')}, but policy records {matched_member.get('gender')} for {matched_member.get('name', 'insured member')}.",
                    blocker=True,
                )
            else:
                evaluate_rule(
                    "patientGenderMatch",
                    True,
                    "Patient gender matches policy records.",
                )

    # 10. Consultation Date Check
    c_date_raw = prescription.get("consultationDate") or prescription.get("visitDate")
    if c_date_raw:
        try:
            c_date = _parse_date(c_date_raw)
            p_start = _parse_date(policy.get("policyStartDate"))
            p_end = _parse_date(policy.get("policyEndDate"))
            now = datetime.now()

            if c_date:
                if c_date > now:
                    evaluate_rule(
                        "consultationDateValid",
                        False,
                        f"Consultation date ({c_date_raw}) is in the future.",
                        blocker=True,
                    )
                elif p_start and c_date < p_start:
                    evaluate_rule(
                        "consultationDateValid",
                        False,
                        f"Consultation date ({c_date_raw}) is prior to policy start date ({policy.get('policyStartDate')}).",
                        blocker=True,
                    )
                elif p_end and c_date > p_end:
                    evaluate_rule(
                        "consultationDateValid",
                        False,
                        f"Consultation date ({c_date_raw}) is after policy expiration date ({policy.get('policyEndDate')}).",
                        blocker=True,
                    )
                else:
                    evaluate_rule(
                        "consultationDateValid",
                        True,
                        "Consultation date is within valid active policy period.",
                    )
        except Exception as e:
            evaluate_rule("consultationDateValid", True, f"Date check note: {e}")

    return results

def _parse_date(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    
    # Try dateutil if available
    try:
        from dateutil import parser
        return parser.parse(str(date_str)).replace(tzinfo=None)
    except Exception:
        pass

    # Standard formats fallback
    clean_str = str(date_str).strip()
    for fmt in (
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%m/%d/%Y",
        "%Y/%m/%d",
        "%d.%m.%Y",
        "%d-%b-%Y",
        "%d-%B-%Y",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
    ):
        try:
            return datetime.strptime(clean_str.split("T")[0] if "T" not in fmt and "T" in clean_str else clean_str, fmt)
        except Exception:
            continue
    return None
