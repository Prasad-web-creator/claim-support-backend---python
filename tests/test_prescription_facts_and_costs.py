"""The coverage AI sees every prescription fact, and cost results stay consistent."""
from app.services.coverage.coverage_analysis import (
    MAX_PRESCRIPTION_CHARS,
    _claim_facts_block,
    _item_names,
    _prescription_text_block,
)
from app.services.coverage.report_generator import _financial_summary, _next_steps
from app.services.coverage.verification import apply_consistency_checks

RX = {
    "patientName": "Mr. Rajesh Venkatraman",
    "age": 45,
    "admission": "12-Oct-2026",
    "diagnosis": "Acute Appendicitis with Appendicolith",
    "procedures": [{"name": "Emergency Laparoscopic Appendectomy", "cost": None}],
    "medicalTests": [{"name": "Complete Blood Count", "cost": 450}, "USG Abdomen & Pelvis"],
    "hospitalizationRequired": True,
    "estimatedCost": 124000,
    "medicalNotes": "Non-smoker, occasional alcohol.",
}


def test_claim_facts_include_tests_costs_admission_and_history():
    block = _claim_facts_block(RX)
    assert "Complete Blood Count (₹450)" in block
    assert "USG Abdomen & Pelvis" in block
    assert "Emergency Laparoscopic Appendectomy" in block
    assert "{'name'" not in block  # never a raw dict
    assert "required (in-patient)" in block
    assert "₹124,000" in block
    assert "occasional alcohol" in block
    assert "12-Oct-2026" in block


def test_item_names_skip_empty_entries():
    assert _item_names([{"name": ""}, None, "", {"name": "CRP", "cost": 0}]) == "CRP"


def test_full_prescription_text_is_sent_and_capped():
    assert _prescription_text_block("Room: Single AC Rs 4,500/day", False) == "Room: Single AC Rs 4,500/day"
    assert len(_prescription_text_block("x" * (MAX_PRESCRIPTION_CHARS + 50), False)) == MAX_PRESCRIPTION_CHARS
    assert _prescription_text_block("", False).startswith("Not available")
    assert _prescription_text_block("fever", True) == "(Self-entered text) fever"


def test_covered_item_with_zero_payable_becomes_unknown_amount():
    [item] = apply_consistency_checks([{
        "item": "Surgical consumables", "coverageStatus": "Partially Covered",
        "prescriptionCost": 8000, "estimatedPayableAmount": 0, "financialDecision": "Annexure I items.",
    }])
    assert item["consistencyCheck"] == "zero_payable_conflict"
    assert item["estimatedPayableAmount"] is None
    assert "final itemised bill" in item["financialDecision"]


def test_not_covered_item_with_zero_payable_is_unchanged():
    [item] = apply_consistency_checks([{
        "item": "Cosmetic", "coverageStatus": "Not Covered", "prescriptionCost": 5000, "estimatedPayableAmount": 0,
    }])
    assert item["estimatedPayableAmount"] == 0
    assert "consistencyCheck" not in item


def test_financial_summary_keeps_only_valid_amounts():
    fs = _financial_summary({"estimatedTotalCost": "₹1,24,000", "estimatedPayableAmount": -5,
                             "estimatedPatientPayable": True, "roomRentCheck": " ok "})
    assert fs == {"estimatedTotalCost": 124000, "estimatedPayableAmount": None,
                  "estimatedPatientPayable": None, "roomRentCheck": "ok", "notes": ""}
    assert _financial_summary({}) == {}
    assert _financial_summary("x") == {}


def test_next_steps_are_trimmed_and_capped():
    assert _next_steps([" a ", "", None, "b"] + ["c"] * 10) == ["a", "b", "c", "c", "c", "c"]
    assert _next_steps("not a list") == []
