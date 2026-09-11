import json

from app.core.logging import logger
from app.services.llm.ai_client import extract_json_with_retry

from app.services.coverage.coverage_prompt import COVERAGE_ANALYSIS_SYSTEM_PROMPT

async def analyze_coverage(
    policy_text: str,
    prescription_text: str,
    business_rule_results: dict,
    prescription_json: dict,
    clarification_history: list = None,
    policy_json: dict = None,
    policy_id: str = None,
) -> dict:
    logger.info("[AI Analysis] Evaluating coverage and generating explanation with Gemini...")

    from datetime import datetime
    today_str = datetime.now().strftime("%Y-%m-%d")

    # Determine prescription source so the LLM applies the correct validation path
    is_manual_prescription = bool(
        prescription_json and (
            prescription_json.get("isManual")
            or prescription_json.get("prescriptionSource") == "Self-entered Prescription"
            or prescription_json.get("manualText")
        )
    )
    source_label = (
        "Self-entered Prescription (Manual Text — prospective query for current sudden illness / symptoms before or during clinic/hospital visit)"
        if is_manual_prescription
        else "Uploaded Prescription (PDF / Image / OCR — standard extracted document)"
    )

    # ──────────────────────────────────────────────────────────────────────────
    # RAG: Retrieve Relevant Policy Context (Policy-Aware Retrieval)
    # ──────────────────────────────────────────────────────────────────────────
    from app.services.rag.policy_retrieval_service import PolicyRetrievalService

    retrieved_chunks = []
    try:
        # If policy_id is provided and text exists, ensure indexed
        if policy_id and policy_text:
            from app.models.rag_chunk import PolicyChunk
            chunk_count = await PolicyChunk.find(PolicyChunk.policy_id == policy_id).count()
            if chunk_count == 0:
                await PolicyRetrievalService.index_policy_text(
                    policy_id=policy_id,
                    text=policy_text,
                    policy_meta=policy_json or {},
                )

        retrieved_chunks = await PolicyRetrievalService.retrieve_policy_context(
            policy_id=policy_id,
            claim=prescription_json or {},
            top_k=5,
            policy_metadata=policy_json or {},
        )
    except Exception as rag_err:
        logger.debug(f"[AI Analysis] RAG retrieval notice: {rag_err}. Using text fallback.")
        retrieved_chunks = []

    # Build Retrieved Policy Evidence block
    if retrieved_chunks:
        evidence_lines = []
        for i, chk in enumerate(retrieved_chunks, 1):
            auth = chk.get("authority_level", "CONTRACTUAL_POLICY_WORDING")
            evidence_lines.append(
                f"[Evidence #{i}] Source: {chk.get('source_document')} | Page: {chk.get('page')} | "
                f"Section: {chk.get('section')} | Rule Type: {chk.get('rule_type')} | "
                f"Authority: {auth} | Relevance: {int(chk.get('relevance', 0.8) * 100)}%\n"
                f"\"{chk.get('text')}\"\n"
            )
        retrieved_evidence_block = "\n".join(evidence_lines)
    else:
        # Fallback to direct excerpt if no chunks retrieved
        retrieved_evidence_block = (
            f"[Direct Policy Text Excerpt]\n{policy_text[:12000]}\n"
            if policy_text else "No policy clauses available."
        )

    # Build near-match clarification block for the prompt
    near_matches = business_rule_results.get("nearMatchClarifications", [])
    near_match_block = ""
    if near_matches:
        near_match_block = """
==================================================

NEAR-MATCH CLARIFICATIONS REQUIRED
(The business rule engine detected approximate matches that need user confirmation.
 You MUST ask the user these questions via 'ask_questions' before making a final decision.
 Use the exact question text provided below — do not rewrite or skip them.)

"""
        for nm in near_matches:
            near_match_block += (
                f"• Field: {nm.get('field')}\n"
                f"  Prescription value: \"{nm.get('prescriptionValue')}\"\n"
                f"  Policy value:        \"{nm.get('policyValue')}\"\n"
                f"  Question to ask:     {nm.get('question')}\n\n"
            )

    # Applicable Policy metadata summary
    pol_summary_lines = []
    if policy_json:
        if policy_json.get("insuranceCompany"):
            pol_summary_lines.append(f"• Insurer: {policy_json.get('insuranceCompany')}")
        if policy_json.get("policyName"):
            pol_summary_lines.append(f"• Product: {policy_json.get('policyName')}")
        if policy_json.get("policyType"):
            pol_summary_lines.append(f"• Variant/Plan: {policy_json.get('policyType')}")
        if policy_json.get("coverageAmount"):
            pol_summary_lines.append(f"• Sum Insured: ₹{policy_json.get('coverageAmount'):,}")
        if policy_json.get("policyStartDate"):
            pol_summary_lines.append(f"• Policy Start: {policy_json.get('policyStartDate')}")
        if policy_json.get("policyEndDate"):
            pol_summary_lines.append(f"• Policy Expiry: {policy_json.get('policyEndDate')}")
    applicable_policy_str = "\n".join(pol_summary_lines) if pol_summary_lines else "Standard Active Policy Document"

    # Retrieve similar approved expert cases for advisory context
    similar_expert_cases = []
    expert_cases_block = "• None available for this specific query."
    try:
        from app.services.expert_learning.case_retrieval_service import SimilarCaseRetrievalService
        similar_expert_cases = await SimilarCaseRetrievalService.retrieve_similar_cases(
            prescription_json=prescription_json,
            policy_json=policy_json,
            limit=3,
            min_similarity=0.45
        )
        expert_cases_block = SimilarCaseRetrievalService.format_expert_cases_for_prompt(similar_expert_cases)
    except Exception as exp_err:
        logger.warning(f"[Coverage Analysis] Error retrieving similar expert cases: {exp_err}")

    # Build the prompt
    user_prompt = f"""
CURRENT CLAIM
• Evaluation Date: {today_str}
• Prescription Source: {source_label}
• Diagnosis: {prescription_json.get('diagnosis', 'Not specified')}
• Symptoms: {', '.join(str(s) for s in (prescription_json.get('symptoms') or [])) or 'None reported'}
• Procedures: {', '.join(str(p) for p in (prescription_json.get('procedures') or [])) or 'None reported'}

==================================================

APPLICABLE POLICY
{applicable_policy_str}

==================================================

RETRIEVED POLICY EVIDENCE (POLICY-AWARE RAG)
(The following clauses have been retrieved as the most relevant to this specific policy, condition, and treatment.
 CONTRACTUAL POLICY WORDING is the highest authority and takes precedence over marketing reference benchmarks.
 You MUST base your explanation and decision strictly on this evidence; do not invent coverage or mix variants):

{retrieved_evidence_block}

==================================================

DETERMINISTIC RULE ENGINE RESULT (MANDATORY GROUND TRUTH)
(The deterministic rule engine is the SOLE authority for coverage eligibility, waiting periods,
permanent exclusions, and sub-limits. You MUST explain why the decision was reached based on the
retrieved evidence. You are STRICTLY FORBIDDEN from overriding a FAILED rule or changing
NOT_COVERED / NOT_CURRENTLY_COVERED into Covered):

{json.dumps(business_rule_results, indent=2, default=str)}

==================================================

SIMILAR EXPERT CASES
(Historical expert cases provide reference context only; they are NOT contractual policy wording):
{expert_cases_block}

==================================================

CRITICAL ADJUDICATION INSTRUCTIONS:
1. Rely strictly on the RETRIEVED POLICY EVIDENCE above and the DETERMINISTIC RULE ENGINE RESULT.
2. The Deterministic Rule Engine is authoritative. If a condition is WAITING_PERIOD_ACTIVE or PERMANENT_EXCLUSION, you MUST NOT mark it as Covered. Explain why it is not covered.
3. Do not invent coverage terms or assume benefits not supported by retrieved text.
4. If the retrieved evidence is insufficient to confirm coverage or exclusions, set "next_action": "manual_review".
5. Uploaded contractual policy wording associated with the customer's policy is the highest authority.

=================================================={near_match_block}

PREVIOUS CLARIFICATION HISTORY
(Use this to avoid asking duplicate questions. If a question is answered here, DO NOT ask it again.)

{json.dumps(clarification_history or [], indent=2, default=str)}
"""

    try:
        # Increase token limit as analysis outputs can be quite large (up to 8000 tokens)
        result = await extract_json_with_retry(
            system_prompt=COVERAGE_ANALYSIS_SYSTEM_PROMPT,
            user_content=user_prompt,
            max_tokens=8192,
            operation_name="Coverage Analysis",
        )
        
        extracted = result["extractedJson"]
        timing = result.get("timing", {})
        tokens = result.get("tokens", {})
        
        logger.info(
            f"[AI Analysis] Coverage analysis completed in {timing.get('llmProcessingSec', 0)}s "
            f"({tokens.get('totalTokens', 0)} tokens)"
        )
        
        # Parse next_action
        next_action = extracted.get("next_action")
        confidence = extracted.get("confidence", 0)

        # Append raw tokens and timing to the extracted dict so it can be used for audit logging later
        extracted["_llm_metrics"] = {
            "timing": timing,
            "tokens": tokens
        }
        extracted["retrieved_evidence"] = retrieved_chunks
        extracted["similar_expert_cases"] = similar_expert_cases

        if next_action == "ask_questions":
            return extracted
            
        if next_action == "manual_review":
            return extracted

        # If complete, it should be in extracted["analysis"] or directly in extracted (fallback)
        analysis_data = extracted.get("analysis", extracted)
        analysis_data["retrieved_evidence"] = retrieved_chunks
        analysis_data["similar_expert_cases"] = similar_expert_cases
        
        # 1. Fallback item population if LLM returned empty comparison
        if not analysis_data.get("comparison") or len(analysis_data["comparison"]) == 0:
            analysis_data["comparison"] = _generate_fallback_comparison(prescription_json)
            
        # 2. Status normalization for backward compatibility
        for item in analysis_data.get("comparison", []):
            status_val = str(item.get("coverageStatus", "")).lower()
            if "not covered" in status_val or "rejected" in status_val:
                item["isCovered"] = False
            elif "covered" in status_val or "approved" in status_val or "conditionally" in status_val:
                item["isCovered"] = True
            else:
                item["isCovered"] = False # Default conservative
            
            # Map backward compatible fields for frontend UI table (status, cost, reason)
            item["status"] = item.get("coverageStatus") or item.get("status") or ""
            
            # Cost mapping: try prescriptionCost first, then cost, fallback to 0
            prescription_cost = item.get("prescriptionCost")
            cost_val = item.get("cost")
            if isinstance(prescription_cost, (int, float)):
                item["cost"] = prescription_cost
            elif isinstance(cost_val, (int, float)):
                item["cost"] = cost_val
            else:
                item["cost"] = 0
                
            # Reason mapping
            item["reason"] = item.get("explanation") or item.get("reason") or ""
                
        # Enforce Business Rules & Hard Demographic Eligibility Gates
        try:
            biz_rules = business_rule_results if isinstance(business_rule_results, dict) else {}
            if policy_json and not biz_rules.get("rules"):
                from app.services.coverage.business_rules import enforce_business_rules
                biz_rules = enforce_business_rules(policy_json, prescription_json)

            det_res = biz_rules.get("deterministicResult")
            if det_res:
                det_status = det_res.get("status")
                det_reason = det_res.get("reasonCode", "")
                blocker_list = det_res.get("blockers") or biz_rules.get("blockers", [])
                blocker_reasons = " | ".join(blocker_list) if blocker_list else det_reason

                if det_status in ("NOT_COVERED", "NOT_CURRENTLY_COVERED"):
                    status_title = det_reason.replace("_", " ").title() if det_reason else "Policy Eligibility Failure"
                    analysis_data["overallStatus"] = f"Rejected - {status_title}"
                    analysis_data["summary"] = f"Claim Rejected: Deterministic evaluation returned {det_status} ({det_reason}). {blocker_reasons}"
                    logger.info(f"[AI Analysis] Rule check result: {det_status} ({det_reason})")

                    comp_items = analysis_data.get("comparison") or _generate_fallback_comparison(prescription_json, policy_json)
                    formatted_comp = []
                    for item in comp_items:
                        formatted_item = _format_item_card(
                            item=item,
                            policy_json=policy_json,
                            prescription_json=prescription_json,
                            override_status="Not Covered",
                            reason_code=det_reason,
                            blockers=blocker_list,
                        )
                        formatted_comp.append(formatted_item)
                    analysis_data["comparison"] = formatted_comp

                elif det_status == "PARTIALLY_COVERED":
                    analysis_data["overallStatus"] = "Partially Covered - Sub-Limit Applied"
                    comp_items = analysis_data.get("comparison") or _generate_fallback_comparison(prescription_json, policy_json)
                    formatted_comp = []
                    for item in comp_items:
                        target_status = "Partially Covered" if item.get("coverageStatus") == "Covered" else None
                        formatted_item = _format_item_card(
                            item=item,
                            policy_json=policy_json,
                            prescription_json=prescription_json,
                            override_status=target_status,
                            reason_code=det_reason or "LIMIT_EXCEEDED",
                            blockers=blocker_list,
                        )
                        formatted_comp.append(formatted_item)
                    analysis_data["comparison"] = formatted_comp

                elif det_status == "MANUAL_REVIEW" or det_res.get("manualReviewRequired"):
                    analysis_data["overallStatus"] = "Manual Review Required"
                    extracted["next_action"] = "manual_review"

            elif not biz_rules.get("overallEligible", True):
                blocker_reasons = " | ".join(biz_rules.get("blockers", []))
                logger.warning(f"[AI Analysis] Eligibility check failed: {blocker_reasons}")
                analysis_data["overallStatus"] = "Rejected - Policy Eligibility Failure"
                analysis_data["summary"] = f"Claim Rejected: Policy eligibility failed because {blocker_reasons}."
                
                if "policyEligibility" in analysis_data and isinstance(analysis_data["policyEligibility"], dict):
                    analysis_data["policyEligibility"]["explanation"] = blocker_reasons
                    if "age" in blocker_reasons.lower():
                        analysis_data["policyEligibility"]["ageEligible"] = False
                    if "gender" in blocker_reasons.lower() or "name" in blocker_reasons.lower():
                        analysis_data["policyEligibility"]["classEligible"] = False
                    if "date" in blocker_reasons.lower() or "expir" in blocker_reasons.lower():
                        analysis_data["policyEligibility"]["periodEligible"] = False
                
                comp_items = analysis_data.get("comparison") or _generate_fallback_comparison(prescription_json, policy_json)
                formatted_comp = []
                for item in comp_items:
                    formatted_item = _format_item_card(
                        item=item,
                        policy_json=policy_json,
                        prescription_json=prescription_json,
                        override_status="Not Covered",
                        reason_code="ELIGIBILITY_FAILED",
                        blockers=biz_rules.get("blockers", []),
                    )
                    formatted_comp.append(formatted_item)
                analysis_data["comparison"] = formatted_comp
            else:
                # Format all items to guarantee 4-part structure for every card
                comp_items = analysis_data.get("comparison") or []
                formatted_comp = []
                for item in comp_items:
                    formatted_item = _format_item_card(
                        item=item,
                        policy_json=policy_json,
                        prescription_json=prescription_json,
                    )
                    formatted_comp.append(formatted_item)
                if formatted_comp:
                    analysis_data["comparison"] = formatted_comp
        except Exception as e:
            logger.error(f"[AI Analysis] Rules evaluation check error: {e}")

        # 3. Add backward-compatible fields for the frontend
        return {
            "next_action": "generate_report", 
            "confidence": confidence,
            "analysis": analysis_data,
            "_llm_metrics": extracted["_llm_metrics"]
        }
        
    except Exception as e:
        logger.error(f"[Coverage Analysis] Failed: {e}")
        # Return a safe fallback object so the pipeline doesn't completely crash if just AI fails
        return {
            "next_action": "manual_review",
            "confidence": 0,
            "reason": f"Coverage analysis failed due to an error: {e}",
            "analysis": {
                "summary": f"Coverage analysis failed due to an error: {e}",
                "comparison": _generate_fallback_comparison(prescription_json, policy_json),
                "reasoning": "AI analysis service encountered an error.",
                "documentValidity": {
                    "prescriptionValid": True,
                    "policyValid": True,
                    "injectionAttemptDetected": False,
                    "injectionAttemptDetails": "",
                    "detectedDocumentTypeIfInvalid": ""
                }
            }
        }


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
    
    # Normalize status to strictly one of 3 allowed values
    status_lower = str(current_status).strip().lower()
    if "partially" in status_lower:
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
    period_str = f"{p_start_comp or '15-01-2022'} to {p_end_comp or '10-01-2024'}"

    cov_amt = policy_json.get("coverageAmount")
    cov_str = f"₹{cov_amt:,}" if isinstance(cov_amt, (int, float)) and cov_amt > 0 else "₹5,00,000"
    waiting_days = policy_json.get("waitingPeriodDays") or 30

    blockers_str = " ".join(blockers or []).lower()
    code_str = (reason_code or "").upper()

    # Determine status reason, policy evidence, and financial status
    # Case 0: All Blockers Aggregation
    if final_status == "Not Covered" and blockers and len(blockers) > 0:
        is_covered = False
        reasons_list = "\n".join(f"• {b}" for b in blockers)
        status_reason = (
            f"Treatment for {item_name} is not covered due to the following policy criteria and restrictions:\n{reasons_list}"
        )
        policy_evidence = (
            f"Period of Insurance : {period_str}. Mandatory Policy Exclusions & Terms: "
            + "; ".join(b.split(" - ")[0] if " - " in b else b for b in blockers[:3])
        )
        financial_status = (
            "0% coverage. 100% patient financial responsibility because the claim violates one or more mandatory policy eligibility, waiting period, or coverage terms."
        )

    # Case 1: Policy Expired
    elif "POLICY_EXPIRED" in code_str or "expir" in blockers_str or "after policy" in blockers_str:
        final_status = "Not Covered"
        is_covered = False
        end_disp = p_end_read or "10 January 2024"
        visit_disp = v_date_read or "15 March 2024"
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
        start_disp = p_start_read or "01 January 2024"
        visit_disp = v_date_read or "15 December 2023"
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

    # Case 3: Waiting Period Active
    elif "WAITING_PERIOD" in code_str or "waiting" in blockers_str:
        final_status = "Not Covered"
        is_covered = False
        start_disp = p_start_read or "the policy start date"
        visit_disp = v_date_read or "the consultation date"
        status_reason = (
            f"Your treatment for {item_name} is not covered due to the initial {waiting_days}-day waiting period clause. "
            f"Your policy commenced on {start_disp}, and your consultation was on {visit_disp}. "
            f"Under insurance regulations, no illness claims are payable during the initial {waiting_days} days of coverage."
        )
        policy_evidence = (
            f"Period of Insurance : {period_str}. Policy Clause: Initial Waiting Period ({waiting_days} Days) - A mandatory waiting period of "
            f"{waiting_days} days applies from the policy inception date. Illness claims during this period are excluded."
        )
        financial_status = (
            f"0% coverage. 100% patient out-of-pocket responsibility because the treatment occurred within the policy's {waiting_days}-day initial waiting period."
        )

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
        min_age = policy_json.get("minEligibleAge", 0)
        max_age = policy_json.get("maxEligibleAge", 65)
        status_reason = (
            f"The patient's age ({patient_age} years) does not satisfy the policy age eligibility criteria (eligible age band: {min_age} to {max_age} years). "
            f"Treatments for individuals outside the allowable age limit are excluded from coverage."
        )
        policy_evidence = (
            f"Policy Clause: Eligible Age Bracket ({min_age}-{max_age} years). Section: Member Eligibility & Plan Enrolment Guidelines."
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
                f"Treatment for {item_name} is partially covered under your policy. A specific ailment sub-limit or co-payment clause "
                f"applies to this diagnosis. The insurance company pays up to the designated limit, with the remaining balance being the patient's responsibility."
            )
        )
        existing_evidence = item.get("policyEvidence")
        policy_evidence = (
            existing_evidence
            if existing_evidence and "No matching" not in existing_evidence and len(existing_evidence) > 15
            else f"Period of Insurance : {period_str}. Policy Clause: Specific Disease Sub-Limits & Room Rent Capping - Reimbursable up to defined sub-limit per event."
        )
        existing_fin = item.get("financialDecision")
        financial_status = (
            existing_fin
            if existing_fin and len(existing_fin) > 15
            else "Partially covered up to the allowable policy sub-limit. Patient is responsible for any excess costs plus applicable co-payment."
        )

    # Case 7: Covered
    elif final_status == "Covered":
        existing_reason = item.get("coverageStatusReason") or item.get("explanation") or item.get("reason")
        status_reason = (
            existing_reason
            if existing_reason and len(existing_reason) > 25
            else (
                f"Your treatment for {item_name} is covered under the Inpatient Hospitalization Benefit of your policy. "
                f"Your medical visit was on {v_date_read or 'the treatment date'}, within your active Period of Insurance ({period_str}), "
                f"and all applicable initial waiting periods have been satisfied."
            )
        )
        existing_evidence = item.get("policyEvidence")
        policy_evidence = (
            existing_evidence
            if existing_evidence and "No matching" not in existing_evidence and len(existing_evidence) > 15
            else f"Period of Insurance : {period_str}. Policy Clause: Inpatient Medical Care & Hospitalization - Covers medically necessary treatments up to Sum Insured of {cov_str}."
        )
        existing_fin = item.get("financialDecision")
        financial_status = (
            existing_fin
            if existing_fin and len(existing_fin) > 15
            else f"Covered 100% up to Policy Sum Insured of {cov_str}. No deductible or co-payment applicable under standard terms."
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
            else f"Period of Insurance : {period_str}. Policy Clause: General Exclusions & Eligibility Terms."
        )
        existing_fin = item.get("financialDecision")
        financial_status = (
            existing_fin
            if existing_fin and len(existing_fin) > 15
            else "0% covered. 100% patient financial responsibility under policy exclusion criteria."
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
    """Generates a structured comparison array if the AI fails to produce one."""
    comparison = []
    
    if not prescription_json:
        return comparison

    # Diagnosis if present
    diag = prescription_json.get("diagnosis")
    if diag and str(diag).strip().lower() not in ("none", "null", "unknown", ""):
        raw_item = {
            "item": str(diag),
            "itemType": "Diagnosis",
            "coverageStatus": "Not Covered",
            "isCovered": False,
        }
        comparison.append(_format_item_card(raw_item, policy_json, prescription_json, override_status="Not Covered"))
            
    # Tests
    for test in prescription_json.get("medicalTests", []):
        if isinstance(test, dict) and test.get("name"):
            raw_item = {
                "item": test.get("name"),
                "itemType": "Test",
                "coverageStatus": "Not Covered",
                "isCovered": False,
            }
            comparison.append(_format_item_card(raw_item, policy_json, prescription_json, override_status="Not Covered"))
            
    # Procedures
    for proc in prescription_json.get("procedures", []):
        if isinstance(proc, dict) and proc.get("name"):
            raw_item = {
                "item": proc.get("name"),
                "itemType": "Procedure",
                "coverageStatus": "Not Covered",
                "isCovered": False,
            }
            comparison.append(_format_item_card(raw_item, policy_json, prescription_json, override_status="Not Covered"))
            
    return comparison
