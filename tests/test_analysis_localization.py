import pytest

from app.services.coverage import coverage_analysis, policy_clause_search
from app.services.coverage.presentation_language import (
    UNAVAILABLE, attach_clause_translations, localize_question, localize_report, tamil_text,
)
from app.services.coverage.report_generator import generate_report


@pytest.mark.asyncio
async def test_analysis_translates_in_existing_requests_and_keeps_verified_evidence(monkeypatch):
    quote = "Appendectomy for acute appendicitis is covered up to INR 50000."
    translated = "குடல்வால் அகற்றும் அறுவைச் சிகிச்சைக்கு 50000 ரூபாய் வரை காப்பீட்டுப் பாதுகாப்பு உண்டு."
    calls = []

    async def fake_request(*, system_prompt, operation_name, user_content, **kwargs):
        calls.append(operation_name)
        if operation_name == "Policy Clause Search":
            if len(calls) == 3:
                assert "displayOptions mapping every exact option" in system_prompt
            return {"extractedJson": {"items": [{
                "item": "Appendectomy", "decision": "Covered", "directMention": True,
                "clauses": [{"quote": quote, "effect": "Covers", "section": "4.1"}],
            }]}}
        language = "ta" if "Tamil (ta)" in system_prompt else "en"
        assert f"({'ta' if language == 'ta' else 'en'})" in system_prompt
        assert quote in user_content
        localized = {
            "localizedItem": "குடல்வால் அகற்றும் அறுவைச் சிகிச்சை",
            "localizedStatusReason": "சிகிச்சை காப்பீட்டுத் திட்டத்தில் சேர்க்கப்பட்டுள்ளது.",
            "localizedPolicyEvidence": translated,
        } if language == "ta" else {}
        data = {
            "summary": "The prescribed procedure is covered.",
            "comparison": [{
                "item": "Appendectomy", "coverageStatus": "Covered",
                "policyEvidence": quote, "coverageStatusReason": "The prescribed procedure is covered.",
                "prescriptionCost": 10000, **localized,
            }],
            "localizedSummary": "பரிந்துரைக்கப்பட்ட சிகிச்சைக்குக் காப்பீட்டுப் பாதுகாப்பு உண்டு.",
            "localizedPrescription": {
                "diagnosis": "குடல்வால் அழற்சி",
                "symptoms": ["வயிற்று வலி"],
            },
            "localizedPolicyClauses": [
                {"quote": quote, "localizedText": translated},
                {"quote": "Fabricated quote", "localizedText": translated},
            ],
        }
        return {"extractedJson": {"next_action": "generate_report", "analysis": data}}

    monkeypatch.setattr(policy_clause_search, "extract_json_with_retry", fake_request)
    monkeypatch.setattr(coverage_analysis, "extract_json_with_retry", fake_request)
    rx = {"diagnosis": "Appendicitis", "symptoms": ["Abdominal pain"], "procedures": [{"name": "Appendectomy"}]}
    policy = {"policyType": "Health", "insuranceCompany": "Example Insurer", "coverageAmount": 50000}

    reports = {}
    for language in ("en", "ta"):
        result = await coverage_analysis.analyze_coverage(
            policy_text=quote, prescription_text="Appendectomy for appendicitis",
            prescription_json=rx, business_rule_results={}, policy_json=policy,
            response_language=language,
        )
        result_data = result["analysis"]
        reports[language] = localize_report(
            generate_report(policy, rx, {}, result_data, 0), result_data, language,
        )
    assert calls == ["Policy Clause Search", "Coverage Analysis"] * 2
    en, ta = [reports[language]["comparison"][0] for language in ("en", "ta")]
    assert ta["coverageStatus"] == en["coverageStatus"] == "Covered"
    assert ta["prescriptionCost"] == en["prescriptionCost"] == 10000
    assert ta["policyEvidence"] == en["policyEvidence"] == quote
    assert ta["localizedPolicyEvidence"] == translated
    assert ta["policyClauses"][0]["quote"] == quote
    assert ta["policyClauses"][0]["section"] == "4.1"
    assert ta["policyClauses"][0]["localizedText"] == translated
    assert len(ta["policyClauses"]) == 1
    assert reports["ta"]["responseLanguage"] == "ta"
    assert reports["ta"]["summary"] != reports["en"]["summary"]
    assert reports["ta"]["prescriptionJson"]["diagnosis"] == "Appendicitis"
    assert reports["ta"]["localizedPrescription"] == {
        "diagnosis": "குடல்வால் அழற்சி", "symptoms": ["வயிற்று வலி"],
    }


def test_tamil_clarification_keeps_identifiers_and_rejects_english_display_text():
    question = {
        "id": "policy_renewed_without_break", "question": "Was it renewed every year?",
        "options": ["Yes, renewed every year without a break", "No, there was a break"],
    }
    translated = localize_question(question, "ta")
    assert translated["id"] == question["id"]
    assert translated["options"] == question["options"]
    assert translated["displayOptions"][question["options"][0]].startswith("ஆம்")
    assert translated["canonicalQuestion"] == question["question"]
    assert translated["question"] != question["question"]
    assert localize_question({"id": "ai_question", "question": "Are you a member?"}, "ta")["question"] == UNAVAILABLE
    incomplete = localize_question({
        "id": "new_condition", "category": "verification",
        "question": "Was the admission planned?", "options": ["Yes", "Emergency admission"],
        "reason": "The policy requires a planned admission.",
    }, "ta")
    assert incomplete["question"] == UNAVAILABLE
    assert incomplete["questionTranslationMissing"] is True
    assert incomplete["canonicalQuestion"] == "Was the admission planned?"
    assert incomplete["options"] == ["Yes", "Emergency admission"]
    assert incomplete["displayOptions"] == {"Yes": "ஆம்"}
    assert incomplete["untranslatedOptions"] == ["Emergency admission"]


def test_tamil_original_quote_and_verified_reference_are_preserved():
    quote = "4.1 காப்பீட்டுத் தொகை 50000 ரூபாய்."
    result = policy_clause_search._clean_result(
        {"items": [{"item": "மருத்துவச் செலவு", "clauses": [
            {"quote": quote, "pageNumber": 3, "clauseNumber": "4.1"},
        ]}]},
        f"--- PAGE 2 ---\nகாப்பீட்டு விதிமுறைகள்\n--- PAGE 3 ---\n{quote}",
    )
    clause = result[0]["clauses"][0]
    assert (clause["quote"], clause["pageNumber"], clause["clauseNumber"]) == (quote, 3, "4.1")
    analysis = {
        "comparison": [{"policyClauses": [clause]}],
        "localizedPolicyClauses": [{"quote": quote, "localizedText": "4.1 Coverage is INR 5000."}],
    }
    attach_clause_translations(analysis, "en")
    assert "localizedText" not in clause


def test_tamil_continuity_uses_existing_dates_and_retains_original_messages():
    from app.services.coverage.policy_continuity import declared_policy_continuity

    policy = {
        "inceptionDate": "2022-04-01",
        "policyStartDate": "2024-04-01",
        "policyEndDate": "2025-03-31",
        "isRenewal": True,
        "continuousRenewal": False,
    }
    continuity = declared_policy_continuity(policy, {"admissionDate": "2024-07-08"})
    assert continuity["statusCode"] == "GAP_DETECTED"
    assert "waiting periods" in continuity["summary"]
    assert "01/04/2024" in continuity["summaryTa"]
    assert "காத்திருப்புக் காலம்" in continuity["summaryTa"]
    assert continuity["claimDateSource"] == "Admission date"
    assert continuity["claimDateSourceTa"] == "மருத்துவமனையில் அனுமதிக்கப்பட்ட தேதி"


def test_tamil_otp_message_localizes_both_mime_parts():
    from app.services.otp_service import _email_message

    message = _email_message("test@example.com", "123456", "sender@example.com", "ta")
    assert "சரிபார்ப்புக் குறியீடு" in str(message["Subject"])
    assert "123456" in message.get_body(("plain",)).get_content()
    html = message.get_body(("html",)).get_content()
    assert 'lang="ta"' in html
    assert "123456" in html
    assert "Do not share" not in html


def test_tamil_prose_rejects_embedded_english_sentences_and_keeps_names():
    assert tamil_text("காப்பீடு. Admission is subject to approval.") == ""
    assert tamil_text("மருந்து Metformin Hydrochloride 500 mg பரிந்துரைக்கப்பட்டது.")
    assert tamil_text("United India Insurance Company Limited வழங்கிய காப்பீடு.")


def test_annexure_question_has_tamil_choices_without_changing_answer_values():
    options = [
        "Yes, contains items from Annexure I",
        "No, all are integral surgical consumables / package items",
        "Don't know / Need itemised breakdown",
    ]
    question = localize_question({"id": "annexure", "question": "What items are in the bill?", "options": options}, "ta")
    assert question["questionTranslationMissing"] is False
    assert "இணைப்பு I" in question["question"]
    assert list(question["displayOptions"]) == options
    assert all(tamil_text(label) for label in question["displayOptions"].values())
    assert question["options"] == options


def test_missing_diagnosis_translation_does_not_replace_the_exact_name():
    original = "Acute Appendicitis with Appendicolith (ICD-10: K35.80)"
    report = localize_report({"prescriptionJson": {"diagnosis": original}}, {}, "ta")
    assert report["prescriptionJson"]["diagnosis"] == original
    assert "diagnosis" not in report.get("localizedPrescription", {})


def test_clause_translation_fills_matching_question_without_changing_answers():
    from app.services.coverage.verification import normalize_decisive_question

    original = {
        "question": "Did the bill include surgical consumables?",
        "options": ["Yes", "No", "Don't know"],
        "outcomes": [{"option": "Yes", "coverageStatus": "Covered"}],
    }
    translations = {
        "localizedQuestion": "கட்டணப் பட்டியலில் அறுவைச் சிகிச்சை நுகர்பொருட்கள் உள்ளனவா?",
        "displayOptions": {"Yes": "ஆம்", "No": "இல்லை"},
    }
    found = {
        "item": "Surgery", "clauses": [], "relatedTerms": [], "directMention": True,
        "decision": "Needs Verification", "reason": "Confirm the bill details.",
        "decisiveQuestion": normalize_decisive_question({**original, **translations}),
    }
    exact, different = dict(original), {**original, "options": ["Yes, with approval", "No", "Don't know"]}
    cards = [{"item": "Surgery", "decisiveQuestion": question} for question in (exact, different)]
    policy_clause_search.attach_policy_clauses(cards, [found])
    assert exact["localizedQuestion"] == translations["localizedQuestion"]
    assert exact["displayOptions"] == translations["displayOptions"]
    assert exact["options"] == original["options"]
    assert exact["outcomes"] == original["outcomes"]
    assert "displayOptions" not in different
