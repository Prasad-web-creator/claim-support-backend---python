"""Localize result presentation without feeding translations into coverage decisions."""

import re
from collections import Counter
from datetime import datetime

from app.core.language import Language


TAMIL = re.compile(r"[\u0b85-\u0bb9]")
LATIN_PHRASE = re.compile(r"\b[A-Za-z]+(?:[ \t,;:/()-]+[A-Za-z]+){2,}\b")
ENGLISH_SENTENCE_WORDS = {
    "am", "are", "be", "been", "can", "could", "has", "have", "is", "should",
    "was", "were", "will", "would", "you", "your",
}
UNAVAILABLE = "இந்த விவரத்தின் தமிழ் மொழிபெயர்ப்பு கிடைக்கவில்லை."
MANUAL_REVIEW = "இந்தக் கோரிக்கையைத் தீர்மானிக்க நேரடி மதிப்பாய்வு தேவை."
ADMISSION_NOTE = (
    "This result assumes you are admitted to hospital (in-patient or day care). "
    "An out-patient consultation alone is not covered under this policy."
)
ADMISSION_NOTE_TA = (
    "இந்த முடிவு மருத்துவமனையில் உள்நோயாளியாக அல்லது பகல்நேரச் சிகிச்சைக்காக அனுமதிக்கப்படுவீர்கள் என்ற "
    "அடிப்படையில் உள்ளது. வெளிநோயாளர் ஆலோசனைக்கு மட்டும் இந்தத் திட்டத்தில் காப்பீட்டுப் பாதுகாப்பு இல்லை."
)

_ITEM_FIELDS = {
    "localizedItem": "item",
    "localizedStatusReason": "coverageStatusReason",
    "localizedFinancialStatus": "financialDecision",
    "localizedPolicyEvidence": "policyEvidence",
    "localizedVerificationCondition": "verificationCondition",
    "localizedVerificationClause": "verificationClause",
}


def _numbers(text: str) -> Counter:
    return Counter(number.replace(",", "") for number in re.findall(r"\d[\d,]*(?:\.\d+)?", text))

_RULE_REASONS = {
    "POLICY_EXPIRED": "சிகிச்சை தேதியில் காப்பீட்டுத் திட்டத்தின் காலம் முடிந்திருந்தது.",
    "POLICY_NOT_YET_EFFECTIVE": "சிகிச்சை தேதியில் காப்பீட்டுத் திட்டம் இன்னும் நடைமுறைக்கு வரவில்லை.",
    "FUTURE_TREATMENT_DATE": "சிகிச்சை தேதி எதிர்காலத்தில் உள்ளது.",
    "MISSING_POLICY_DATES": "காப்பீட்டுத் திட்டத்தின் நடைமுறைத் தேதிகளை உறுதிப்படுத்த முடியவில்லை.",
    "PERMANENT_EXCLUSION": "இந்தச் சிகிச்சை காப்பீட்டுத் திட்டத்தின் நிரந்தர விலக்கில் உள்ளது.",
    "POLICY_EXCLUSION": "இந்தச் சிகிச்சை காப்பீட்டுத் திட்டத்தின் விலக்கில் உள்ளது.",
    "WAITING_PERIOD_ACTIVE": "காப்பீட்டுத் திட்டத்தின் காத்திருப்புக் காலம் இன்னும் நிறைவடையவில்லை.",
    "BENEFIT_NOT_INCLUDED": "இந்தச் சிகிச்சை காப்பீட்டுத் திட்டத்தின் பலன்களில் சேர்க்கப்படவில்லை.",
    "LIMIT_EXCEEDED": "சிகிச்சைச் செலவு காப்பீட்டுத் திட்டத்தின் வரம்பை மீறுகிறது.",
    "NETWORK_RESTRICTION": "இந்தச் சிகிச்சை வழங்குநர் காப்பீட்டு நிறுவனத்தின் அங்கீகரிக்கப்பட்ட வலையமைப்பில் இல்லை.",
    "GEOGRAPHIC_RESTRICTION": "சிகிச்சை இடம் காப்பீட்டுத் திட்டத்தின் புவியியல் வரம்புக்கு வெளியே உள்ளது.",
    "HOSPITALIZATION_CRITERIA_NOT_MET": "மருத்துவமனை அனுமதிக்கான காப்பீட்டு நிபந்தனைகள் பூர்த்தியாகவில்லை.",
    "ELIGIBILITY_FAILED": "இந்தக் கோரிக்கை காப்பீட்டுத் திட்டத்தின் தகுதி நிபந்தனைகளைப் பூர்த்தி செய்யவில்லை.",
}


def prompt_language(language: Language) -> str:
    name = "Tamil" if language == "ta" else "English"
    instruction = f"""
USER LANGUAGE CONFIGURATION: {name} ({language})
Analyze the original documents using all existing rules and the existing JSON schema.
Language must never affect eligibility, coverage decisions, amounts, dates, or limits.
Keep original document quotations exact; keep item/item IDs, keys, enums, status/action
codes, question IDs and option values unchanged, as the app uses them for matching.
Keep patient, insurer and medicine names, policy numbers and other identifiers unchanged.
"""
    if language == "ta":
        return instruction + """
For each analysis.comparison item, add localizedItem, localizedStatusReason,
localizedFinancialStatus and localizedPolicyEvidence in natural Tamil alongside the
existing fields. Do the same for each decisive-question outcome that contains a reason,
financial decision or quotation. Keep the original fields usable by the existing rules.
Add localizedVerificationCondition for each non-empty verificationCondition and
localizedVerificationClause for each non-empty verificationClause. Preserve
verificationClause as exact original wording.
In analysis, add localizedPrescription with diagnosis and symptoms (an array in
the same order as the extracted symptoms). Translate the extracted medical facts
only. Preserve medicine names and medical codes, and leave missing fields empty.
In analysis add localizedSummary, localizedReasoning, localizedRecommendations and
localizedNextSteps for the displayed summary, reasoning, recommendations and next steps.
Add localizedRoomRentCheck and localizedNotes inside financialSummary. For every
verified clause in the WHOLE POLICY CLAUSE SEARCH evidence, add an object to
analysis.localizedPolicyClauses with quote (the exact provided quote), localizedText,
localizedSection and localizedExplanation. Translate only relevant evidence, never the
entire source document. Keep figures, qualifiers, exclusions, page and clause references.
For every clarification question (including decisiveQuestions), keep title, question
and reason in English for interpretation and deduplication, and add localizedTitle,
localizedQuestion and localizedReason in Tamil. Keep options as the original stable strings
shared with outcomes and add displayOptions, an object keyed by those exact strings
with Tamil labels. In any later round use the same ID for the same question.
Localize the response's manual-review reason in Tamil. Before returning verify all Tamil
display fields, keeping official names and source quotations in their original fields.
Use these terms consistently: coverage = காப்பீட்டுப் பாதுகாப்பு; exclusion = விலக்கு;
waiting period = காத்திருப்புக் காலம்; deductible = கழிவுத்தொகை; co-payment = இணைக் கட்டணம்.
"""
    return instruction + """
Write user-facing narrative, questions and option labels in English. For any non-English
policy quote, also add localizedPolicyEvidence on the comparison item and an entry in
analysis.localizedPolicyClauses (quote, localizedText, localizedSection, localizedExplanation).
Preserve the exact quote in the existing source field.
When the extracted diagnosis or symptoms are not in English, add localizedPrescription
with their English diagnosis and symptoms, keeping symptoms in the same order. For any
non-English medical item name (except medicine names), add localizedItem in English
and keep the original item for matching.
"""


def tamil_text(value) -> str:
    """A conservative script check for prose. A short embedded name or unit is allowed."""
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if not TAMIL.search(value):
        return ""
    for phrase in LATIN_PHRASE.findall(value):
        words = set(re.findall(r"[a-z]+", phrase.lower()))
        if words & ENGLISH_SENTENCE_WORDS:
            return ""
    return value


def _display(value, language: Language) -> str:
    if language == "ta":
        return tamil_text(value)
    return value.strip() if isinstance(value, str) and not TAMIL.search(value) else ""


def capture_item_source(item: dict) -> None:
    """Bind each display field to the content it described before deterministic rules ran."""
    source = {}
    for display_key, raw_key in _ITEM_FIELDS.items():
        if item.get(display_key):
            source[display_key] = item.get(raw_key)
    item["_localizationSource"] = source
    item["_localizationStatus"] = str(item.get("coverageStatus") or "").lower()
    for key in ("decisiveQuestion", "decisiveQuestions"):
        questions = item.get(key) or []
        if isinstance(questions, dict):
            questions = [questions]
        for question in questions:
            if not isinstance(question, dict):
                continue
            outcomes = question.get("outcomes") or []
            for outcome in outcomes.values() if isinstance(outcomes, dict) else outcomes:
                if isinstance(outcome, dict):
                    capture_item_source(outcome)


def attach_clause_translations(analysis: dict, language: Language) -> None:
    # Join only on the exact retrieved quotation, after the existing policy-text verification.
    translations = {
        candidate.get("quote"): candidate
        for candidate in analysis.pop("localizedPolicyClauses", []) or []
        if isinstance(candidate, dict) and isinstance(candidate.get("quote"), str)
    }
    for item in analysis.get("comparison") or []:
        if not isinstance(item, dict):
            continue
        for clause in item.get("policyClauses") or []:
            candidate = translations.get(clause.get("quote"), {})
            for key in ("localizedText", "localizedSection", "localizedExplanation"):
                value = _display(candidate.get(key), language)
                if key == "localizedText" and value and _numbers(value) != _numbers(clause["quote"]):
                    continue
                if value:
                    clause[key] = value


def _render_item(item: dict, language: Language, rule_reason: str) -> None:
    source = item.pop("_localizationSource", {})
    old_status = item.pop("_localizationStatus", "")
    status_same = old_status == str(item.get("coverageStatus") or "").lower()
    for display_key, raw_key in _ITEM_FIELDS.items():
        value = _display(item.pop(display_key, None), language)
        original = item.get(raw_key)
        is_quote = raw_key in ("policyEvidence", "verificationClause")
        if value and source.get(display_key) == original:
            if is_quote and (not isinstance(original, str) or _numbers(value) != _numbers(original)):
                value = ""
            if value and (is_quote or raw_key == "item" or status_same):
                item[display_key] = value
                continue
        # Same-language originals, identifiers and medicine names can remain visible.
        if language == "ta" and original and raw_key != "item" and not is_quote:
            fallback = rule_reason if raw_key == "coverageStatusReason" else ""
            item[display_key] = tamil_text(original) or fallback or UNAVAILABLE


def localize_report(report: dict, analysis: dict, language: Language) -> dict:
    report["responseLanguage"] = language
    rule = report.get("deterministicResult") or {}
    rule_reason = _RULE_REASONS.get(rule.get("reasonCode"), "")
    if not rule_reason and str(report.get("overallStatus", "")).startswith("Rejected"):
        rule_reason = _RULE_REASONS["ELIGIBILITY_FAILED"]
    for item in report.get("comparison") or []:
        _render_item(item, language, rule_reason)
    prescription = report.get("prescriptionJson") or {}
    supplied = analysis.get("localizedPrescription")
    supplied = supplied if isinstance(supplied, dict) else {}
    localized_prescription = {}
    diagnosis = prescription.get("diagnosis")
    has_diagnosis = isinstance(diagnosis, str) and diagnosis.strip().lower() not in ("", "---", "none", "unknown", "n/a", "null")
    if has_diagnosis and (language == "ta" or TAMIL.search(diagnosis)):
        value = _display(supplied.get("diagnosis"), language)
        if value:
            localized_prescription["diagnosis"] = value
    symptoms = prescription.get("symptoms")
    if isinstance(symptoms, list) and symptoms and (language == "ta" or any(TAMIL.search(str(value)) for value in symptoms)):
        translated = supplied.get("symptoms")
        if not isinstance(translated, list) or len(translated) != len(symptoms):
            translated = [None] * len(symptoms)
        if language == "ta":
            localized_prescription["symptoms"] = [
                tamil_text(value) or tamil_text(original) or UNAVAILABLE
                for value, original in zip(translated, symptoms)
            ]
        elif all(_display(value, language) for value in translated):
            localized_prescription["symptoms"] = translated
    if localized_prescription:
        report["localizedPrescription"] = localized_prescription
    if language == "en":
        return report
    localized_items = {
        item.get("item"): item["localizedItem"]
        for item in report.get("comparison") or []
        if isinstance(item.get("item"), str) and item.get("localizedItem")
    }
    for term in report.get("majorTerms") or []:
        if term.get("items"):
            term["localizedItems"] = [localized_items.get(item, item) for item in term["items"]]
        if not term.get("effect") and rule_reason:
            term["localizedSection"] = rule_reason
    if str(report.get("overallStatus", "")).startswith("Invalid"):
        validity = report.get("documentValidity") or {}
        for key, message in (
            ("policyInvalidReason", "காப்பீட்டு ஆவணம் செல்லுபடியாகவில்லை அல்லது படிக்க இயலவில்லை."),
            ("prescriptionInvalidReason", "மருத்துவச் சீட்டு செல்லுபடியாகவில்லை அல்லது படிக்க இயலவில்லை."),
        ):
            if validity.get(key):
                validity[key] = message
        report["summary"] = "தேவையான ஆவணங்கள் செல்லுபடியாகவில்லை அல்லது படிக்க இயலாததால் பகுப்பாய்வை முடிக்க முடியவில்லை."
    else:
        source = str(report.get("summary") or "")
        assumed_admission = source.endswith(ADMISSION_NOTE)
        if assumed_admission:
            source = source.removesuffix(ADMISSION_NOTE).rstrip()
        summary_matches = source == analysis.get("summary")
        translated = tamil_text(analysis.get("localizedSummary")) if summary_matches else ""
        if summary_matches and not translated:
            translated = tamil_text(source)
        report["summary"] = translated or rule_reason or (
            MANUAL_REVIEW if "Manual Review" in str(report.get("overallStatus")) else UNAVAILABLE
        )
        if assumed_admission:
            report["summary"] += f" {ADMISSION_NOTE_TA}"
    report["summaryText"] = report["summary"]
    for key in ("reasoning", "recommendations"):
        display = "localized" + key.capitalize()
        if report.get(key):
            report[key] = tamil_text(analysis.get(display)) or tamil_text(report[key]) or UNAVAILABLE
    if report.get("nextSteps"):
        translated_steps = analysis.get("localizedNextSteps")
        if isinstance(translated_steps, list) and len(translated_steps) == len(report["nextSteps"]):
            report["nextSteps"] = [tamil_text(step) or UNAVAILABLE for step in translated_steps]
        else:
            report["nextSteps"] = [UNAVAILABLE]
    financial_source = analysis.get("financialSummary")
    financial_report = report.get("financialSummary")
    if isinstance(financial_source, dict) and financial_report:
        for key in ("roomRentCheck", "notes"):
            if financial_report.get(key):
                display = "localized" + key[0].upper() + key[1:]
                financial_report[key] = tamil_text(financial_source.get(display)) or UNAVAILABLE
    return report


_FIRST_START_REASON = "இடைவெளியின்றி காப்பீட்டுப் பாதுகாப்பு முதலில் தொடங்கிய நாளிலிருந்து காத்திருப்புக் காலம் கணக்கிடப்படும்."
_BREAK_REASON = "புதுப்பிப்பில் இடைவெளி இருந்தால், தற்போதைய காப்பீட்டுத் திட்டம் தொடங்கிய நாளிலிருந்து காத்திருப்புக் காலம் கணக்கிடப்படும்."
_OPTIONS = {
    "Yes": "ஆம்",
    "No": "இல்லை",
    "Don't know": "தெரியவில்லை",
    "Not sure": "உறுதியாகத் தெரியவில்லை",
    "New policy (first year)": "புதிய காப்பீட்டுத் திட்டம் (முதல் ஆண்டு)",
    "Renewed policy": "புதுப்பிக்கப்பட்ட காப்பீட்டுத் திட்டம்",
    "Yes, renewed every year without a break": "ஆம், ஒவ்வோர் ஆண்டும் இடைவெளியின்றிப் புதுப்பிக்கப்பட்டது",
    "No, there was a break": "இல்லை, இடைவெளி இருந்தது",
    "Use my answer (there was a break)": "என் பதிலைப் பயன்படுத்தவும் (இடைவெளி இருந்தது)",
    "Use policy document (renewed without a break)": "ஆவணத்தைப் பயன்படுத்தவும் (இடைவெளியின்றிப் புதுப்பிக்கப்பட்டது)",
    "Use my answer (renewed without a break)": "என் பதிலைப் பயன்படுத்தவும் (இடைவெளியின்றிப் புதுப்பிக்கப்பட்டது)",
    "Use policy document (there was a break)": "ஆவணத்தைப் பயன்படுத்தவும் (இடைவெளி இருந்தது)",
}

_ANNEXURE_OPTIONS = {
    "yes, contains items from annexure i": "ஆம், இணைப்பு I-ல் உள்ள பொருட்கள் இடம்பெற்றுள்ளன",
    "no, all are integral surgical consumables / package items": (
        "இல்லை, அனைத்தும் அறுவைச் சிகிச்சைக்கு இன்றியமையாத நுகர்பொருட்கள் அல்லது சிகிச்சைத் தொகுப்பில் அடங்கியவை"
    ),
    "don't know / need itemised breakdown": "தெரியவில்லை / பொருள்வாரியான கட்டண விவரம் தேவை",
}


def _option_key(option: str) -> str:
    return " ".join(option.casefold().replace("’", "'").replace("itemized", "itemised").split()).rstrip(".")


def _date_label(value: str) -> str:
    for pattern in ("%d %b %Y", "%d-%b-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), pattern).strftime("%d/%m/%Y")
        except ValueError:
            pass
    return value


def _deterministic_question(question: dict) -> tuple[str, str, str, dict] | None:
    qid = question.get("id")
    options = question.get("options") or []
    if qid in ("policy_inception_date_conflict", "policy_start_date_conflict") and len(options) == 2:
        dates = [_date_label(option.partition("(")[2].rstrip(")")) for option in options]
        return (
            "காப்பீட்டுத் திட்டம் தொடங்கிய தேதி",
            f"நீங்கள் {dates[0]} எனப் பதிவு செய்துள்ளீர்கள். ஆவணத்தில் {dates[1]} என உள்ளது. எந்தத் தேதியைப் பயன்படுத்த வேண்டும்?",
            _FIRST_START_REASON if qid == "policy_inception_date_conflict" else "நீங்கள் உள்ளிட்ட தேதியும் ஆவணத்தில் உள்ள தேதியும் வேறுபடுகின்றன.",
            {options[0]: f"நான் உள்ளிட்ட தேதி ({dates[0]})", options[1]: f"ஆவணத்தில் உள்ள தேதி ({dates[1]})"},
        )
    if qid == "policy_first_start_date":
        return (
            "காப்பீட்டுத் திட்டத்தின் முதல் தொடக்கத் தேதி",
            "புதுப்பிப்புகளுக்கு முன், இந்தக் காப்பீட்டுத் திட்டம் முதன்முதலில் தொடங்கிய தேதி எது?",
            _FIRST_START_REASON, {},
        )
    if qid == "policy_new_start_date":
        return (
            "காப்பீட்டுத் திட்டத்தின் தொடக்கத் தேதி",
            "ஆவணத்தில் உள்ள காப்பீட்டுத் திட்டத்தின் தொடக்கத் தேதியைத் தேர்ந்தெடுக்கவும்.",
            "புதிய காப்பீட்டுத் திட்டத்தின் தொடக்கத் தேதியிலிருந்து காத்திருப்புக் காலம் கணக்கிடப்படும்.", {},
        )
    if qid == "policy_new_or_renewed_conflict":
        return (
            "புதிய அல்லது புதுப்பிக்கப்பட்ட காப்பீட்டுத் திட்டம்",
            "நீங்கள் அளித்த தகவலும் ஆவணத்தில் உள்ள தகவலும் வேறுபடுகின்றன. இது புதிய காப்பீட்டுத் திட்டமா அல்லது புதுப்பிக்கப்பட்டதா?",
            "முதல் ஆண்டு காப்பீடா அல்லது புதுப்பிப்பா என்பதைப் பொறுத்துக் காத்திருப்புக் காலம் மாறும்.", {},
        )
    if qid == "policy_renewed_without_break":
        return (
            "இடைவெளியற்ற புதுப்பிப்பு",
            "இந்தக் காப்பீட்டுத் திட்டம் ஒவ்வோர் ஆண்டும் இடைவெளியின்றிப் புதுப்பிக்கப்பட்டதா?",
            _BREAK_REASON, {},
        )
    if qid == "policy_renewal_break_conflict":
        return (
            "புதுப்பிப்பு இடைவெளி குறித்த முரண்பாடு",
            "புதுப்பிப்பில் இடைவெளி இருந்ததா என்பது குறித்த உங்கள் பதிலும் ஆவணத்தில் உள்ள தகவலும் வேறுபடுகின்றன. எதைப் பயன்படுத்த வேண்டும்?",
            _BREAK_REASON, {},
        )
    return None


def localize_question(question: dict, language: Language) -> dict:
    result = {**question, "responseLanguage": language}
    if language == "en":
        return result
    translated = _deterministic_question(question)
    original_options = question.get("options") or []
    labels = question.get("displayOptions")
    labels = labels if isinstance(labels, dict) else {}
    if translated:
        title, text, reason, option_translations = translated
        labels = {**labels, **option_translations}
    else:
        title = tamil_text(question.get("localizedTitle") or question.get("title")) or "காப்பீட்டுப் பாதுகாப்பை உறுதிசெய்யத் தேவையான விவரம்"
        text = tamil_text(question.get("localizedQuestion") or question.get("question"))
        reason = tamil_text(question.get("localizedReason") or question.get("reason"))
    known_options = {_option_key(source): label for source, label in _OPTIONS.items()} | _ANNEXURE_OPTIONS
    option_keys = {_option_key(option) for option in original_options if isinstance(option, str)}
    if not text and option_keys == _ANNEXURE_OPTIONS.keys():
        text = (
            "கட்டணப் பட்டியலில் இணைப்பு I-ல் உள்ள பொருட்கள் ஏதேனும் உள்ளனவா? அல்லது அனைத்தும் "
            "அறுவைச் சிகிச்சைக்கு இன்றியமையாத நுகர்பொருட்களா அல்லது சிகிச்சைத் தொகுப்பில் அடங்கியவையா?"
        )
    display_options = {}
    untranslated = []
    for option in original_options:
        if not isinstance(option, str):
            continue
        label = tamil_text(labels.get(option)) or known_options.get(_option_key(option)) or tamil_text(option)
        if label:
            display_options[option] = label
        else:
            untranslated.append(option)
    result.update(
        canonicalQuestion=question.get("canonicalQuestion") or question.get("question"),
        title=title,
        question=text or UNAVAILABLE,
        questionTranslationMissing=not text,
        reason=reason,
        displayOptions=display_options,
        untranslatedOptions=untranslated,
    )
    return result
