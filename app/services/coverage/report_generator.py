"""
Report Generation Service — migrated from ReportGenerationService.js.
Generates the final structured coverage summary report.
"""

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
            
    # Determine Overall Status based on priority (1. Not Covered, 2. Partially Covered, 3. Covered)
    overall_status = "Unknown"
    dominance_score = 0.0
    
    if total_items > 0:
        if not_covered_count >= partially_covered_count and not_covered_count >= covered_count:
            overall_status = "Not Covered"
            dominance_score = (not_covered_count / total_items) * 100
        elif partially_covered_count >= covered_count:
            overall_status = "Partially Covered"
            dominance_score = (partially_covered_count / total_items) * 100
        else:
            overall_status = "Covered"
            dominance_score = (covered_count / total_items) * 100
            
    dominance_score = round(dominance_score, 2)
    
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

    prescription_summary = {
        "patientName": prescription_json.get("patientName", "Unknown"),
        "hospital": prescription_json.get("hospitalName", "Unknown"),
        "doctor": prescription_json.get("doctorName", "Unknown"),
        "diagnosis": prescription_json.get("diagnosis", "Unknown"),
        "hospitalizationRequired": prescription_json.get("hospitalizationRequired")
    }


    matched_items = coverage_analysis.get("coveredTreatments", [c["item"] for c in comparison if c.get("isCovered")])
    excluded_items = coverage_analysis.get("excludedTreatments", [c["item"] for c in comparison if not c.get("isCovered")])

    summary_text = (
        f"Analysis complete with status: {overall_status}. "
        f"Patient {prescription_summary['patientName']} diagnosed with {prescription_summary['diagnosis']}. "
        f"{len(matched_items)} items are covered, while {len(excluded_items)} items are not covered or excluded. "
        f"Dominance Score: {dominance_score}."
    )

    recommendations = coverage_analysis.get("recommendation", "")
    if coverage_analysis.get("nextSteps") and isinstance(coverage_analysis["nextSteps"], list):
        recommendations += " " + " ".join(coverage_analysis["nextSteps"])

    doc_validity_default = {
        "prescriptionValid": True,
        "policyValid": True,
        "injectionAttemptDetected": False,
        "injectionAttemptDetails": "",
        "detectedDocumentTypeIfInvalid": ""
    }

    return {
        "documentValidity": coverage_analysis.get("documentValidity", doc_validity_default),
        "overallStatus": overall_status,
        "dominanceScore": dominance_score,
        "coverageBreakdown": coverage_breakdown,
        "summary": coverage_analysis.get("summary", summary_text),
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
        "missingDocuments": coverage_analysis.get("missingDocuments", []),
        "processingTimeMs": processing_time_ms,
        "analysisVersion": "2.0.0",
        "summaryText": summary_text,
        "comparison": comparison
    }
