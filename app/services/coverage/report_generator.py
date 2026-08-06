from datetime import datetime


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

    comparison = coverage_analysis.get("comparison", [])
    
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

    policy_summary = {
        "company": policy_json.get("insuranceCompany", "Unknown"),
        "policyName": policy_json.get("policyName", "Unknown"),
        "policyNumber": policy_json.get("policyNumber", "Unknown"),
        "policyType": policy_json.get("policyType", "Unknown"),
        "coverageAmount": policy_json.get("coverageAmount", 0),
        "status": policy_status
    }

    is_manual_rx = bool(
        prescription_json.get("isManual")
        or prescription_json.get("prescriptionSource") == "Self-entered Prescription"
        or prescription_json.get("manualText")
    )
    prescription_source = "Self-entered Prescription" if is_manual_rx else prescription_json.get("prescriptionSource", "PDF Upload")

    prescription_summary = {
        "patientName": prescription_json.get("patientName", "Unknown"),
        "hospital": prescription_json.get("hospitalName", "Unknown"),
        "doctor": prescription_json.get("doctorName", "Unknown"),
        "diagnosis": prescription_json.get("diagnosis", "Unknown"),
        "hospitalizationRequired": prescription_json.get("hospitalizationRequired"),
        "isManual": is_manual_rx,
        "prescriptionSource": prescription_source,
        "manualText": prescription_json.get("manualText")
    }

    matched_items = coverage_analysis.get("coveredTreatments", [c["item"] for c in comparison if c.get("isCovered")]) if not is_doc_invalid else []
    excluded_items = coverage_analysis.get("excludedTreatments", [c["item"] for c in comparison if not c.get("isCovered")]) if not is_doc_invalid else []

    if not is_doc_invalid:
        summary_text = (
            f"Analysis complete with status: {overall_status}. "
            f"Patient {prescription_summary['patientName']} diagnosed with {prescription_summary['diagnosis']}. "
            f"{len(matched_items)} items are covered, while {len(excluded_items)} items are not covered or excluded. "
            f"Dominance Score: {dominance_score}."
        )

    recommendations = coverage_analysis.get("recommendation", "")
    if coverage_analysis.get("nextSteps") and isinstance(coverage_analysis["nextSteps"], list):
        recommendations += " " + " ".join(coverage_analysis["nextSteps"])

    final_doc_validity = {
        "prescriptionValid": rx_valid,
        "policyValid": p_valid,
        "isPrescriptionValid": rx_valid,
        "isPolicyValid": p_valid,
        "injectionAttemptDetected": doc_validity.get("injectionAttemptDetected", False),
        "injectionAttemptDetails": doc_validity.get("injectionAttemptDetails", ""),
        "detectedDocumentTypeIfInvalid": doc_validity.get("detectedDocumentTypeIfInvalid", "")
    }

    return {
        "documentValidity": final_doc_validity,
        "overallStatus": overall_status,
        "dominanceScore": dominance_score,
        "coverageBreakdown": coverage_breakdown,
        "summary": summary_text if is_doc_invalid else coverage_analysis.get("summary", summary_text),
        "coverageStatus": overall_status,
        "policySummary": policy_summary,
        "prescriptionSummary": prescription_summary,
        "matchedItems": matched_items,
        "excludedItems": excluded_items,
        "applicableClauses": coverage_analysis.get("matchedPolicyClauses", []),
        "blockedClauses": coverage_analysis.get("blockedPolicyClauses", []),
        "businessRuleResults": business_rule_results,
        "recommendations": recommendations.strip(),
        "reasoning": coverage_analysis.get("reasoning", ""),
        "processingTimeMs": processing_time_ms,
        "analysisVersion": "2.0.0",
        "summaryText": summary_text,
        "comparison": comparison,
        "isManualPrescription": is_manual_rx,
        "prescriptionSource": prescription_source,
        "prescriptionJson": prescription_json,
        "policyJson": policy_json
    }
