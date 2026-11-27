"""
Business Rule Engine — migrated from BusinessRuleEngine.js.
Executes deterministic business rules on extracted JSON data.
"""

from datetime import datetime


def enforce_business_rules(policy: dict, prescription: dict, policy_text: str | None = None) -> dict:
    """
    Executes a suite of deterministic business rules against the extracted data.
    `policy_text`, when given, lets exclusions be cited with the policy's own clause wording.

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
    _br_logger.debug(
        f"[BizRules DEBUG] p_name={repr(p_name)} | "
        f"policyholder={repr(policyholder)} | "
        f"members={[m.get('name','?') for m in members[:5]]}"
    )

    # 1. Patient Name Match Check
    matched_member = None
    match_is_exact = False
    if p_name:
        p_name_clean = _strip_titles(p_name)

        for m in members:
            m_name_raw = (
                m.get("name")
                or m.get("fullName")
                or m.get("memberName")
                or ""
            ).strip().lower()
            m_name_clean = _strip_titles(m_name_raw)

            if m_name_raw and (
                m_name_raw in p_name or p_name in m_name_raw
            ):
                matched_member = m
                match_is_exact = True
                break

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
            evaluate_rule(
                "patientNameMatch",
                False,
                f"[Patient Name] Patient '{prescription.get('patientName')}' is not listed as an insured member on this policy schedule.",
                blocker=True,
            )
        elif matched_member and not match_is_exact:
            policy_name_display = (
                matched_member.get("name")
                or matched_member.get("fullName")
                or matched_member.get("memberName")
                or policyholder
            )
            evaluate_rule(
                "patientNameMatch",
                True,
                f"Patient name near-match: prescription '{prescription.get('patientName')}' "
                f"approximately matches policy insured '{policy_name_display}'. User confirmation required.",
            )
            results["nearMatchClarifications"].append({
                "field": "patientName",
                "prescriptionValue": prescription.get("patientName", ""),
                "policyValue": policy_name_display,
                "question": (
                    f"The policy lists an insured member as \"{policy_name_display}\" "
                    f"but the prescription shows the patient as \"{prescription.get('patientName', '')}\". "
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
    p_age_raw = prescription.get("patientAge") or prescription.get("age")
    presc_age = None
    if p_age_raw is not None:
        try:
            import re
            p_age_digits = re.sub(r"\D", "", str(p_age_raw))
            presc_age = int(p_age_digits) if p_age_digits else None

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
                    else:
                        evaluate_rule(
                            "patientAgeMatch",
                            False,
                            f"[Patient Age] Patient age mismatch: Prescription lists {presc_age} Yrs, but policy records {pol_age} Yrs for {matched_member.get('name', 'insured member')}.",
                            blocker=True,
                        )
                else:
                    evaluate_rule(
                        "patientAgeMatch",
                        True,
                        f"Patient age ({presc_age} Yrs) matches policy records.",
                    )
            elif not matched_member and members and presc_age is not None:
                member_ages = []
                for m in members:
                    if m.get("age") is not None:
                        d = re.sub(r"\D", "", str(m.get("age")))
                        if d:
                            member_ages.append((m.get("name") or "member", int(d)))
                if member_ages and not any(abs(presc_age - a[1]) <= 2 for a in member_ages):
                    ages_str = ", ".join(f"{a[0]} ({a[1]} Yrs)" for a in member_ages)
                    evaluate_rule(
                        "patientAgeMatch",
                        False,
                        f"[Patient Age] Patient age ({presc_age} Yrs) does not match any enrolled insured member on the policy schedule ({ages_str}).",
                        blocker=True,
                    )
        except Exception as e:
            evaluate_rule("patientAgeMatch", True, f"Could not parse age: {e}")

    # 3. Patient Gender Match Check
    p_gender = (prescription.get("patientGender") or prescription.get("gender") or "").strip().lower()
    if p_gender:
        if matched_member and matched_member.get("gender"):
            pol_gender = str(matched_member.get("gender")).strip().lower()
            p_is_male = p_gender.startswith("m")
            pol_is_male = pol_gender.startswith("m")

            if p_is_male != pol_is_male:
                evaluate_rule(
                    "patientGenderMatch",
                    False,
                    f"[Gender Discrepancy] Patient gender ({prescription.get('patientGender')}) contradicts policy record ({matched_member.get('gender')} for {matched_member.get('name', 'insured member')}).",
                    blocker=True,
                )
            else:
                evaluate_rule(
                    "patientGenderMatch",
                    True,
                    "Patient gender matches policy records.",
                )
        elif not matched_member and members:
            member_genders = [str(m.get("gender")).strip().lower() for m in members if m.get("gender")]
            p_is_male = p_gender.startswith("m")
            if member_genders and all((g.startswith("m")) != p_is_male for g in member_genders):
                evaluate_rule(
                    "patientGenderMatch",
                    False,
                    f"[Gender Discrepancy] Patient gender ({prescription.get('patientGender') or prescription.get('gender')}) does not match any enrolled member on the policy schedule.",
                    blocker=True,
                )

    # 4. Relationship / dependent eligibility is decided by the AI coverage analysis
    #    from the policy's own member and eligibility wording (no built-in rules).

    # 5. Member ID Match Check
    rx_member_id = (prescription.get("memberId") or prescription.get("memberNumber") or prescription.get("policyMemberNumber") or "").strip()
    pol_member_id = (policy.get("memberId") or policy.get("memberNumber") or policy.get("policyMemberNumber") or "").strip()
    if rx_member_id and pol_member_id:
        if rx_member_id.lower() != pol_member_id.lower():
            evaluate_rule(
                "memberIdMatch",
                False,
                f"[Member ID Mismatch] Prescription '{rx_member_id}' vs Policy '{pol_member_id}'.",
                blocker=True,
            )
        else:
            evaluate_rule("memberIdMatch", True, f"Member ID matched: {rx_member_id}")

    # 6. Age Range Eligible Check
    try:
        import re as _re
        min_age = policy.get("minEligibleAge") or policy.get("minimumAge") or policy.get("minAge")
        max_age = policy.get("maxEligibleAge") or policy.get("maximumAge") or policy.get("maxAge")
        if (min_age is not None or max_age is not None) and presc_age is not None:
            age_ok = True
            reason_parts = []
            if min_age is not None and presc_age < int(str(min_age).strip()):
                age_ok = False
                reason_parts.append(f"below minimum ({min_age} Yrs)")
            if max_age is not None and presc_age > int(str(max_age).strip()):
                age_ok = False
                reason_parts.append(f"above maximum ({max_age} Yrs)")
            if age_ok:
                evaluate_rule(
                    "ageRangeEligible",
                    True,
                    f"Patient age ({presc_age}) is within policy eligible age range ({min_age}–{max_age}).",
                )
            else:
                evaluate_rule(
                    "ageRangeEligible",
                    False,
                    f"[Eligible Age Band] Patient age ({presc_age} Yrs) is outside policy allowable age band ({min_age}–{max_age} Yrs): {', '.join(reason_parts)}.",
                    blocker=True,
                )
    except Exception as e:
        evaluate_rule("ageRangeEligible", True, f"Age range check skipped: {e}")

    # ===========================================================
    # STAGE 2 — POLICY PERIOD, PRESCRIPTION DATES & WAITING PERIOD (HARD GATE #2)
    # ===========================================================

    # 7. Consultation Date Check
    c_date_raw = prescription.get("consultationDate") or prescription.get("visitDate")
    c_date = _parse_date(c_date_raw) if c_date_raw else None
    p_start = _parse_date(policy.get("policyStartDate"))
    p_end = _parse_date(policy.get("policyEndDate"))
    p_issued = _parse_date(policy.get("policyIssuedDate") or policy.get("policyIssueDate"))
    now = datetime.now()

    if c_date_raw and c_date:
        try:
            if p_start and c_date < p_start:
                if p_issued and c_date == p_issued:
                    evaluate_rule(
                        "consultationDateValid",
                        False,
                        f"[Pre-Effective Policy Window] Consultation date ({c_date_raw}) is on policy issuance date ({policy.get('policyIssuedDate') or 'the issue date'}) prior to risk inception ({policy.get('policyStartDate')}). Coverage has not commenced.",
                        blocker=True,
                    )
                else:
                    evaluate_rule(
                        "consultationDateValid",
                        False,
                        f"[Policy Inception] Consultation date ({c_date_raw}) occurred prior to policy inception ({policy.get('policyStartDate')}). Medical expenses incurred before inception are non-payable.",
                        blocker=True,
                    )
            elif p_end and c_date > p_end:
                days_post = (c_date - p_end).days
                if 0 < days_post <= 30:
                    evaluate_rule(
                        "consultationDateValid",
                        False,
                        f"[Renewal Grace Period Inactive] Consultation date ({c_date_raw}) occurred {days_post} days after policy expiry ({policy.get('policyEndDate')}) during unpaid renewal grace period. Claims during unpaid grace period are not covered.",
                        blocker=True,
                    )
                else:
                    evaluate_rule(
                        "consultationDateValid",
                        False,
                        f"[Policy Expiration] Consultation date ({c_date_raw}) occurred after policy expiration date ({policy.get('policyEndDate')}). Coverage ended on {policy.get('policyEndDate')}.",
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

    # 8. Policy Expiry Check
    try:
        visit_date = c_date
        if not visit_date and is_self_entered:
            visit_date = datetime.now()

        if p_end and visit_date:
            passed = visit_date <= p_end
            evaluate_rule(
                "policyActive",
                passed,
                "Policy active during visit/evaluation date" if passed else f"[Policy Expired] Policy expired on {policy.get('policyEndDate')} before consultation date.",
                blocker=not passed,
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

    # 9. Waiting periods: which waiting period applies (initial, pre-existing, specified
    #    illnesses, and any exceptions such as accidents) is decided by the AI coverage
    #    analysis from the policy's own wording. Only the cover duration is computed here,
    #    so the AI never has to do date arithmetic.
    from app.services.coverage.cover_duration import cover_duration
    results["coverDuration"] = cover_duration(policy, prescription)

    # ===========================================================
    # STAGE 3 — DIAGNOSIS, CARE SETTING & EXCLUSIONS
    # ===========================================================

    # 10-11. Exclusions, care setting (in-patient / day care / out-patient) and their
    #        exceptions are decided by the AI coverage analysis from the policy wording.

    # ===========================================================
    # STAGE 4 — DETERMINISTIC COVERAGE RULE ENGINE & AGGREGATION
    # ===========================================================
    try:
        from app.services.rule_engine import CoverageRuleEngine
        decision = CoverageRuleEngine.evaluate({**policy, "policyText": policy_text} if policy_text else policy, prescription)
        results["deterministicResult"] = decision.model_dump(mode="json", by_alias=True)
        results["decision"] = decision.status.value
        results["reasonCode"] = decision.reason_code.value
        results["manualReviewRequired"] = decision.manual_review_required

        if not decision.overall_eligible:
            results["overallEligible"] = False
            for blk in decision.blockers:
                if blk not in results["blockers"]:
                    results["blockers"].append(blk)
        for warn in decision.warnings:
            if warn not in results["warnings"]:
                results["warnings"].append(warn)
    except Exception as e:
        from app.core.logging import logger as _err_logger
        _err_logger.error(f"[enforce_business_rules] Deterministic engine error: {e}")

    # Synchronize overallEligible and deterministicResult with ALL accumulated blockers
    if results["blockers"]:
        results["overallEligible"] = False
        if "deterministicResult" not in results or not results["deterministicResult"]:
            results["deterministicResult"] = {}
        results["deterministicResult"]["status"] = "NOT_COVERED"
        results["deterministicResult"]["reasonCode"] = "DETERMINISTIC_EXCLUSION"
        results["deterministicResult"]["blockers"] = list(results["blockers"])
        results["decision"] = "NOT_COVERED"

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
