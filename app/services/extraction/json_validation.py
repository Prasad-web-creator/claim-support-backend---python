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


def _is_valid_value(val) -> bool:
    """Checks if a string or list field is meaningfully populated."""
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


def validate_extracted_json(policy_json: dict | None, prescription_json: dict | None) -> dict:
    """
    Validates the extracted policy and prescription JSON objects.
    """
    errors = []
    is_policy_valid = True
    is_prescription_valid = True

    if not policy_json or not isinstance(policy_json, dict):
        errors.append("policyJson is missing or not an object")
        is_policy_valid = False
        policy_json = {}

    if not prescription_json or not isinstance(prescription_json, dict):
        errors.append("prescriptionJson is missing or not an object")
        is_prescription_valid = False
        prescription_json = {}

    # Validate Policy JSON based on substantive insurance content (metadata fields are optional)
    if is_policy_valid:
        has_covered_treatments = _is_valid_value(policy_json.get("coveredTreatments"))
        has_excluded_treatments = _is_valid_value(policy_json.get("excludedTreatments"))
        has_covered_diseases = _is_valid_value(policy_json.get("coveredDiseases"))
        has_excluded_diseases = _is_valid_value(policy_json.get("excludedDiseases"))
        has_coverages = _is_valid_value(policy_json.get("coverages"))
        has_exclusions = _is_valid_value(policy_json.get("exclusions"))
        has_hospitalization = _is_valid_value(policy_json.get("hospitalization"))
        has_icu = _is_valid_value(policy_json.get("icu"))
        has_room_eligibility = _is_valid_value(policy_json.get("roomEligibility"))
        has_medicines_cov = _is_valid_value(policy_json.get("medicinesCoverage"))
        has_tests_cov = _is_valid_value(policy_json.get("medicalTestsCoverage"))
        has_special_conds = _is_valid_value(policy_json.get("specialConditions"))
        has_waiting_period = _is_valid_value(policy_json.get("waitingPeriodDays")) or _is_valid_value(policy_json.get("waitingPeriods"))
        has_copay_or_ded = _is_valid_value(policy_json.get("coPay")) or _is_valid_value(policy_json.get("deductibles")) or _is_valid_value(policy_json.get("coPaymentRules"))
        has_sublimits = _is_valid_value(policy_json.get("subLimits")) or _is_valid_value(policy_json.get("riders"))
        has_policy_type = _is_valid_value(policy_json.get("policyType"))
        has_metadata = _is_valid_value(policy_json.get("insuranceCompany")) or _is_valid_value(policy_json.get("policyNumber")) or _is_valid_value(policy_json.get("policyName")) or _is_valid_value(policy_json.get("coverageAmount"))

        has_insurance_content = (
            has_covered_treatments
            or has_excluded_treatments
            or has_covered_diseases
            or has_excluded_diseases
            or has_coverages
            or has_exclusions
            or has_hospitalization
            or has_icu
            or has_room_eligibility
            or has_medicines_cov
            or has_tests_cov
            or has_special_conds
            or has_waiting_period
            or has_copay_or_ded
            or has_sublimits
            or has_policy_type
            or has_metadata
        )

        if not has_insurance_content:
            errors.append("policyJson contains no recognizable insurance policy clauses, treatments, or coverage rules")
            is_policy_valid = False

    # Validate Prescription JSON
    if is_prescription_valid:
        diag_ok = _is_valid_value(prescription_json.get("diagnosis"))
        has_meds = bool(prescription_json.get("medicines"))
        has_tests = bool(prescription_json.get("medicalTests") or prescription_json.get("labInvestigations"))
        has_procs = bool(prescription_json.get("procedures"))
        has_symptoms = bool(prescription_json.get("symptoms"))

        if not diag_ok and not has_meds and not has_tests and not has_procs and not has_symptoms:
            errors.append("prescriptionJson has no valid diagnosis, medicines, medical tests, or procedures")
            is_prescription_valid = False

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
        "isValid": is_policy_valid and is_prescription_valid,
        "isPolicyValid": is_policy_valid,
        "isPrescriptionValid": is_prescription_valid,
        "errors": errors,
        "validatedPolicyJson": policy_json,
        "validatedPrescriptionJson": prescription_json,
    }
