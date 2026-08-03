"""
Extraction Validator — migrated from extractionValidator.js.
Handles strict schema validation, confidence scoring, and generating warnings.
"""


def is_populated(val) -> bool:
    """Check if a value is meaningfully populated (not null, empty, or an AI default)."""
    if val is None:
        return False
    if isinstance(val, str) and val.strip() == "":
        return False
    if isinstance(val, list) and len(val) == 0:
        return False

    # Check for invalid AI default strings
    if isinstance(val, str):
        lower = val.lower().strip()
        if lower in ("unknown", "n/a", "none", "not specified"):
            return False

    return True


def validate_extraction(
    extracted_json: dict | None,
    required_fields: list[str],
    all_expected_fields: list[str],
) -> dict:
    """
    Validates extraction results against a schema and calculates confidence.

    Args:
        extracted_json: The JSON object from AI.
        required_fields: Fields that MUST be present for extraction to be valid.
        all_expected_fields: All fields expected in the document for confidence scoring.

    Returns:
        Validation result dict with isValid, confidence, errors, warnings, cleanedJson.
    """
    if not extracted_json or not isinstance(extracted_json, dict):
        return {
            "isValid": False,
            "confidence": 0,
            "warnings": ["Extracted JSON is missing or invalid object type."],
            "errors": ["Invalid root object"],
            "cleanedJson": extracted_json,
        }

    errors: list[str] = []
    warnings: list[str] = []

    # Clean up "Unknown" fields to None
    for key in list(extracted_json.keys()):
        if not is_populated(extracted_json[key]):
            extracted_json[key] = None

    # Check required fields
    for field in required_fields:
        if not is_populated(extracted_json.get(field)):
            errors.append(f"Missing required field: {field}")

    # Calculate confidence based on expected fields population
    populated_count = 0
    for field in all_expected_fields:
        if is_populated(extracted_json.get(field)):
            populated_count += 1
        else:
            warnings.append(f"Field missing or unpopulated: {field}")

    # Basic confidence algorithm:
    # 50% weight to required fields (if valid, else 0)
    # 50% weight to overall fields populated
    required_score = 50 if len(errors) == 0 else max(0, 50 - (len(errors) * 10))

    if len(all_expected_fields) > 0:
        overall_ratio = populated_count / len(all_expected_fields)
    else:
        overall_ratio = 0.0
    overall_score = round(overall_ratio * 50)

    confidence = required_score + overall_score

    return {
        "isValid": len(errors) == 0,
        "confidence": confidence,
        "errors": errors,
        "warnings": warnings,
        "cleanedJson": extracted_json,
    }
