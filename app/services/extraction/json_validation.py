"""
JSON Validation Service — migrated from JsonValidationService.js.
Validates extracted JSON objects (Stage 6).
"""

import json


def sanitize_array_of_objects(arr, default_key="name") -> list[dict] | None:
    """AI Robustness Sanitization: Ensures lists contain valid dicts."""
    if arr is None:
        return None

    # Detect and parse JSON strings
    if isinstance(arr, str):
        try:
            parsed = json.loads(arr)
            if isinstance(parsed, (list, dict)):
                arr = parsed
            else:
                return [{default_key: arr, "cost": None}]
        except Exception:
            return [{default_key: arr, "cost": None}]

    if not isinstance(arr, list):
        return [{default_key: str(arr), "cost": None}]

    # Flatten nested arrays (limited depth for simplicity)
    flat_arr = []
    for item in arr:
        if isinstance(item, list):
            flat_arr.extend(item)
        else:
            flat_arr.append(item)

    sanitized = []
    for item in flat_arr:
        if isinstance(item, str):
            sanitized.append({default_key: item, "cost": None})
        elif isinstance(item, dict):
            sanitized.append(item)
        else:
            sanitized.append({default_key: str(item), "cost": None})

    return sanitized


def validate_extracted_json(policy_json: dict | None, prescription_json: dict | None) -> dict:
    """
    Validates the extracted policy and prescription JSON objects.
    """
    errors = []

    if not policy_json or not isinstance(policy_json, dict):
        errors.append("policyJson is missing or not an object")

    if not prescription_json or not isinstance(prescription_json, dict):
        errors.append("prescriptionJson is missing or not an object")

    if errors:
        return {
            "isValid": False,
            "errors": errors,
            "validatedPolicyJson": policy_json,
            "validatedPrescriptionJson": prescription_json,
        }

    # Validate Policy JSON required fields
    required_policy_fields = ["insuranceCompany", "coveredTreatments", "excludedTreatments"]
    for field in required_policy_fields:
        if field not in policy_json:
            errors.append(f"policyJson is missing required field: {field}")

    # Validate Prescription JSON required fields
    required_prescription_fields = ["diagnosis"]
    for field in required_prescription_fields:
        if field not in prescription_json:
            errors.append(f"prescriptionJson is missing required field: {field}")

    # Type validation
    if policy_json.get("coveredTreatments") is not None and not isinstance(
        policy_json["coveredTreatments"], list
    ):
        errors.append("policyJson.coveredTreatments must be an array or null")

    if policy_json.get("excludedTreatments") is not None and not isinstance(
        policy_json["excludedTreatments"], list
    ):
        errors.append("policyJson.excludedTreatments must be an array or null")

    # Sanitize Prescription arrays
    prescription_json["medicalTests"] = sanitize_array_of_objects(
        prescription_json.get("medicalTests"), "name"
    )
    prescription_json["procedures"] = sanitize_array_of_objects(
        prescription_json.get("procedures"), "name"
    )
    
    # -------------------------------------------------------------
    # STRUCTURAL NORMALIZATION (Bugfix for NoneType iteration)
    # Ensure any optional list fields that come back from the LLM as 
    # 'null' (None) are defensively converted to an empty list [].
    # -------------------------------------------------------------
    known_policy_lists = [
        "coveredDiseases", "excludedDiseases", "coveredTreatments",
        "excludedTreatments", "waitingPeriods", "subLimits",
        "coPaymentRules", "riders", "coverages", "exclusions"
    ]
    known_rx_lists = [
        "medicines", "labInvestigations", "medicalTests", 
        "procedures", "recommendedTests"
    ]
    
    for field in known_policy_lists:
        if policy_json.get(field) is None:
            policy_json[field] = []
            
    for field in known_rx_lists:
        if prescription_json.get(field) is None:
            prescription_json[field] = []

    return {
        "isValid": len(errors) == 0,
        "errors": errors,
        "validatedPolicyJson": policy_json,
        "validatedPrescriptionJson": prescription_json,
    }
