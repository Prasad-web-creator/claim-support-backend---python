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

    # Determine if self-entered prescription query
    is_self_entered = bool(
        prescription.get("isManual")
        or prescription.get("prescriptionSource") == "Self-entered Prescription"
        or prescription.get("manualText")
    )

    # 1. Policy Expiry Check
    try:
        policy_end = _parse_date(policy.get("policyEndDate"))
        visit_date = _parse_date(prescription.get("visitDate") or prescription.get("consultationDate"))

        # For self-entered queries, default visit_date to today's date if missing
        if not visit_date and is_self_entered:
            visit_date = datetime.now()

        if policy_end and visit_date:
            passed = visit_date <= policy_end
            evaluate_rule(
                "policyActive",
                passed,
                "Policy active during visit/evaluation date" if passed else "Policy expired before visit/evaluation date",
                blocker=True,
            )
        elif visit_date:
            evaluate_rule(
                "policyActive",
                True,
                "Policy end date not specified; visit date accepted",
                blocker=False,
            )
        else:
            evaluate_rule(
                "policyActive",
                True,
                "Missing visit date; assuming active current coverage query",
                blocker=False,
            )
    except Exception as e:
        evaluate_rule("policyActive", True, f"Date parse note: {e}", blocker=False)

    # 2. Waiting Period Check
    try:
        policy_start = _parse_date(policy.get("policyStartDate"))
        visit_date = _parse_date(prescription.get("visitDate") or prescription.get("consultationDate"))
        if not visit_date and is_self_entered:
            visit_date = datetime.now()
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
        
    # ===========================================================
    # STAGE 1 — MEMBER ELIGIBILITY VALIDATION
    # All rules below are BLOCKERS. If ANY fails, overallEligible
    # is set to False and coverage analysis stops.
    # ===========================================================

    # --- Shared member reference data ---
    p_name = (prescription.get("patientName") or "").strip().lower()
    members = policy.get("insuredMembers") or policy.get("familyMembers") or []
    policyholder = (
        policy.get("policyholderName")
        or policy.get("policyHolder")
        or policy.get("insuredPersonName")
        or ""
    ).strip().lower()

    # 7. Patient Name Match Check
    if p_name:
        matched_member = None
        for m in members:
            m_name = (
                m.get("name")
                or m.get("fullName")
                or m.get("memberName")
                or ""
            ).strip().lower()
            if m_name and (
                m_name in p_name
                or p_name in m_name
                or _fuzzy_name_match(p_name, m_name)
            ):
                matched_member = m
                break

        if not matched_member and policyholder and (
            policyholder in p_name
            or p_name in policyholder
            or _fuzzy_name_match(p_name, policyholder)
        ):
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

    # 11. Member ID / Policy Member Number — Exact Match
    rx_member_id = (
        prescription.get("memberId")
        or prescription.get("memberNumber")
        or prescription.get("policyMemberNumber")
        or ""
    ).strip()
    pol_member_id = (
        policy.get("memberId")
        or policy.get("memberNumber")
        or policy.get("policyMemberNumber")
        or ""
    ).strip()
    if rx_member_id and pol_member_id:
        if rx_member_id.lower() != pol_member_id.lower():
            evaluate_rule(
                "memberIdMatch",
                False,
                f"Member ID mismatch: Prescription '{rx_member_id}' vs Policy '{pol_member_id}'.",
                blocker=True,
            )
        else:
            evaluate_rule("memberIdMatch", True, f"Member ID matched: {rx_member_id}")

    # 12. Employee ID / Corporate Employee Number — Exact Match
    rx_emp_id = (
        prescription.get("employeeId")
        or prescription.get("corporateEmployeeNumber")
        or prescription.get("empId")
        or ""
    ).strip()
    pol_emp_id = (
        policy.get("employeeId")
        or policy.get("corporateEmployeeNumber")
        or policy.get("empId")
        or ""
    ).strip()
    if rx_emp_id and pol_emp_id:
        if rx_emp_id.lower() != pol_emp_id.lower():
            evaluate_rule(
                "employeeIdMatch",
                False,
                f"Employee ID mismatch: Prescription '{rx_emp_id}' vs Policy '{pol_emp_id}'.",
                blocker=True,
            )
        else:
            evaluate_rule("employeeIdMatch", True, f"Employee ID matched: {rx_emp_id}")

    # 13. Health Card / Insurance Card Number — Exact Match
    rx_card = (
        prescription.get("healthCardNumber")
        or prescription.get("insuranceCardNumber")
        or ""
    ).strip()
    pol_card = (
        policy.get("healthCardNumber")
        or policy.get("insuranceCardNumber")
        or ""
    ).strip()
    if rx_card and pol_card:
        if rx_card.lower() != pol_card.lower():
            evaluate_rule(
                "healthCardMatch",
                False,
                f"Health/Insurance card number mismatch: Prescription '{rx_card}' vs Policy '{pol_card}'.",
                blocker=True,
            )
        else:
            evaluate_rule("healthCardMatch", True, f"Health/Insurance card matched: {rx_card}")

    # 14. Group Member Number — Exact Match
    rx_group = (prescription.get("groupMemberNumber") or "").strip()
    pol_group = (policy.get("groupMemberNumber") or "").strip()
    if rx_group and pol_group:
        if rx_group.lower() != pol_group.lower():
            evaluate_rule(
                "groupMemberNumberMatch",
                False,
                f"Group member number mismatch: Prescription '{rx_group}' vs Policy '{pol_group}'.",
                blocker=True,
            )
        else:
            evaluate_rule("groupMemberNumberMatch", True, f"Group member number matched: {rx_group}")

    # 15. Aadhaar Number — Exact Match (digits only)
    rx_aadhaar = (prescription.get("aadhaarNumber") or "").strip().replace(" ", "").replace("-", "")
    pol_aadhaar = (policy.get("aadhaarNumber") or "").strip().replace(" ", "").replace("-", "")
    if rx_aadhaar and pol_aadhaar:
        if rx_aadhaar != pol_aadhaar:
            evaluate_rule(
                "aadhaarMatch",
                False,
                f"Aadhaar number mismatch between prescription and policy records.",
                blocker=True,
            )
        else:
            evaluate_rule("aadhaarMatch", True, "Aadhaar number matched.")

    # 16. Passport Number — Exact Match (case-insensitive)
    rx_passport = (prescription.get("passportNumber") or "").strip().upper().replace(" ", "")
    pol_passport = (policy.get("passportNumber") or "").strip().upper().replace(" ", "")
    if rx_passport and pol_passport:
        if rx_passport != pol_passport:
            evaluate_rule(
                "passportMatch",
                False,
                f"Passport number mismatch: Prescription '{rx_passport}' vs Policy '{pol_passport}'.",
                blocker=True,
            )
        else:
            evaluate_rule("passportMatch", True, "Passport number matched.")

    # 17. National ID — Exact Match (case-insensitive)
    rx_national_id = (
        prescription.get("nationalId")
        or prescription.get("nationalID")
        or prescription.get("panNumber")
        or ""
    ).strip().upper().replace(" ", "")
    pol_national_id = (
        policy.get("nationalId")
        or policy.get("nationalID")
        or policy.get("panNumber")
        or ""
    ).strip().upper().replace(" ", "")
    if rx_national_id and pol_national_id:
        if rx_national_id != pol_national_id:
            evaluate_rule(
                "nationalIdMatch",
                False,
                f"National ID mismatch: Prescription '{rx_national_id}' vs Policy '{pol_national_id}'.",
                blocker=True,
            )
        else:
            evaluate_rule("nationalIdMatch", True, "National ID matched.")

    # 18. Relationship Validation
    rx_relationship = (prescription.get("relationship") or "").strip().lower()
    policy_coverage_for = policy.get("coverageFor") or policy.get("insuredRelationships") or []
    if isinstance(policy_coverage_for, str):
        policy_coverage_for = [policy_coverage_for]
    policy_coverage_for_lower = [r.strip().lower() for r in policy_coverage_for if r]

    if rx_relationship and policy_coverage_for_lower:
        # Normalise common relationship aliases
        rel_aliases = {
            "self": ["self", "primary", "policyholder", "policy holder"],
            "spouse": ["spouse", "wife", "husband", "partner"],
            "son": ["son", "child", "children", "dependent", "son/daughter"],
            "daughter": ["daughter", "child", "children", "dependent", "son/daughter"],
            "mother": ["mother", "parent", "parents", "mom"],
            "father": ["father", "parent", "parents", "dad"],
        }
        rx_rel_normalised = rx_relationship
        for canonical, aliases in rel_aliases.items():
            if rx_relationship in aliases:
                rx_rel_normalised = canonical
                break

        matched_rel = any(
            rx_rel_normalised in r or r in rx_rel_normalised
            for r in policy_coverage_for_lower
        )
        if not matched_rel:
            evaluate_rule(
                "relationshipEligible",
                False,
                f"Patient relationship '{prescription.get('relationship')}' is not a covered relationship under this policy.",
                blocker=True,
            )
        else:
            evaluate_rule(
                "relationshipEligible",
                True,
                f"Patient relationship '{prescription.get('relationship')}' is covered under this policy.",
            )

    # 19. Policy Holder Coverage Check
    # Ensure prescription patient exists in the policy member list
    if p_name and (members or policyholder):
        any_member_match = False
        for m in members:
            m_name = (
                m.get("name")
                or m.get("fullName")
                or m.get("memberName")
                or ""
            ).strip().lower()
            if m_name and (
                m_name in p_name
                or p_name in m_name
                or _fuzzy_name_match(p_name, m_name)
            ):
                any_member_match = True
                break
        if not any_member_match and policyholder and (
            policyholder in p_name
            or p_name in policyholder
            or _fuzzy_name_match(p_name, policyholder)
        ):
            any_member_match = True

        # Only block if the policy has a populated member list and NO match found
        if not any_member_match:
            evaluate_rule(
                "patientInPolicyMemberList",
                False,
                f"Prescription patient '{prescription.get('patientName')}' is not listed as an insured member on this policy.",
                blocker=True,
            )
        # (Pass silently — already covered by rule 7 patientNameMatch)

    # 20. Minimum / Maximum Eligible Age Range Check
    try:
        import re as _re
        min_age = policy.get("minEligibleAge") or policy.get("minimumAge") or policy.get("minAge")
        max_age = policy.get("maxEligibleAge") or policy.get("maximumAge") or policy.get("maxAge")
        if min_age is not None or max_age is not None:
            p_age_raw2 = prescription.get("age") or prescription.get("patientAge")
            if p_age_raw2 is not None:
                p_age_digits2 = _re.sub(r"\D", "", str(p_age_raw2))
                presc_age2 = int(p_age_digits2) if p_age_digits2 else None
                if presc_age2 is not None:
                    age_ok = True
                    reason_parts = []
                    if min_age is not None and presc_age2 < int(str(min_age).strip()):
                        age_ok = False
                        reason_parts.append(f"below minimum ({min_age})")
                    if max_age is not None and presc_age2 > int(str(max_age).strip()):
                        age_ok = False
                        reason_parts.append(f"above maximum ({max_age})")
                    if age_ok:
                        evaluate_rule(
                            "ageRangeEligible",
                            True,
                            f"Patient age ({presc_age2}) is within policy eligible age range ({min_age}–{max_age}).",
                        )
                    else:
                        evaluate_rule(
                            "ageRangeEligible",
                            False,
                            f"Patient age ({presc_age2}) is outside policy eligible age range ({min_age}–{max_age}): {', '.join(reason_parts)}.",
                            blocker=True,
                        )
    except Exception as e:
        evaluate_rule("ageRangeEligible", True, f"Age range check skipped: {e}")

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

def _fuzzy_name_match(name_a: str, name_b: str) -> bool:
    """
    Fuzzy name matching to handle initials, abbreviations, and common
    spelling variations between prescription and policy member names.

    Examples:
        "r. kumar"  / "raj kumar"   -> True  (initial match)
        "rajkumar"  / "raj kumar"   -> True  (concatenated name)
        "raj kumar" / "kumar raj"   -> True  (word-order swap)
        "arun kumar"/ "raj kumar"   -> False (different first name)
    """
    if not name_a or not name_b:
        return False

    # Normalise: strip punctuation, collapse spaces
    import re
    def _norm(s: str) -> str:
        return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()

    a = _norm(name_a)
    b = _norm(name_b)

    if a == b:
        return True

    # Token sets
    a_tokens = set(a.split())
    b_tokens = set(b.split())

    # 1. All meaningful tokens of the shorter name appear in the longer name
    shorter = a_tokens if len(a_tokens) <= len(b_tokens) else b_tokens
    longer  = a_tokens if len(a_tokens) >  len(b_tokens) else b_tokens
    if shorter and shorter.issubset(longer):
        return True

    # 2. Concatenated name match  ("rajkumar" == "raj kumar")
    if a.replace(" ", "") == b.replace(" ", ""):
        return True

    # 3. Initial match  ("r. kumar" matches "raj kumar" when last name matches)
    for x, y in ((a_tokens, b_tokens), (b_tokens, a_tokens)):
        single_initials = {t for t in x if len(t) == 1}
        if single_initials:
            remaining_x = x - single_initials
            # Every non-initial token of x must appear in y
            if remaining_x and remaining_x.issubset(y):
                # Each initial in x must match the first character of some token in y
                if all(any(t.startswith(init) for t in y) for init in single_initials):
                    return True

    # 4. Word-order swap  ("kumar raj" == "raj kumar")
    if sorted(a.split()) == sorted(b.split()):
        return True

    return False


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
