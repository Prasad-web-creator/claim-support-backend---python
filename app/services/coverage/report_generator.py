from datetime import datetime
from app.services.coverage.reference_benchmark_service import generate_reference_comparison


def _is_meaningful(val) -> bool:
    """Check if value is meaningful (not None, empty, None string, Unknown, N/A)."""
    if val is None:
        return False
    if isinstance(val, str):
        cleaned = val.strip().lower()
        if not cleaned or cleaned in ("unknown", "none", "n/a", "null", "not specified", "undefined"):
            return False
        return True
    if isinstance(val, (list, dict)):
        return len(val) > 0
    return True


def generate_report(
    policy_json: dict | None,
    prescription_json: dict | None,
    business_rule_results: dict | None,
    coverage_analysis: dict | None,
    processing_time_ms: int
) -> dict:
    """
    Generates the final structured coverage summary report based on all pipeline outputs.
    """
    policy_json = policy_json or {}
    prescription_json = prescription_json or {}
    business_rule_results = business_rule_results or {}
    coverage_analysis = coverage_analysis or {}
    if isinstance(coverage_analysis.get("analysis"), dict):
        raw_analysis = coverage_analysis["analysis"]
        for k, v in raw_analysis.items():
            if k not in coverage_analysis or coverage_analysis[k] is None:
                coverage_analysis[k] = v
        if "comparison" in raw_analysis and not coverage_analysis.get("comparison"):
            coverage_analysis["comparison"] = raw_analysis["comparison"]
        if "overallStatus" in raw_analysis and not coverage_analysis.get("overallStatus"):
            coverage_analysis["overallStatus"] = raw_analysis["overallStatus"]

    # Check document validity from explicit flags if present
    doc_validity = coverage_analysis.get("documentValidity") or {}
    p_valid = doc_validity.get("policyValid", True)
    rx_valid = doc_validity.get("prescriptionValid", True)

    # Heuristic content checks for Policy (metadata fields are optional)
    has_policy_content = (
        _is_meaningful(policy_json.get("coveredTreatments"))
        or _is_meaningful(policy_json.get("excludedTreatments"))
        or _is_meaningful(policy_json.get("coveredDiseases"))
        or _is_meaningful(policy_json.get("excludedDiseases"))
        or _is_meaningful(policy_json.get("coverages"))
        or _is_meaningful(policy_json.get("exclusions"))
        or _is_meaningful(policy_json.get("hospitalization"))
        or _is_meaningful(policy_json.get("icu"))
        or _is_meaningful(policy_json.get("roomEligibility"))
        or _is_meaningful(policy_json.get("medicinesCoverage"))
        or _is_meaningful(policy_json.get("medicalTestsCoverage"))
        or _is_meaningful(policy_json.get("specialConditions"))
        or _is_meaningful(policy_json.get("waitingPeriodDays"))
        or _is_meaningful(policy_json.get("waitingPeriods"))
        or _is_meaningful(policy_json.get("coPay"))
        or _is_meaningful(policy_json.get("deductibles"))
        or _is_meaningful(policy_json.get("subLimits"))
        or _is_meaningful(policy_json.get("policyType"))
        or _is_meaningful(policy_json.get("insuranceCompany"))
        or _is_meaningful(policy_json.get("policyNumber"))
        or _is_meaningful(policy_json.get("policyName"))
        or _is_meaningful(policy_json.get("coverageAmount"))
    )
    if not has_policy_content:
        p_valid = False

    # Heuristic content checks for Prescription
    diag_ok = _is_meaningful(prescription_json.get("diagnosis"))
    has_meds = _is_meaningful(prescription_json.get("medicines"))
    has_tests = _is_meaningful(prescription_json.get("medicalTests")) or _is_meaningful(prescription_json.get("labInvestigations"))
    has_procs = _is_meaningful(prescription_json.get("procedures"))
    has_symptoms = _is_meaningful(prescription_json.get("symptoms"))
    if not diag_ok and not has_meds and not has_tests and not has_procs and not has_symptoms:
        rx_valid = False

    from app.services.coverage.coverage_analysis import _format_item_card, _generate_fallback_comparison

    raw_comparison = coverage_analysis.get("comparison", [])
    if not raw_comparison and p_valid and rx_valid:
        raw_comparison = _generate_fallback_comparison(prescription_json, policy_json)

    comparison = []
    for item in raw_comparison:
        comparison.append(_format_item_card(item, policy_json, prescription_json))
    
    # Calculate Coverage Breakdown
    total_items = len(comparison)
    covered_count = 0
    partially_covered_count = 0
    not_covered_count = 0
    
    for item in comparison:
        status = item.get("coverageStatus", "")
        if status == "Covered":
            covered_count += 1
        elif status == "Partially Covered":
            partially_covered_count += 1
        elif status == "Not Covered":
            not_covered_count += 1

    overall_status = coverage_analysis.get("overallStatus") or "Unknown"
    dominance_score = 0.0

    if not p_valid and not rx_valid:
        overall_status = "Invalid Policy and Prescription"
    elif not p_valid:
        overall_status = "Invalid Policy"
    elif not rx_valid:
        overall_status = "Invalid Prescription"
    elif total_items > 0:
        if not_covered_count >= partially_covered_count and not_covered_count >= covered_count:
            overall_status = "Not Covered"
            dominance_score = (not_covered_count / total_items) * 100
        elif partially_covered_count >= covered_count:
            overall_status = "Partially Covered"
            dominance_score = (partially_covered_count / total_items) * 100
        else:
            overall_status = "Covered"
            dominance_score = (covered_count / total_items) * 100
    else:
        # total_items == 0 with no clear decision
        if not rx_valid:
            overall_status = "Invalid Prescription"
        elif not p_valid:
            overall_status = "Invalid Policy"
        else:
            overall_status = "Not Covered"
            
    dominance_score = round(dominance_score, 2)
    
    is_doc_invalid = (not p_valid) or (not rx_valid) or str(overall_status).startswith("Invalid")

    all_blockers = []
    if business_rule_results.get("blockers"):
        for b in business_rule_results.get("blockers"):
            if b not in all_blockers:
                all_blockers.append(b)
    det_res = business_rule_results.get("deterministicResult") or {}
    if det_res.get("blockers"):
        for b in det_res.get("blockers"):
            if b not in all_blockers:
                all_blockers.append(b)

    is_blocked = (
        (business_rule_results.get("overallEligible") is False)
        or (det_res.get("status") in ("NOT_COVERED", "NOT_CURRENTLY_COVERED"))
        or (len(all_blockers) > 0)
    )

    if is_blocked and not is_doc_invalid:
        overall_status = "Not Covered"
        comparison = [
            _format_item_card(
                c,
                policy_json,
                prescription_json,
                override_status="Not Covered",
                reason_code=det_res.get("reasonCode") or "POLICY_EXCLUSION",
                blockers=all_blockers,
            )
            for c in comparison
        ]
        covered_count = 0
        partially_covered_count = 0
        not_covered_count = len(comparison)
        dominance_score = 100.0
    elif det_res and not is_doc_invalid:
        det_status = det_res.get("status")
        if det_status == "PARTIALLY_COVERED":
            overall_status = "Partially Covered"
            comparison = [
                _format_item_card(
                    c,
                    policy_json,
                    prescription_json,
                    override_status="Partially Covered" if c.get("coverageStatus") == "Covered" else None,
                    reason_code=det_res.get("reasonCode") or "LIMIT_EXCEEDED",
                    blockers=det_res.get("blockers"),
                )
                for c in comparison
            ]
        elif det_status == "MANUAL_REVIEW":
            overall_status = "Manual Review Required"

    if is_doc_invalid:
        comparison = []
        coverage_breakdown = {
            "covered": 0,
            "partiallyCovered": 0,
            "notCovered": 0,
            "total": 0
        }
        dominance_score = 0.0
        if not p_valid and not rx_valid:
            overall_status = "Invalid Policy and Prescription"
            summary_text = "The uploaded Policy and Prescription documents could not be analyzed because they are invalid, unreadable, or unsupported."
        elif not p_valid:
            overall_status = "Invalid Policy"
            summary_text = "The uploaded Policy document could not be analyzed because it is invalid, unreadable, or unsupported."
        else:
            overall_status = "Invalid Prescription"
            is_manual_rx = bool(
                prescription_json and (
                    prescription_json.get("isManual")
                    or prescription_json.get("prescriptionSource") == "Self-entered Prescription"
                    or prescription_json.get("manualText")
                )
            )
            if is_manual_rx:
                summary_text = "The self-entered prescription is not valid. Please provide valid medical details (such as diagnosis, symptoms, diseases, medicines, or medical tests) and try again."
            else:
                summary_text = "The uploaded Prescription document could not be analyzed because it is invalid, unreadable, or unsupported."
    else:
        coverage_breakdown = {
            "covered": covered_count,
            "partiallyCovered": partially_covered_count,
            "notCovered": not_covered_count,
            "total": total_items
        }

    # Determine status based on dates
    policy_status = "Active"
    if policy_json.get("policyEndDate"):
        end_date = None
        try:
            from dateutil import parser
            end_date = parser.parse(str(policy_json["policyEndDate"])).replace(tzinfo=None)
        except Exception:
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%Y/%m/%d", "%d.%m.%Y", "%d-%b-%Y"):
                try:
                    end_date = datetime.strptime(str(policy_json["policyEndDate"]).strip().split("T")[0], fmt)
                    break
                except Exception:
                    continue
        if end_date and end_date < datetime.now():
            policy_status = "Expired"

    policy_company = policy_json.get("insuranceCompany")
    if not policy_company or str(policy_company).strip().lower() in ("none", "null", "unknown", "unknown policy", "unknown company"):
        policy_company = "---"

    policy_name = policy_json.get("policyName")
    if not policy_name or str(policy_name).strip().lower() in ("none", "null", "unknown", "unknown policy"):
        policy_name = "---"

    policy_num = policy_json.get("policyNumber")
    if not policy_num or str(policy_num).strip().lower() in ("none", "null", "unknown"):
        policy_num = "---"

    policy_type = policy_json.get("policyType")
    if not policy_type or str(policy_type).strip().lower() in ("none", "null", "unknown"):
        policy_type = "---"

    policy_summary = {
        "company": policy_company,
        "policyName": policy_name,
        "policyNumber": policy_num,
        "policyType": policy_type,
        "coverageAmount": policy_json.get("coverageAmount", 0),
        "status": policy_status
    }

    is_manual_rx = bool(
        prescription_json.get("isManual")
        or prescription_json.get("prescriptionSource") == "Self-entered Prescription"
        or prescription_json.get("manualText")
    )
    prescription_source = "Self-entered Prescription" if is_manual_rx else prescription_json.get("prescriptionSource", "PDF Upload")

    # Clean fallback for diagnosis
    extracted_diag = prescription_json.get("diagnosis")
    if not extracted_diag or str(extracted_diag).strip().lower() in ("none", "null", "unknown"):
        symptoms_list = prescription_json.get("symptoms")
        if isinstance(symptoms_list, list) and len(symptoms_list) > 0:
            extracted_diag = ", ".join(str(s) for s in symptoms_list if s)
        elif prescription_json.get("manualText"):
            extracted_diag = prescription_json.get("manualText")
        else:
            extracted_diag = None

    p_name = prescription_json.get("patientName")
    if not p_name or str(p_name).strip().lower() in ("none", "null", "unknown", "unknown patient"):
        p_name = None

    h_name = prescription_json.get("hospitalName")
    if not h_name or str(h_name).strip().lower() in ("none", "null", "unknown", "unknown hospital"):
        h_name = None

    d_name = prescription_json.get("doctorName")
    if not d_name or str(d_name).strip().lower() in ("none", "null", "unknown", "unknown doctor"):
        d_name = None

    if extracted_diag and not prescription_json.get("diagnosis"):
        prescription_json["diagnosis"] = extracted_diag

    prescription_summary = {
        "patientName": p_name or "---",
        "hospital": h_name or "---",
        "doctor": d_name or "---",
        "diagnosis": extracted_diag or "---",
        "hospitalizationRequired": prescription_json.get("hospitalizationRequired"),
        "isManual": is_manual_rx,
        "prescriptionSource": prescription_source,
        "manualText": prescription_json.get("manualText")
    }

    if is_blocked and not is_doc_invalid:
        matched_items = []
        excluded_items = [c["item"] for c in comparison] if comparison else (coverage_analysis.get("excludedTreatments", []) + coverage_analysis.get("coveredTreatments", []))
        if all_blockers:
            blockers_formatted = "\n".join(f"• {b}" for b in all_blockers)
            summary_text = (
                f"Claim Assessment: Not Covered (0% Coverage).\n\n"
                f"The claim cannot be approved under policy terms due to the following non-coverage criteria and restrictions:\n"
                f"{blockers_formatted}"
            )
        else:
            summary_text = (
                f"Claim Assessment: Not Covered (0% Coverage). "
                f"The claim is not covered under the terms and conditions of this policy."
            )
    else:
        matched_items = coverage_analysis.get("coveredTreatments", [c["item"] for c in comparison if c.get("isCovered")]) if not is_doc_invalid else []
        excluded_items = coverage_analysis.get("excludedTreatments", [c["item"] for c in comparison if not c.get("isCovered")]) if not is_doc_invalid else []

        if not is_doc_invalid:
            patient_clause = f"Patient {p_name}" if p_name else "Member"
            diag_clause = f"diagnosed with {extracted_diag}" if extracted_diag else "requesting medical coverage"

            summary_text = (
                f"Analysis complete with status: {overall_status}. "
                f"{patient_clause} {diag_clause}. "
                f"{len(matched_items)} items are covered, while {len(excluded_items)} items are not covered or excluded. "
                f"Dominance Score: {dominance_score}."
            )

    recommendations = coverage_analysis.get("recommendation", "")
    if coverage_analysis.get("nextSteps") and isinstance(coverage_analysis["nextSteps"], list):
        recommendations += " " + " ".join(coverage_analysis["nextSteps"])

    policy_invalid_reason = ""
    prescription_invalid_reason = ""

    if not p_valid:
        policy_invalid_reason = (
            doc_validity.get("policyInvalidReason")
            or "The uploaded document contains no recognizable insurance policy clauses, covered treatments, benefit rules, or insurance terms."
        )
    if not rx_valid:
        if is_manual_rx:
            prescription_invalid_reason = (
                doc_validity.get("prescriptionInvalidReason")
                or "The self-entered text contains no recognizable medical details (no diagnosis, symptoms, diseases, medicines, or medical tests)."
            )
        else:
            prescription_invalid_reason = (
                doc_validity.get("prescriptionInvalidReason")
                or "The uploaded document contains no valid diagnosis, medicines, medical tests, procedures, or symptoms."
            )

    final_doc_validity = {
        "prescriptionValid": rx_valid,
        "policyValid": p_valid,
        "isPrescriptionValid": rx_valid,
        "isPolicyValid": p_valid,
        "policyInvalidReason": policy_invalid_reason,
        "prescriptionInvalidReason": prescription_invalid_reason,
        "errors": doc_validity.get("errors", []),
        "injectionAttemptDetected": doc_validity.get("injectionAttemptDetected", False),
        "injectionAttemptDetails": doc_validity.get("injectionAttemptDetails", ""),
        "detectedDocumentTypeIfInvalid": doc_validity.get("detectedDocumentTypeIfInvalid", "")
    }

    reference_comparison = generate_reference_comparison(
        policy_json=policy_json,
        prescription_json=prescription_json,
        coverage_analysis=coverage_analysis,
    )

    combined_blocked = list(coverage_analysis.get("blockedPolicyClauses", []))
    for b in all_blockers:
        if b not in combined_blocked:
            combined_blocked.append(b)

    return {
        "documentValidity": final_doc_validity,
        "overallStatus": overall_status,
        "dominanceScore": dominance_score,
        "coverageBreakdown": coverage_breakdown,
        "summary": summary_text if (is_doc_invalid or is_blocked) else coverage_analysis.get("summary", summary_text),
        "coverageStatus": overall_status,
        "policySummary": policy_summary,
        "prescriptionSummary": prescription_summary,
        "matchedItems": matched_items,
        "excludedItems": excluded_items,
        "applicableClauses": coverage_analysis.get("matchedPolicyClauses", []),
        "blockedClauses": combined_blocked,
        "businessRuleResults": business_rule_results,
        "deterministicResult": det_res,
        "decisionType": "Manual Review" if (det_res.get("manualReviewRequired") or det_res.get("status") == "MANUAL_REVIEW") else "Automatic",
        "recommendations": recommendations.strip(),
        "reasoning": coverage_analysis.get("reasoning", ""),
        "processingTimeMs": processing_time_ms,
        "analysisVersion": "2.0.0",
        "summaryText": summary_text,
        "comparison": comparison,
        "isManualPrescription": is_manual_rx,
        "prescriptionSource": prescription_source,
        "prescriptionJson": prescription_json,
        "policyJson": policy_json,
        "referenceComparison": reference_comparison,
    }
