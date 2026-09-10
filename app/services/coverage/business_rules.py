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
        "nearMatchClarifications": [],  # Near-matches that need user confirmation
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
    # ===========================================================
    # STAGE 1 — MEMBER ELIGIBILITY VALIDATION (HARD GATE #1)
    # All rules below are BLOCKERS. Must pass before evaluating
    # dates, waiting periods, or medical coverage.
    # ===========================================================

    # --- Shared member reference data ---
    p_name = (prescription.get("patientName") or "").strip().lower()

    # Gather members from any field the LLM may have used
    raw_members = (
        policy.get("insuredMembers")
        or policy.get("familyMembers")
        or policy.get("insuredPersons")
        or policy.get("members")
        or policy.get("beneficiaries")
        or []
    )
    # Normalise: LLM sometimes returns ["Name1","Name2"] instead of [{"name":"Name1"}]
    members = []
    for m in raw_members:
        if isinstance(m, str):
            members.append({"name": m})
        elif isinstance(m, dict):
            members.append(m)

    policyholder = (
        policy.get("policyholderName")
        or policy.get("policyHolder")
        or policy.get("insuredPersonName")
        or policy.get("insuredName")
        or policy.get("primaryInsured")
        or ""
    ).strip().lower()

    # If no members list but policyholder name exists, treat it as a single-member list
    if not members and policyholder:
        members = [{"name": policyholder}]

    # DEBUG: log actual members seen (remove after diagnosis is confirmed)
    from app.core.logging import logger as _br_logger
    _br_logger.info(
        f"[BizRules DEBUG] p_name={repr(p_name)} | "
        f"policyholder={repr(policyholder)} | "
        f"members={[m.get('name','?') for m in members[:5]]}"
    )

    # 1. Patient Name Match Check
    if p_name:
        matched_member = None
        match_is_exact = False

        # Clean honorifics from prescription name before matching
        p_name_clean = _strip_titles(p_name)

        for m in members:
            m_name_raw = (
                m.get("name")
                or m.get("fullName")
                or m.get("memberName")
                or ""
            ).strip().lower()
            m_name_clean = _strip_titles(m_name_raw)

            # Exact substring match
            if m_name_raw and (
                m_name_raw in p_name or p_name in m_name_raw
            ):
                matched_member = m
                match_is_exact = True
                break

            # Fuzzy match (after title stripping)
            if m_name_clean and (
                m_name_clean in p_name_clean
                or p_name_clean in m_name_clean
                or _fuzzy_name_match(p_name_clean, m_name_clean)
            ):
                matched_member = m
                match_is_exact = False
                break

        if not matched_member and policyholder:
            ph_clean = _strip_titles(policyholder)
            if policyholder in p_name or p_name in policyholder:
                matched_member = {"name": policyholder}
                match_is_exact = True
            elif (
                ph_clean in p_name_clean
                or p_name_clean in ph_clean
                or _fuzzy_name_match(p_name_clean, ph_clean)
            ):
                matched_member = {"name": policyholder}
                match_is_exact = False

        if not matched_member and (members or policyholder):
            # Hard fail: no match at all — even with fuzzy
            evaluate_rule(
                "patientNameMatch",
                False,
                f"Patient '{prescription.get('patientName')}' is not listed as an insured member on this policy.",
                blocker=True,
            )
        elif matched_member and not match_is_exact:
            # Near-match — pass the rule but flag for user confirmation
            policy_name_display = (
                matched_member.get("name")
                or matched_member.get("fullName")
                or matched_member.get("memberName")
                or policyholder
            )
            evaluate_rule(
                "patientNameMatch",
                True,  # Don't hard-block; let LLM ask the user
                f"Patient name near-match: prescription '{prescription.get('patientName')}' "
                f"approximately matches policy insured '{policy_name_display}'. User confirmation required.",
            )
            results["nearMatchClarifications"].append({
                "field": "patientName",
                "prescriptionValue": prescription.get("patientName", ""),
                "policyValue": policy_name_display,
                "question": (
                    f"The policy lists an insured member as \"\u200b{policy_name_display}\" "
                    f"but the prescription shows the patient as \"\u200b{prescription.get('patientName', '')}\". "
                    f"Are these the same person?"
                ),
            })
        else:
            evaluate_rule(
                "patientNameMatch",
                True,
                f"Patient '{prescription.get('patientName')}' is an active insured member.",
            )

    # 2. Patient Age Match Check
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
                    age_diff = abs(presc_age - pol_age)
                    if age_diff <= 2:
                        evaluate_rule(
                            "patientAgeMatch",
                            True,
                            f"Minor age discrepancy: prescription lists {presc_age} Yrs, policy records {pol_age} Yrs "
                            f"for {matched_member.get('name', 'insured member')}. User confirmation required.",
                        )
                        results["nearMatchClarifications"].append({
                            "field": "patientAge",
                            "prescriptionValue": str(presc_age),
                            "policyValue": str(pol_age),
                            "question": (
                                f"The policy records the insured member \"{matched_member.get('name', 'insured')}\" "
                                f"as {pol_age} years old, but the prescription lists the patient as {presc_age} years old. "
                                f"Can you confirm this is the same person?"
                            ),
                        })
                    else:
                        evaluate_rule(
                            "patientAgeMatch",
                            False,
                            f"Patient age mismatch: Prescription lists {presc_age} Yrs, but policy records {pol_age} Yrs "
                            f"for {matched_member.get('name', 'insured member')}.",
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

    # 3. Patient Gender Match Check
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
                    True,
                    f"Gender discrepancy: prescription lists {prescription.get('patientGender')}, "
                    f"policy records {matched_member.get('gender')} for {matched_member.get('name', 'insured member')}. "
                    f"User confirmation required.",
                )
                results["nearMatchClarifications"].append({
                    "field": "patientGender",
                    "prescriptionValue": prescription.get("patientGender", ""),
                    "policyValue": str(matched_member.get("gender", "")),
                    "question": (
                        f"The policy lists the insured member \"{matched_member.get('name', 'insured')}\" "
                        f"as {matched_member.get('gender')}, but the prescription records the patient as "
                        f"{prescription.get('patientGender')}. Can you confirm the patient's gender?"
                    ),
                })
            else:
                evaluate_rule(
                    "patientGenderMatch",
                    True,
                    "Patient gender matches policy records.",
                )

    # 4. Member ID Match
    rx_member_id = (prescription.get("memberId") or prescription.get("memberNumber") or prescription.get("policyMemberNumber") or "").strip()
    pol_member_id = (policy.get("memberId") or policy.get("memberNumber") or policy.get("policyMemberNumber") or "").strip()
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

    # 5. Age Range Eligible Check
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

    # ===========================================================
    # STAGE 2 — POLICY PERIOD, PRESCRIPTION DATES & WAITING PERIOD (HARD GATE #2)
    # ===========================================================

    # 6. Consultation Date Check
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

    # 7. Policy Expiry Check
    try:
        policy_end = _parse_date(policy.get("policyEndDate"))
        visit_date = _parse_date(prescription.get("visitDate") or prescription.get("consultationDate"))
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

    # 8. Waiting Period & Pre-Existing Disease (PED) Check
    try:
        policy_start = _parse_date(policy.get("policyStartDate"))
        visit_date = _parse_date(prescription.get("visitDate") or prescription.get("consultationDate"))
        diag_date = _parse_date(prescription.get("diagnosisDate") or prescription.get("diseaseOnsetDate") or prescription.get("symptomOnsetDate"))
        if not visit_date and is_self_entered:
            visit_date = datetime.now()
        waiting_days = policy.get("waitingPeriodDays")
        ped_waiting_days = policy.get("pedWaitingPeriodDays") or waiting_days

        is_ped = prescription.get("isPreExisting") is True or (diag_date and policy_start and diag_date < policy_start)

        if policy_start and visit_date:
            days_active = (visit_date - policy_start).days
            if is_ped:
                applicable_wait = int(ped_waiting_days) if ped_waiting_days is not None else 150
                passed = days_active >= applicable_wait
                evaluate_rule(
                    "waitingPeriodCompleted",
                    passed,
                    f"Pre-existing disease. Policy active for {days_active} days (Required PED waiting period: {applicable_wait} days)" if passed else f"Pre-existing disease waiting period not satisfied. Policy active for {days_active} days (Required: {applicable_wait} days)",
                    blocker=True,
                )
            elif waiting_days is not None:
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
                    True,
                    f"Policy active for {days_active} days.",
                    blocker=False,
                )
        else:
            evaluate_rule(
                "waitingPeriodCompleted",
                False,
                "Could not verify waiting period due to missing dates or waiting period value",
                blocker=False,
            )
    except Exception as e:
        evaluate_rule("waitingPeriodCompleted", False, f"Waiting period check error: {e}")

    # ===========================================================
    # STAGE 3 — DIAGNOSIS & EXCLUSIONS
    # ===========================================================

    # 9. Diagnosis Excluded Check
    diagnosis = (prescription.get("diagnosis") or "").lower()
    excluded_diseases = [d.lower() for d in (policy.get("excludedDiseases") or []) if d]
    if diagnosis and excluded_diseases:
        failed = any(d in diagnosis or diagnosis in d for d in excluded_diseases)
        if failed:
             evaluate_rule("diagnosisExcluded", False, f"Diagnosis '{prescription.get('diagnosis')}' is explicitly excluded", blocker=True)
        else:
             evaluate_rule("diagnosisExcluded", True, "Diagnosis is not in the excluded list")
    else:
         evaluate_rule("diagnosisExcluded", True, "No excluded diseases list to check against")

    # 10. Diagnosis Covered Check
    covered_diseases = [d.lower() for d in (policy.get("coveredDiseases") or []) if d]
    if diagnosis and covered_diseases:
        passed = any(d in diagnosis or diagnosis in d for d in covered_diseases)
        if passed:
             evaluate_rule("diagnosisCovered", True, f"Diagnosis '{prescription.get('diagnosis')}' found in covered list")
        else:
             evaluate_rule("diagnosisCovered", False, f"Diagnosis '{prescription.get('diagnosis')}' not explicitly found in covered list", blocker=False)
    else:
        evaluate_rule("diagnosisCovered", False, "Missing diagnosis or covered diseases list", blocker=False)

    # 11. Hospitalization Check
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

    return results


def _strip_titles(name: str) -> str:
    """
    Strip common honorific titles from a name before fuzzy comparison.
    Handles both 'Mr. Vimal' (space) and 'Mr.Vimal' (no space).
    e.g. 'Mr.Vimal M' -> 'vimal m', 'Dr. Anu' -> 'anu'
    """
    import re
    # \s* instead of \s+ to handle no-space case like 'Mr.Vimal'
    titles = r"^(mr\.?|mrs\.?|ms\.?|dr\.?|prof\.?|sir|shri|smt\.?|kumari|master|miss|rev\.?|capt\.?|col\.?|maj\.?)\s*"
    return re.sub(titles, "", name.strip().lower(), flags=re.IGNORECASE).strip()


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
