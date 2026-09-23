"""Null-sanitising shared by the analysis document models.

Persisted payloads (prescriptionJson, policyJson, referenceComparison, ...) come
straight from LLM extraction, so any field may arrive as None. The models
replace those nulls with empty values rather than storing null, which keeps
clients from having to null-check every field.

The subtlety is that an array-valued field must collapse to ``[]``, not ``""``.
The mobile app reads these keys as lists, and a string there throws
"type 'String' is not a subtype of type 'List<dynamic>?'" and takes down the
whole summary screen. This happens for real inputs: a free-text prescription of
"Hernia" is extracted as diagnosis="Hernia" with symptoms=null (a condition, not
a symptom), whereas "Fever" is extracted as symptoms=["Fever"].
"""

from typing import Any, Optional

# Keys the API contract and the mobile client treat as arrays.
LIST_VALUED_KEYS = frozenset({
    # prescriptionJson
    "symptoms", "medicines", "medicalTests", "procedures",
    "labInvestigations", "recommendedTests", "icdCodes", "medicalHistory",
    # policyJson
    "insuredMembers", "coveredDiseases", "excludedDiseases",
    "coveredTreatments", "excludedTreatments", "specialConditions",
    "waitingPeriods", "subLimits", "coPaymentRules", "riders", "coverages",
    "exclusions",
    # referenceComparison
    "featureComparisons", "detectedWaitingConditions",
    "detectedPermanentExclusions", "actionableTakeaways",
    # report / session level
    "comparison", "policyClausesUsed", "prescriptionEvidence",
    "clarificationAnswersUsed", "errors", "questions", "options",
    "policyAnalyses", "reportIds",
})


def sanitize_nulls(data: Any, key: Optional[str] = None) -> Any:
    """Recursively replace None with ``[]`` for list-valued keys, ``""`` otherwise.

    ``key`` is the dict key ``data`` was found under; it is None at the root and
    for list elements.
    """
    if isinstance(data, dict):
        return {k: sanitize_nulls(v, k) for k, v in data.items()}
    if isinstance(data, list):
        return [sanitize_nulls(item) for item in data]
    if data is None:
        return [] if key in LIST_VALUED_KEYS else ""
    return data
