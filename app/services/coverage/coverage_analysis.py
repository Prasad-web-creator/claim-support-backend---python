"""Coverage decisions and report card formatting."""

from app.core.config import get_settings
from app.core.logging import logger
from app.services.coverage.gemini_triage import triage_unresolved
from app.services.coverage.policy_evidence import policy_chunks, ranked_clauses
from app.services.coverage.verification import NEEDS_VERIFICATION
from app.services.rule_engine.adjudication import claim_items, decide_items
from app.services.rule_engine.catalog import load_catalog


async def analyze_coverage(
    policy_text: str,
    prescription_text: str,
    business_rule_results: dict,
    prescription_json: dict,
    clarification_history: list = None,
    policy_json: dict = None,
    policy_id: str = None,
    on_stage=None,
    extra_instruction: str = "",
) -> dict:
    """Apply reviewed rules and return the existing interactive analysis contract."""
    async def stage(name: str) -> None:
        if on_stage is not None:
            try:
                await on_stage(name)
            except Exception:
                logger.warning("[Coverage] Progress update failed for stage {}", name)

    policy = policy_json or {}
    prescription = prescription_json or {}
    checks = business_rule_results or {}
    if policy and prescription and not checks:
        from app.services.coverage.business_rules import enforce_business_rules

        checks = enforce_business_rules(policy, prescription, policy_text=policy_text)

    await stage("evidence_retrieval")
    catalog = load_catalog()
    items = claim_items(prescription)
    settings = get_settings()
    evidence = ranked_clauses(
        policy_chunks(policy_text, policy, policy_id or ""),
        (item["item"] for item in items),
        settings.RAG_TOP_K,
    )

    await stage("coverage_analysis")
    comparison, questions, unresolved, policy_rules_id = decide_items(
        catalog, policy_text, policy, prescription, checks, clarification_history or [],
    )
    if questions:
        return {"next_action": "ask_questions", "confidence": 0, "questions": questions}

    manual = not comparison or any(item["coverageStatus"] == "Manual Review" for item in comparison)
    statuses = {item["coverageStatus"] for item in comparison}
    overall_status = "Manual Review Required" if manual else (
        next(iter(statuses)) if len(statuses) == 1 else "Partially Covered"
    )
    summary = (
        "One or more items require manual review before coverage can be confirmed."
        if manual else "Coverage was evaluated using the policy's verified terms and eligibility facts."
    )
    clauses = [clause for item in comparison for clause in item.get("policyClauses", ())]
    next_steps = []
    if manual:
        advice = await triage_unresolved(unresolved) if unresolved else None
        next_steps.append(advice or "Ask the insurer to confirm coverage using the policy wording and applicable schedule.")

    result = {
        "next_action": "manual_review" if manual else "generate_report",
        "confidence": 0 if manual else 100,
        "retrieved_evidence": evidence,
        "analysis": {
            "summary": summary,
            "overallStatus": overall_status,
            "comparison": comparison,
            "retrieved_evidence": evidence,
            "policyClausesUsed": clauses,
            "nextSteps": next_steps,
            "ruleAudit": {
                "catalogVersion": catalog.version,
                "policyRulesId": policy_rules_id,
                "itemRuleVersions": {
                    item["ruleId"]: item["ruleVersion"] for item in comparison if item.get("ruleId")
                },
                "manualReviewRequired": manual,
                "prescriptionConfirmed": not prescription.get("_ocrReviewRequired"),
            },
        },
    }
    if manual:
        result["reason"] = summary
    return result


MAX_PRESCRIPTION_CHARS = 20000


def _item_names(values) -> str:
    """Names (with any stated cost) of extracted prescription items, never raw dicts."""
    out = []
    for v in values or []:
        if isinstance(v, dict):
            name = str(v.get("name") or v.get("item") or v.get("test") or "").strip()
            if not name:
                continue
            cost = v.get("cost")
            out.append(f"{name} (₹{cost:,})" if isinstance(cost, (int, float)) and cost else name)
        elif v is not None and str(v).strip():
            out.append(str(v).strip())
    return ", ".join(out)


def _claim_facts_block(rx: dict) -> str:
    """Every extracted prescription fact that can change a coverage decision."""
    rx = rx or {}
    hosp = rx.get("hospitalizationRequired")
    lines = [
        f"• Patient: {rx.get('patientName') or 'not stated'}; age {rx.get('patientAge') or rx.get('age') or 'not stated'}; "
        f"gender {rx.get('patientGender') or rx.get('gender') or 'not stated'}",
        f"• Treatment / admission date: {rx.get('admissionDate') or rx.get('admission') or rx.get('visitDate') or 'not stated'}",
        f"• Diagnosis: {rx.get('diagnosis') or 'Not specified'}",
        f"• Symptoms: {', '.join(str(s) for s in (rx.get('symptoms') or [])) or 'None reported'}",
        f"• Procedures: {_item_names(rx.get('procedures')) or 'None reported'}",
        f"• Tests / investigations: {_item_names(rx.get('medicalTests')) or 'None reported'}",
        "• Hospitalisation: "
        + ("required (in-patient)" if hosp is True else "not required (out-patient)" if hosp is False else "not stated"),
    ]
    if rx.get("discharge"):
        lines.append(f"• Discharge: {rx.get('discharge')}")
    cost = rx.get("estimatedCost")
    if cost:
        lines.append(f"• Estimated cost (from the prescription): ₹{cost:,}" if isinstance(cost, (int, float))
                     else f"• Estimated cost (from the prescription): {cost}")
    for key, label in (("medicalNotes", "History / notes"), ("doctorNotes", "Doctor's notes")):
        if rx.get(key):
            lines.append(f"• {label}: {rx.get(key)}")
    return "\n".join(lines)


def _prescription_text_block(prescription_text: str, is_manual: bool) -> str:
    text = (prescription_text or "").strip()
    if not text:
        return "Not available; use the extracted fields above."
    if is_manual:
        return f"(Self-entered text) {text}"
    if len(text) > MAX_PRESCRIPTION_CHARS:
        logger.warning(f"[AI Analysis] Prescription text is {len(text)} characters; only the first "
                       f"{MAX_PRESCRIPTION_CHARS} are sent to the AI.")
        text = text[:MAX_PRESCRIPTION_CHARS]
    return text


def _app_checks_for_prompt(business_rule_results: dict) -> dict:
    """
    The app checks as the AI sees them: only whether the policy period and member
    checks passed, and why not. The engine's internal outcome label (which reads
    "COVERED" when nothing failed) is left out so it never suggests a coverage result.
    """
    br = business_rule_results if isinstance(business_rule_results, dict) else {}
    det = br.get("deterministicResult") or {}
    blockers = list(br.get("blockers") or [])
    manual = det.get("status") == "MANUAL_REVIEW" or bool(br.get("manualReviewRequired"))
    return {
        "policyPeriodAndMemberChecks": "FAILED" if blockers else ("NEEDS_REVIEW" if manual else "PASSED"),
        "failedChecks": blockers,
        "notes": list(br.get("warnings") or []),
        "checks": br.get("rules") or {},
        "nearMatchClarifications": br.get("nearMatchClarifications") or [],
    }


def _cover_duration_block(business_rule_results: dict) -> str:
    """The cover duration facts for the coverage prompt."""
    facts = (business_rule_results or {}).get("coverDuration") if isinstance(business_rule_results, dict) else None
    if not isinstance(facts, dict) or not facts.get("summary"):
        return "• Not available (a date is missing). Decide waiting periods only if the policy and claim dates allow it."
    return (
        f"• {facts['summary']}\n"
        f"• Cover counted from: {facts.get('coverStartDate')} ({facts.get('coverStartBasis')})\n"
        f"• Completed months of cover: {facts.get('completedMonths')}; days of cover: {facts.get('daysActive')}"
    )


def _format_date_readable(date_str: str | None) -> str:
    """Formats date into e.g. '10 January 2024'."""
    if not date_str:
        return ""
    try:
        from app.services.rule_engine.date_utils import parse_flexible_date
        d = parse_flexible_date(date_str)
        if d:
            return d.strftime("%d %B %Y")
    except Exception:
        pass
    return str(date_str).strip()


def _format_date_compact(date_str: str | None) -> str:
    """Formats date into e.g. '10-01-2024'."""
    if not date_str:
        return ""
    try:
        from app.services.rule_engine.date_utils import parse_flexible_date
        d = parse_flexible_date(date_str)
        if d:
            return d.strftime("%d-%m-%Y")
    except Exception:
        pass
    return str(date_str).strip()


def _is_policy_exclusion_blocker(blocker: str) -> bool:
    text = blocker.split(" - ", 1)[0].lower()
    return "permanently excluded" in text or "[explicit policy exclusion]" in text


def decisive_blockers(blockers: list[str]) -> list[str]:
    """
    The blockers that decide the outcome. A permanent policy exclusion comes first, and a
    waiting period is dropped beside it: it is moot for a treatment the policy never covers.
    """
    exclusions = [b for b in blockers if _is_policy_exclusion_blocker(b)]
    if not exclusions:
        return list(blockers)
    return exclusions + [b for b in blockers if b not in exclusions and "waiting period" not in b.lower()]


def _format_item_card(
    item: dict,
    policy_json: dict | None = None,
    prescription_json: dict | None = None,
    override_status: str | None = None,
    reason_code: str | None = None,
    blockers: list[str] | None = None,
) -> dict:
    """
    Formats an individual diagnosis/item card to strictly conform to:
    1. coverageStatus: Strictly 'Covered', 'Not Covered', or 'Partially Covered'
    2. coverageStatusReason / explanation: Plain-English friendly text with policy-related terms
    3. policyEvidence: Concrete policy dates, clause title/section, and document excerpt
    4. financialDecision: Clear financial coverage details (payable %, patient out-of-pocket, co-pay)
    """
    policy_json = policy_json or {}
    prescription_json = prescription_json or {}
    item = dict(item)

    item_name = item.get("item") or item.get("name") or "Prescribed Diagnosis/Treatment"
    current_status = override_status or item.get("coverageStatus") or item.get("status") or "Not Covered"
    
    # Normalize status to one of the allowed values
    status_lower = str(current_status).strip().lower()
    if "manual" in status_lower or "review" in status_lower:
        final_status = "Manual Review"
        is_covered = False
    elif "verif" in status_lower:
        final_status = NEEDS_VERIFICATION
        is_covered = False
    elif "partially" in status_lower:
        final_status = "Partially Covered"
        is_covered = True
    elif "not" in status_lower or "reject" in status_lower or "deni" in status_lower or "fail" in status_lower or "uncover" in status_lower:
        final_status = "Not Covered"
        is_covered = False
    elif "cover" in status_lower:
        final_status = "Covered"
        is_covered = True
    else:
        final_status = "Not Covered"
        is_covered = False

    # Extract dates & policy metadata
    p_start_raw = (
        policy_json.get("policyStartDate")
        or policy_json.get("policyEffectiveDate")
        or policy_json.get("effectiveFrom")
        or policy_json.get("inceptionDate")
    )
    p_end_raw = (
        policy_json.get("policyEndDate")
        or policy_json.get("policyExpirationDate")
        or policy_json.get("effectiveTo")
        or policy_json.get("expiryDate")
    )
    v_date_raw = (
        prescription_json.get("visitDate")
        or prescription_json.get("consultationDate")
        or prescription_json.get("treatmentDate")
        or prescription_json.get("admissionDate")
    )
    p_start_read = _format_date_readable(p_start_raw)
    p_end_read = _format_date_readable(p_end_raw)
    v_date_read = _format_date_readable(v_date_raw)

    p_start_comp = _format_date_compact(p_start_raw)
    p_end_comp = _format_date_compact(p_end_raw)
    period_str = f"{p_start_comp or 'Not available'} to {p_end_comp or 'Not available'}"

    cov_amt = policy_json.get("coverageAmount")
    cov_str = f"₹{cov_amt:,}" if isinstance(cov_amt, (int, float)) and cov_amt > 0 else "the policy Sum Insured"

    blockers_str = " ".join(blockers or []).lower()
    code_str = (reason_code or "").upper()

    # Determine status reason, policy evidence, and financial status
    # Undecided: the automated analysis could not reach a decision for this item
    if final_status == "Manual Review":
        status_reason = item.get("coverageStatusReason") or item.get("explanation") or (
            f"Coverage for {item_name} could not be confirmed automatically from the policy and prescription. "
            f"This item has been referred for manual review."
        )
        policy_evidence = item.get("policyEvidence") or f"Period of Insurance : {period_str}. Decision pending manual review."
        financial_status = item.get("financialDecision") or "Payable amount will be confirmed after manual review."

    # Depends on one fact the documents do not give (e.g. dioptres, BMI, cause of injury)
    elif final_status == NEEDS_VERIFICATION:
        condition = str(item.get("verificationCondition") or "").strip()
        clause_name = str(item.get("verificationClause") or "").strip()
        existing_reason = item.get("coverageStatusReason") or item.get("explanation") or item.get("reason")
        status_reason = (
            existing_reason
            if existing_reason and len(existing_reason) > 25
            else (
                f"Coverage for {item_name} depends on a detail that is not available yet. {condition}".strip()
            )
        )
        existing_evidence = item.get("policyEvidence")
        policy_evidence = (
            existing_evidence
            if existing_evidence and "No matching" not in existing_evidence and len(existing_evidence) > 15
            else f"Period of Insurance : {period_str}. {clause_name or 'See the policy clauses considered for this item.'}"
        )
        existing_fin = item.get("financialDecision")
        financial_status = (
            existing_fin
            if existing_fin and len(existing_fin) > 15
            else "The payable amount depends on the detail that needs verification."
        )

    # Case 0: All Blockers Aggregation
    elif final_status == "Not Covered" and blockers and len(blockers) > 0:
        is_covered = False
        shown = decisive_blockers(blockers)
        exclusion_blockers = [b for b in shown if _is_policy_exclusion_blocker(b)]
        # Blockers may read "<reason> - <policy wording>": the reason explains, the wording is the evidence.
        reasons_list = "\n".join(f"• {b.split(' - ', 1)[0]}" for b in shown)
        status_reason = (
            f"Treatment for {item_name} is not covered due to the following policy criteria and restrictions:\n{reasons_list}"
        )
        policy_evidence = (
            f"Period of Insurance : {period_str}. Mandatory Policy Exclusions & Terms: "
            + "; ".join(b.split(" - ", 1)[1] if " - " in b else b for b in shown[:3])
        )
        financial_status = (
            f"0% coverage. 100% patient financial responsibility because {item_name} is permanently excluded under the policy."
            if exclusion_blockers
            else "0% coverage. 100% patient financial responsibility because the claim violates one or more mandatory policy eligibility, waiting period, or coverage terms."
        )

    # Case 1: Policy Expired
    elif "POLICY_EXPIRED" in code_str or "expir" in blockers_str or "after policy" in blockers_str:
        final_status = "Not Covered"
        is_covered = False
        end_disp = p_end_read or "the policy end date"
        visit_disp = v_date_read or "the treatment date"
        status_reason = (
            f"Your insurance policy expired on {end_disp}. Your doctor visit was on {visit_disp}. "
            f"Claims cannot be paid for treatments taken after the policy has ended. Under your policy terms, "
            f"medical coverage is strictly restricted to services received during the active Period of Insurance."
        )
        policy_evidence = (
            f"Period of Insurance : {period_str}. Policy Clause: Policy Period & Expiration - Coverage terminates at "
            f"midnight on the policy end date. Claims incurred outside the active Period of Insurance are strictly excluded from payment."
        )
        financial_status = (
            "0% coverage. 100% patient financial responsibility because the doctor visit and treatment occurred after the insurance policy ended."
        )

    # Case 2: Policy Not Yet Effective / Inception Date
    elif "POLICY_NOT_YET_EFFECTIVE" in code_str or "prior to policy start" in blockers_str:
        final_status = "Not Covered"
        is_covered = False
        start_disp = p_start_read or "the policy start date"
        visit_disp = v_date_read or "the treatment date"
        status_reason = (
            f"Your insurance policy starts on {start_disp}. Your doctor visit was on {visit_disp}. "
            f"Claims cannot be paid for medical consultations or treatments received prior to the policy inception date. "
            f"Coverage only applies to treatments after the active Period of Insurance begins."
        )
        policy_evidence = (
            f"Period of Insurance : {period_str}. Policy Clause: Inception Date & Coverage Commencement - Expenses incurred "
            f"prior to the policy commencement date are non-payable."
        )
        financial_status = (
            "0% coverage. 100% patient financial responsibility because medical treatment was received before policy inception."
        )

    # (Waiting periods are decided by the AI from the policy's own wording, so no
    #  built-in waiting-period wording is produced here.)

    # Case 4: Member Not Found / Mismatch
    elif "MEMBER" in code_str or "name" in blockers_str:
        final_status = "Not Covered"
        is_covered = False
        rx_name = prescription_json.get("patientName") or "the patient"
        status_reason = (
            f"The patient name ({rx_name}) on the prescription does not match any insured member listed on the policy schedule. "
            f"Insurance benefits can only be claimed for registered, verified insured members."
        )
        policy_evidence = (
            f"Policy Clause: Insured Beneficiaries - Medical expenses are only payable for individuals named on the policy schedule. "
            f"Patient '{rx_name}' is not listed as an insured member."
        )
        financial_status = (
            "0% coverage. 100% patient financial responsibility because the patient is not an enrolled member under this insurance policy."
        )

    # Case 5: Age Eligibility Failure
    elif "AGE" in code_str or "age" in blockers_str:
        final_status = "Not Covered"
        is_covered = False
        patient_age = prescription_json.get("age") or prescription_json.get("patientAge") or "specified age"
        min_age = policy_json.get("minEligibleAge")
        max_age = policy_json.get("maxEligibleAge")
        band = (
            f" (eligible age band in the policy: {min_age} to {max_age} years)"
            if min_age is not None and max_age is not None else ""
        )
        status_reason = (
            f"The patient's age ({patient_age} years) does not satisfy the age eligibility stated in the policy{band}."
        )
        policy_evidence = (
            f"Period of Insurance : {period_str}. Age eligibility as stated in the policy{band}."
        )
        financial_status = (
            "0% coverage. 100% patient financial responsibility due to age eligibility restrictions under policy terms."
        )

    # Case 6: Sub-Limit / Partially Covered
    elif final_status == "Partially Covered":
        existing_reason = item.get("coverageStatusReason") or item.get("explanation") or item.get("reason")
        status_reason = (
            existing_reason
            if existing_reason and len(existing_reason) > 25
            else (
                f"Treatment for {item_name} is partially covered under your policy. A limit or cost-sharing term of the policy "
                f"applies, so part of the cost is the patient's responsibility."
            )
        )
        existing_evidence = item.get("policyEvidence")
        policy_evidence = (
            existing_evidence
            if existing_evidence and "No matching" not in existing_evidence and len(existing_evidence) > 15
            else f"Period of Insurance : {period_str}. See the policy clauses considered for this item."
        )
        existing_fin = item.get("financialDecision")
        financial_status = (
            existing_fin
            if existing_fin and len(existing_fin) > 15
            else "Partially covered as per the policy's limits and cost-sharing terms."
        )

    # Case 7: Covered
    elif final_status == "Covered":
        existing_reason = item.get("coverageStatusReason") or item.get("explanation") or item.get("reason")
        status_reason = (
            existing_reason
            if existing_reason and len(existing_reason) > 25
            else (
                f"Your treatment for {item_name} is covered under your policy. "
                f"The treatment date ({v_date_read or 'the treatment date'}) is within your active Period of Insurance ({period_str})."
            )
        )
        existing_evidence = item.get("policyEvidence")
        policy_evidence = (
            existing_evidence
            if existing_evidence and "No matching" not in existing_evidence and len(existing_evidence) > 15
            else f"Period of Insurance : {period_str}. See the policy clauses considered for this item."
        )
        existing_fin = item.get("financialDecision")
        financial_status = (
            existing_fin
            if existing_fin and len(existing_fin) > 15
            else f"Covered up to the policy Sum Insured of {cov_str}, subject to the policy's terms."
        )

    # Case 8: Other Not Covered
    else:
        existing_reason = item.get("coverageStatusReason") or item.get("explanation") or item.get("reason")
        status_reason = (
            existing_reason
            if existing_reason and len(existing_reason) > 25
            else f"Treatment for {item_name} is not covered under the terms and conditions of this policy."
        )
        existing_evidence = item.get("policyEvidence")
        policy_evidence = (
            existing_evidence
            if existing_evidence and "No matching" not in existing_evidence and len(existing_evidence) > 15
            else f"Period of Insurance : {period_str}. See the policy clauses considered for this item."
        )
        existing_fin = item.get("financialDecision")
        financial_status = (
            existing_fin
            if existing_fin and len(existing_fin) > 15
            else "0% covered. 100% patient financial responsibility under the policy's terms."
        )

    # Ensure all sync fields are assigned
    item["coverageStatus"] = final_status
    item["status"] = final_status
    item["isCovered"] = is_covered
    item["coverageStatusReason"] = status_reason
    item["explanation"] = status_reason
    item["reason"] = status_reason
    item["policyEvidence"] = policy_evidence
    item["financialDecision"] = financial_status

    return item


def _generate_fallback_comparison(prescription_json: dict, policy_json: dict | None = None) -> list[dict]:
    """Item list used when the AI produced none; nothing was decided, so items await manual review."""
    comparison = []
    
    if not prescription_json:
        return comparison

    # Diagnosis if present
    diag = prescription_json.get("diagnosis")
    if diag and str(diag).strip().lower() not in ("none", "null", "unknown", ""):
        raw_item = {
            "item": str(diag),
            "itemType": "Diagnosis",
            "coverageStatus": "Manual Review",
            "isCovered": False,
        }
        comparison.append(_format_item_card(raw_item, policy_json, prescription_json, override_status="Manual Review"))
            
    # Tests
    for test in prescription_json.get("medicalTests", []):
        if isinstance(test, dict) and test.get("name"):
            raw_item = {
                "item": test.get("name"),
                "itemType": "Test",
                "coverageStatus": "Manual Review",
                "isCovered": False,
            }
            comparison.append(_format_item_card(raw_item, policy_json, prescription_json, override_status="Manual Review"))
            
    # Procedures
    for proc in prescription_json.get("procedures", []):
        if isinstance(proc, dict) and proc.get("name"):
            raw_item = {
                "item": proc.get("name"),
                "itemType": "Procedure",
                "coverageStatus": "Manual Review",
                "isCovered": False,
            }
            comparison.append(_format_item_card(raw_item, policy_json, prescription_json, override_status="Manual Review"))
            
    return comparison
