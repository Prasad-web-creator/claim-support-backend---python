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
    policy_json: dict = None
) -> dict:
    logger.info("[Coverage Analysis] Starting AI coverage analysis...")

    # Build the prompt
    user_prompt = f"""
PRESCRIPTION TEXT

{prescription_text}

==================================================

POLICY TEXT

{policy_text}

==================================================

BUSINESS RULE RESULTS

{json.dumps(business_rule_results)}

==================================================

PREVIOUS CLARIFICATION HISTORY
(Use this to avoid asking duplicate questions. If a question is answered here, DO NOT ask it again.)

{json.dumps(clarification_history or [], indent=2)}
"""

    try:
        # Increase token limit as analysis outputs can be quite large (up to 8000 tokens)
        result = await extract_json_with_retry(
            system_prompt=COVERAGE_ANALYSIS_SYSTEM_PROMPT,
            user_content=user_prompt,
            max_tokens=8192
        )
        
        extracted = result["extractedJson"]
        timing = result.get("timing", {})
        tokens = result.get("tokens", {})
        
        logger.info("[Coverage Analysis] Success.")
        logger.info(f"[Coverage Analysis] Stage 8 Performance Profiling:\n"
                    f"  - Prompt Prep: {timing.get('promptPreparationSec', 0)}s\n"
                    f"  - LLM Processing: {timing.get('llmProcessingSec', 0)}s\n"
                    f"  - JSON Parsing: {timing.get('jsonParsingSec', 0)}s\n"
                    f"  - Input Tokens: {tokens.get('promptTokens', 0)}\n"
                    f"  - Output Tokens: {tokens.get('completionTokens', 0)}\n"
                    f"  - Total Tokens: {tokens.get('totalTokens', 0)}")
        
        # Parse next_action
        next_action = extracted.get("next_action")
        confidence = extracted.get("confidence", 0)

        # Append raw tokens and timing to the extracted dict so it can be used for audit logging later
        extracted["_llm_metrics"] = {
            "timing": timing,
            "tokens": tokens
        }

        if next_action == "ask_questions":
            return extracted
            
        if next_action == "manual_review":
            return extracted

        # If complete, it should be in extracted["analysis"] or directly in extracted (fallback)
        analysis_data = extracted.get("analysis", extracted)
        
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

            if not biz_rules.get("overallEligible", True):
                blocker_reasons = " | ".join(biz_rules.get("blockers", []))
                logger.warning(f"[Coverage Analysis] Business rules eligibility failed: {blocker_reasons}")
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
                
                for item in analysis_data.get("comparison", []):
                    item["coverageStatus"] = "Not Covered"
                    item["status"] = "Not Covered"
                    item["isCovered"] = False
                    item["reason"] = f"Not Covered: Policy eligibility failed ({blocker_reasons})"
                    item["explanation"] = f"Not Covered: Policy eligibility failed ({blocker_reasons})"
        except Exception as e:
            logger.error(f"[Coverage Analysis] Business rules check error: {e}")

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
                "comparison": _generate_fallback_comparison(prescription_json),
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


def _generate_fallback_comparison(prescription_json: dict) -> list[dict]:
    """Generates a generic comparison array if the AI fails to produce one."""
    comparison = []
    
    if not prescription_json:
        return comparison
            
    # Tests
    for test in prescription_json.get("medicalTests", []):
        if isinstance(test, dict) and test.get("name"):
            comparison.append({
                "item": test.get("name"),
                "itemType": "Test",
                "coverageStatus": "Not Covered",
                "isCovered": False,
                "reasoning": "AI analysis failed to process this item. Marked as Not Covered for safety."
            })
            
    # Procedures
    for proc in prescription_json.get("procedures", []):
         if isinstance(proc, dict) and proc.get("name"):
            comparison.append({
                "item": proc.get("name"),
                "itemType": "Procedure",
                "coverageStatus": "Not Covered",
                "isCovered": False,
                "reasoning": "AI analysis failed to process this item. Marked as Not Covered for safety."
            })
            
    return comparison
