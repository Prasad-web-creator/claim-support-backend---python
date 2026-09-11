"""
Reference Benchmark Comparison Service.
Compares analyzed policies and prescriptions against the industry reference standards
extracted from the 5 ClaimSupport PDFs:
- Marsh India / GIC Council Market Snapshot (Solvency ratios, market standing)
- Niva Bupa ReAssure 3.0 Gold Standard (Features & Riders)
- Master 2-Year Specific Waiting Period Conditions & 32 Permanent Exclusions
"""

from typing import Dict, Any, List, Optional
import re
from app.data.reference.reference_benchmarks import (
    INSURER_MARKET_BENCHMARK,
    INDUSTRY_OVERVIEW,
    BENCHMARK_POLICY_SPEC,
    SPECIFIC_2_YEAR_WAITING_CONDITIONS,
    PERMANENT_EXCLUSIONS_CATALOG,
)


def _clean_str(val: Any) -> str:
    if not val:
        return ""
    return str(val).strip().lower()


def _match_insurer(company_name: str) -> Optional[Dict[str, Any]]:
    if not company_name:
        return None
    cleaned = _clean_str(company_name)
    if cleaned in ("---", "none", "null", "unknown", "unknown company", "unknown policy"):
        return None

    # Direct match
    if cleaned in INSURER_MARKET_BENCHMARK:
        return INSURER_MARKET_BENCHMARK[cleaned]

    # Keyword / Substring match
    for key, data in INSURER_MARKET_BENCHMARK.items():
        if key in cleaned or cleaned in key:
            return data
        
        # Check specific tokens (e.g. "icici", "star health", "niva bupa", "tata aig", "hdfc")
        tokens = [t for t in key.split() if len(t) > 3 and t not in ("insurance", "company", "limited", "general")]
        matched_tokens = [t for t in tokens if t in cleaned]
        if len(matched_tokens) >= 2 or (len(tokens) == 1 and len(matched_tokens) == 1):
            return data

    return None


def generate_reference_comparison(
    policy_json: Optional[Dict[str, Any]],
    prescription_json: Optional[Dict[str, Any]],
    coverage_analysis: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Compares the user's policy and prescription against the Reference Benchmark standards.
    Returns a structured dictionary to be added as extra content in the coverage summary.
    """
    policy_json = policy_json or {}
    prescription_json = prescription_json or {}
    coverage_analysis = coverage_analysis or {}

    # ──────────────────────────────────────────────────────────────────────────
    # 1. INSURER SOLVENCY & MARKET STANDING BENCHMARK
    # ──────────────────────────────────────────────────────────────────────────
    insurer_name = policy_json.get("insuranceCompany") or policy_json.get("company") or ""
    matched_insurer = _match_insurer(insurer_name)

    if matched_insurer:
        solvency = matched_insurer.get("solvencyRatio")
        solvency_norm = INDUSTRY_OVERVIEW["irdaiMinSolvencyNorm"]
        if solvency is not None:
            if solvency >= 2.0:
                solvency_desc = f"Robust solvency ({solvency:.2f}), well above IRDAI minimum norm of {solvency_norm:.2f}. Excellent claim-paying stability."
                badge_type = "robust"
            elif solvency >= solvency_norm:
                solvency_desc = f"Healthy solvency ({solvency:.2f}), compliant with IRDAI regulatory norm of {solvency_norm:.2f}."
                badge_type = "healthy"
            elif solvency > 0:
                solvency_desc = f"Marginal solvency ({solvency:.2f}), below IRDAI recommended benchmark of {solvency_norm:.2f}."
                badge_type = "marginal"
            else:
                solvency_desc = f"Negative solvency ratio ({solvency:.2f}). State support or capital restructuring underway."
                badge_type = "stressed"
        else:
            solvency_desc = "Solvency ratio not publicly reported in current GIC Council provisional snapshot."
            badge_type = "unknown"

        insurer_benchmark = {
            "isMatched": True,
            "insurerName": matched_insurer["displayName"],
            "category": matched_insurer["category"],
            "marketRank": matched_insurer["marketRank"],
            "marketSharePct": matched_insurer["marketSharePct"],
            "solvencyRatio": solvency,
            "solvencyStatus": matched_insurer["solvencyStatus"],
            "solvencyDescription": solvency_desc,
            "badgeType": badge_type,
            "reportingPeriod": INDUSTRY_OVERVIEW["reportingPeriod"],
            "source": "Marsh India / GIC Council Provisional Industry Snapshot"
        }
    else:
        display_company = str(insurer_name).strip() if insurer_name and _clean_str(insurer_name) not in ("---", "none", "unknown") else "Your Insurer"
        insurer_benchmark = {
            "isMatched": False,
            "insurerName": display_company,
            "category": "Health / General Insurance Provider",
            "marketRank": None,
            "marketSharePct": None,
            "solvencyRatio": None,
            "solvencyStatus": "Regulatory Norm: Min 1.50",
            "solvencyDescription": "IRDAI mandates a minimum Solvency Ratio of 1.50 for all insurers to guarantee long-term claim settlement capability.",
            "badgeType": "info",
            "reportingPeriod": INDUSTRY_OVERVIEW["reportingPeriod"],
            "source": "IRDAI Regulatory Benchmark"
        }

    # ──────────────────────────────────────────────────────────────────────────
    # 2. FEATURE-BY-FEATURE BENCHMARK COMPARISON (vs. ReAssure 3.0 Standard)
    # ──────────────────────────────────────────────────────────────────────────
    feature_comparisons: List[Dict[str, Any]] = []

    # A. Sum Insured & Restoration
    cov_amount = policy_json.get("coverageAmount")
    user_si_str = f"₹{int(cov_amount):,}" if isinstance(cov_amount, (int, float)) and cov_amount > 0 else (str(cov_amount) if cov_amount else "Specified Base SI")
    feature_comparisons.append({
        "feature": "Base Sum Insured",
        "userPolicy": user_si_str,
        "benchmarkStandard": BENCHMARK_POLICY_SPEC["baseSumInsured"],
        "comparisonStatus": "Superior Benchmark" if "unlimited" not in _clean_str(user_si_str) else "Parity",
        "insight": "Benchmark plan provides limitless coverage from Day 1 with zero capping on admissible claims."
    })

    # B. Room Rent Limit
    user_room = policy_json.get("roomEligibility") or policy_json.get("roomRentLimit") or policy_json.get("roomRent") or "Standard Capping (1% of SI or Twin Sharing)"
    is_room_unlimited = any(kw in _clean_str(user_room) for kw in ["no limit", "any room", "single private", "unlimited", "no sublimit"])
    feature_comparisons.append({
        "feature": "Room Rent Category",
        "userPolicy": str(user_room).strip(),
        "benchmarkStandard": BENCHMARK_POLICY_SPEC["roomRentCategory"],
        "comparisonStatus": "Parity" if is_room_unlimited else "Potential Gap",
        "insight": "Room rent capping often causes proportionate deductions across the entire hospital bill. Benchmark covers Any Room without deduction."
    })

    # C. Consumables / Non-Payable Items (Claim Safeguard+)
    exclusions_text = " ".join([
        str(policy_json.get("exclusions") or ""),
        str(policy_json.get("excludedTreatments") or ""),
        str(policy_json.get("specialConditions") or "")
    ]).lower()
    has_consumables = any(kw in exclusions_text for kw in ["consumables covered", "safeguard", "non-medical items covered", "list 1 covered"])
    feature_comparisons.append({
        "feature": "Consumables & Non-Payables",
        "userPolicy": "Covered via Rider" if has_consumables else "Excluded by Default (Standard IRDAI Lists I-IV)",
        "benchmarkStandard": BENCHMARK_POLICY_SPEC["consumablesCoverage"],
        "comparisonStatus": "Parity" if has_consumables else "Coverage Gap",
        "insight": "Consumable items (gloves, PPE, syringes, administrative fees) constitute 10-15% of modern hospital bills. Benchmark provides 100% reimbursement."
    })

    # D. Modern Treatments
    has_modern_capping = any(kw in exclusions_text for kw in ["modern treatment sublimit", "robotic surgery cap", "sub-limit on modern"])
    feature_comparisons.append({
        "feature": "Modern Treatments & Robotic Surgery",
        "userPolicy": "Subject to Sub-limits" if has_modern_capping else "Covered up to Sum Insured",
        "benchmarkStandard": BENCHMARK_POLICY_SPEC["modernTreatments"],
        "comparisonStatus": "Potential Gap" if has_modern_capping else "Parity",
        "insight": "Advanced treatments like robotic surgeries, stem cell therapies, and balloon sinuplasty are covered up to full Sum Insured in the benchmark standard."
    })

    # E. Pre & Post Hospitalization Duration
    feature_comparisons.append({
        "feature": "Pre & Post Hospitalization Coverage",
        "userPolicy": "Standard 30 Days Pre / 60 Days Post",
        "benchmarkStandard": f"{BENCHMARK_POLICY_SPEC['preHospitalizationDays']} Days Pre / {BENCHMARK_POLICY_SPEC['postHospitalizationDays']} Days Post",
        "comparisonStatus": "Extended Window in Benchmark",
        "insight": "Benchmark provides 6 months (180 days) post-discharge follow-up tests, medicines, and consultation coverage."
    })

    # ──────────────────────────────────────────────────────────────────────────
    # 3. PRESCRIPTION SCREENING (2-Year Waiting Periods & 32 Permanent Exclusions)
    # ──────────────────────────────────────────────────────────────────────────
    # Gather all searchable medical text from prescription and comparison items
    medical_tokens = []
    if prescription_json.get("diagnosis"):
        medical_tokens.append(str(prescription_json["diagnosis"]))
    if prescription_json.get("symptoms"):
        medical_tokens.extend([str(s) for s in prescription_json["symptoms"]])
    if prescription_json.get("procedures"):
        medical_tokens.extend([str(p) for p in prescription_json["procedures"]])
    if prescription_json.get("medicines"):
        for m in prescription_json["medicines"]:
            if isinstance(m, dict):
                medical_tokens.append(m.get("name") or "")
            else:
                medical_tokens.append(str(m))
    if prescription_json.get("medicalTests"):
        for t in prescription_json["medicalTests"]:
            if isinstance(t, dict):
                medical_tokens.append(t.get("name") or "")
            else:
                medical_tokens.append(str(t))
    if prescription_json.get("manualText"):
        medical_tokens.append(str(prescription_json["manualText"]))

    full_medical_corpus = " ".join(medical_tokens).lower()

    # Check against 14 Specific 2-Year Waiting Period conditions
    detected_waiting_conditions: List[Dict[str, Any]] = []
    for cond in SPECIFIC_2_YEAR_WAITING_CONDITIONS:
        matched_kw = [kw for kw in cond["keywords"] if kw in full_medical_corpus]
        if matched_kw:
            detected_waiting_conditions.append({
                "condition": cond["category"],
                "matchedKeyword": matched_kw[0],
                "standardWaitMonths": cond["standardWaitMonths"],
                "rule": cond["standardRule"],
                "advisory": f"Under standard non-life health insurance rules, {cond['category']} carries a 24-month specific waiting period unless your policy includes a Day 1 waiver or continuous coverage > 2 years.",
                "source": {
                    "document": "2 YEAR & Permanent Exclusion-2.pdf",
                    "page": 1,
                    "section": "2 YEAR'S SPECIFIC WAITING PERIODS",
                },
                "authorityLevel": "REFERENCE_BENCHMARK",
            })

    # Check against 32 Permanent Exclusions Master Catalog
    detected_permanent_exclusions: List[Dict[str, Any]] = []
    for excl in PERMANENT_EXCLUSIONS_CATALOG:
        matched_excl_kw = [kw for kw in excl["keywords"] if kw in full_medical_corpus]
        if matched_excl_kw:
            detected_permanent_exclusions.append({
                "exclusionNumber": excl["num"],
                "title": excl["name"],
                "matchedKeyword": matched_excl_kw[0],
                "warning": f"Matches Permanent Exclusion #{excl['num']} ({excl['name']}). Standard health policies permanently exclude this expense from claim admissibility.",
                "source": {
                    "document": "2 YEAR & Permanent Exclusion-2.pdf",
                    "page": 2,
                    "section": "PERMANENT EXCLUSION - NOT COVERED",
                },
                "authorityLevel": "REFERENCE_BENCHMARK",
            })

    # ──────────────────────────────────────────────────────────────────────────
    # 4. BENCHMARK ACTIONABLE TAKEAWAYS FOR CLAIMANT
    # ──────────────────────────────────────────────────────────────────────────
    actionable_takeaways: List[str] = []

    if detected_waiting_conditions:
        actionable_takeaways.append(
            f"Waiting Period Notice: Condition '{detected_waiting_conditions[0]['condition']}' detected. Ensure continuous policy renewal records (past 2+ policy years) are submitted with the claim."
        )

    if detected_permanent_exclusions:
        actionable_takeaways.append(
            f"Exclusion Alert: '{detected_permanent_exclusions[0]['title']}' is normally excluded unless supported by emergency/accident trauma or a specific rider."
        )

    actionable_takeaways.append(
        "Consumables Alert: Request the hospital to provide an itemized bill separating non-payable consumables (gloves, sanitizers, administrative fees) for accurate out-of-pocket tracking."
    )
    actionable_takeaways.append(
        "Cashless Hospital Intimation: Intimate the insurer/TPA at least 48 hours in advance for planned admissions (or within 24 hours for emergencies) to secure cashless pre-authorization."
    )

    return {
        "benchmarkName": BENCHMARK_POLICY_SPEC["benchmarkName"],
        "industryOverview": INDUSTRY_OVERVIEW,
        "insurerBenchmark": insurer_benchmark,
        "featureComparisons": feature_comparisons,
        "detectedWaitingConditions": detected_waiting_conditions,
        "detectedPermanentExclusions": detected_permanent_exclusions,
        "actionableTakeaways": actionable_takeaways,
        "hasWaitingPeriodFlag": len(detected_waiting_conditions) > 0,
        "hasPermanentExclusionFlag": len(detected_permanent_exclusions) > 0,
        "contractualAuthorityNote": "Reference rules derived from Knowledge Base standards. Specific policy wording remains highest contractual authority.",
    }


async def generate_knowledge_base_comparison(
    policy_json: Optional[Dict[str, Any]],
    prescription_json: Optional[Dict[str, Any]],
    coverage_analysis: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Asynchronously queries active MongoDB Knowledge Base models for dynamic,
    versioned rule evaluation and falls back smoothly to static benchmarks if DB is uninitialized.
    """
    try:
        from app.services.knowledge_base.policy_knowledge_service import PolicyKnowledgeService
        kb_eval = await PolicyKnowledgeService.evaluate_claim_against_knowledge_base(
            policy_data=policy_json or {},
            prescription_data=prescription_json or {},
        )
        base_comparison = generate_reference_comparison(policy_json, prescription_json, coverage_analysis)
        if kb_eval.get("detectedWaitingConditions"):
            base_comparison["detectedWaitingConditions"] = kb_eval["detectedWaitingConditions"]
            base_comparison["hasWaitingPeriodFlag"] = True
        if kb_eval.get("detectedPermanentExclusions"):
            base_comparison["detectedPermanentExclusions"] = kb_eval["detectedPermanentExclusions"]
            base_comparison["hasPermanentExclusionFlag"] = True
        base_comparison["knowledgeBaseEvaluation"] = kb_eval
        return base_comparison
    except Exception:
        return generate_reference_comparison(policy_json, prescription_json, coverage_analysis)

