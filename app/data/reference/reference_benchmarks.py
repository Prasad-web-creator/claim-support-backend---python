"""
Reference benchmark dataset extracted from:
1. Snapshot.pdf & Mail - ClaimSupport 1.pdf: Marsh India / GIC Council Market Snapshot & Solvency Ratios
2. Leaflet artwork.pdf & Mail - ClaimSupport 2.pdf: Niva Bupa ReAssure 3.0 Policy Specification & Benefits
3. 2 YEAR & Permanent Exclusion-2.pdf: 14 Specific 2-Year Waiting Periods & 32 Permanent Exclusions
"""

# ==============================================================================
# 1. INSURER MARKET & SOLVENCY DATA (Marsh India / GIC Council Apr-Jul 2026)
# ==============================================================================
INSURER_MARKET_BENCHMARK = {
    "the new india assurance co ltd": {
        "displayName": "The New India Assurance Co Ltd",
        "category": "Public General Insurer",
        "marketRank": 1,
        "marketSharePct": 14.0,
        "premiumCr": 17023,
        "growthRatePct": 4.0,
        "solvencyRatio": 1.84,
        "solvencyStatus": "Healthy",
        "healthSegmentPremiumCr": 9142,
    },
    "icici lombard general insurance co ltd": {
        "displayName": "ICICI Lombard General Insurance Co Ltd",
        "category": "Private General Insurer",
        "marketRank": 2,
        "marketSharePct": 9.0,
        "premiumCr": 10724,
        "growthRatePct": 5.0,
        "solvencyRatio": 2.70,
        "solvencyStatus": "Robust",
        "healthSegmentPremiumCr": 3811,
    },
    "united india insurance co ltd": {
        "displayName": "United India Insurance Co Ltd",
        "category": "Public General Insurer",
        "marketRank": 3,
        "marketSharePct": 7.0,
        "premiumCr": 8572,
        "growthRatePct": 9.0,
        "solvencyRatio": -1.36,
        "solvencyStatus": "Stressed",
        "healthSegmentPremiumCr": 3651,
    },
    "tata aig general insurance co ltd": {
        "displayName": "Tata AIG General Insurance Co Ltd",
        "category": "Private General Insurer",
        "marketRank": 4,
        "marketSharePct": 7.0,
        "premiumCr": 8431,
        "growthRatePct": 31.0,
        "solvencyRatio": 1.91,
        "solvencyStatus": "Healthy",
        "healthSegmentPremiumCr": 2517,
    },
    "bajaj general insurance limited": {
        "displayName": "Bajaj Allianz General Insurance Co Ltd",
        "category": "Private General Insurer",
        "marketRank": 5,
        "marketSharePct": 7.0,
        "premiumCr": 8144,
        "growthRatePct": 12.0,
        "solvencyRatio": 3.02,
        "solvencyStatus": "Robust",
        "healthSegmentPremiumCr": 2807,
    },
    "the oriental insurance co ltd": {
        "displayName": "The Oriental Insurance Co Ltd",
        "category": "Public General Insurer",
        "marketRank": 6,
        "marketSharePct": 7.0,
        "premiumCr": 7928,
        "growthRatePct": 1.0,
        "solvencyRatio": None,
        "solvencyStatus": "Not Disclosed",
        "healthSegmentPremiumCr": 3171,
    },
    "national insurance co ltd": {
        "displayName": "National Insurance Co Ltd",
        "category": "Public General Insurer",
        "marketRank": 7,
        "marketSharePct": 5.0,
        "premiumCr": 5642,
        "growthRatePct": 6.0,
        "solvencyRatio": None,
        "solvencyStatus": "Not Disclosed",
        "healthSegmentPremiumCr": 2158,
    },
    "hdfc ergo general insurance co ltd": {
        "displayName": "HDFC ERGO General Insurance Co Ltd",
        "category": "Private General Insurer",
        "marketRank": 8,
        "marketSharePct": 5.0,
        "premiumCr": 5557,
        "growthRatePct": 20.0,
        "solvencyRatio": 2.07,
        "solvencyStatus": "Robust",
        "healthSegmentPremiumCr": 2708,
    },
    "sbi general insurance co ltd": {
        "displayName": "SBI General Insurance Co Ltd",
        "category": "Private General Insurer",
        "marketRank": 9,
        "marketSharePct": 4.0,
        "premiumCr": 5193,
        "growthRatePct": 15.0,
        "solvencyRatio": 1.90,
        "solvencyStatus": "Healthy",
        "healthSegmentPremiumCr": 1621,
    },
    "indusind general insurance company limited": {
        "displayName": "IndusInd General Insurance Co Ltd",
        "category": "Private General Insurer",
        "marketRank": 10,
        "marketSharePct": 3.0,
        "premiumCr": 3663,
        "growthRatePct": -15.0,
        "solvencyRatio": None,
        "solvencyStatus": "Not Disclosed",
        "healthSegmentPremiumCr": 1395,
    },
    "star health & allied insurance co ltd": {
        "displayName": "Star Health & Allied Insurance Co Ltd",
        "category": "Standalone Health Insurer (SAHI)",
        "marketRank": 1,
        "marketSharePct": 5.0,
        "premiumCr": 6091,
        "growthRatePct": 19.0,
        "solvencyRatio": 1.95,
        "solvencyStatus": "Healthy",
        "healthSegmentPremiumCr": 6048,
    },
    "care health insurance ltd": {
        "displayName": "Care Health Insurance Ltd",
        "category": "Standalone Health Insurer (SAHI)",
        "marketRank": 2,
        "marketSharePct": 3.0,
        "premiumCr": 4101,
        "growthRatePct": 42.0,
        "solvencyRatio": 1.82,
        "solvencyStatus": "Healthy",
        "healthSegmentPremiumCr": 4009,
    },
    "niva bupa health insurance company limited": {
        "displayName": "Niva Bupa Health Insurance Co Ltd",
        "category": "Standalone Health Insurer (SAHI)",
        "marketRank": 3,
        "marketSharePct": 3.0,
        "premiumCr": 3002,
        "growthRatePct": 32.0,
        "solvencyRatio": 1.76,
        "solvencyStatus": "Healthy",
        "healthSegmentPremiumCr": 2965,
    },
    "aditya birla health insurance co ltd": {
        "displayName": "Aditya Birla Health Insurance Co Ltd",
        "category": "Standalone Health Insurer (SAHI)",
        "marketRank": 4,
        "marketSharePct": 2.0,
        "premiumCr": 2595,
        "growthRatePct": 45.0,
        "solvencyRatio": 1.70,
        "solvencyStatus": "Healthy",
        "healthSegmentPremiumCr": 2457,
    },
    "manipalcigna health insurance co ltd": {
        "displayName": "ManipalCigna Health Insurance Co Ltd",
        "category": "Standalone Health Insurer (SAHI)",
        "marketRank": 5,
        "marketSharePct": 1.0,
        "premiumCr": 921,
        "growthRatePct": 35.0,
        "solvencyRatio": 1.65,
        "solvencyStatus": "Healthy",
        "healthSegmentPremiumCr": 902,
    },
    "go digit general insurance ltd": {
        "displayName": "Go Digit General Insurance Ltd",
        "category": "Private General Insurer",
        "marketRank": 11,
        "marketSharePct": 3.0,
        "premiumCr": 3244,
        "growthRatePct": -4.0,
        "solvencyRatio": 1.78,
        "solvencyStatus": "Healthy",
        "healthSegmentPremiumCr": 576,
    },
    "iffco-tokio general insurance co ltd": {
        "displayName": "IFFCO-Tokio General Insurance Co Ltd",
        "category": "Private General Insurer",
        "marketRank": 12,
        "marketSharePct": 3.0,
        "premiumCr": 3180,
        "growthRatePct": 11.0,
        "solvencyRatio": 1.68,
        "solvencyStatus": "Healthy",
        "healthSegmentPremiumCr": 372,
    },
    "acko general insurance ltd": {
        "displayName": "Acko General Insurance Ltd",
        "category": "Private General Insurer",
        "marketRank": 18,
        "marketSharePct": 1.0,
        "premiumCr": 1036,
        "growthRatePct": 43.0,
        "solvencyRatio": 1.88,
        "solvencyStatus": "Healthy",
        "healthSegmentPremiumCr": 550,
    },
    "zurich kotak mahindra general insurance co ltd": {
        "displayName": "Zurich Kotak Mahindra General Insurance Co Ltd",
        "category": "Private General Insurer",
        "marketRank": 20,
        "marketSharePct": 1.0,
        "premiumCr": 863,
        "growthRatePct": 20.0,
        "solvencyRatio": 4.84,
        "solvencyStatus": "Robust",
        "healthSegmentPremiumCr": 262,
    },
}

INDUSTRY_OVERVIEW = {
    "reportingPeriod": "April 2026 to July 2026",
    "totalNonLifePremiumCr": 119303,
    "overallGrowthRatePct": 9.0,
    "generalInsurersSharePct": 85.3,
    "healthInsurersSharePct": 14.1,
    "specializedInsurersSharePct": 0.6,
    "irdaiMinSolvencyNorm": 1.50,
}

# ==============================================================================
# 2. BENCHMARK POLICY SPECIFICATION: NIVA BUPA REASSURE 3.0 STANDARD
# ==============================================================================
BENCHMARK_POLICY_SPEC = {
    "benchmarkName": "Industry Gold-Standard Benchmark (Niva Bupa ReAssure 3.0)",
    "baseSumInsured": "Unlimited from Day 1",
    "roomRentCategory": "Any Room / No Capping (Elite/Black) or Twin Sharing (Select)",
    "modernTreatments": "Covered up to full Sum Insured (no individual sub-limits)",
    "hospitalizationHours": "Covered for 2+ hours (AYUSH 24+ hours)",
    "preHospitalizationDays": 60,
    "postHospitalizationDays": 180,
    "ambulanceRoad": "Up to full Sum Insured (or ₹2,000 in Basic)",
    "ambulanceAir": "Up to ₹5,00,000 per hospitalization",
    "consumablesCoverage": "Claim Safeguard+ (100% coverage of IRDAI Lists I, II, III, IV non-payable items)",
    "lockTheClock": "Pay entry-age premium until first claim paid",
    "cashBagCashback": "10% first renewal, 5% subsequent, +10% bonus for 3 claim-free years",
    "opdWellConsult": "Up to 5x premium coverage for OPD, pharmacy, diagnostics, dental, vision",
    "borderless": "Global treatment coverage up to ₹5 Cr with optional co-pays",
}

# ==============================================================================
# 3. 14 SPECIFIC 2-YEAR WAITING PERIOD CONDITIONS
# ==============================================================================
SPECIFIC_2_YEAR_WAITING_CONDITIONS = [
    {
        "category": "Cataract",
        "keywords": ["cataract", "phacoemulsification", "iol", "intraocular lens", "eye lens replacement"],
        "standardWaitMonths": 24,
        "standardRule": "Standard 24-month waiting period applies across non-life health policies unless waived by specific Day 1 rider."
    },
    {
        "category": "Stones in Biliary and Urinary Systems",
        "keywords": ["gallstone", "cholelithiasis", "kidney stone", "renal calculi", "ureteric calculus", "nephrolithiasis", "lithotripsy", "cholecystectomy", "eswl"],
        "standardWaitMonths": 24,
        "standardRule": "Biliary and urinary calculi surgeries are subject to a 24-month specific waiting period."
    },
    {
        "category": "Lumps and Cysts",
        "keywords": ["lump", "cyst", "sebaceous cyst", "ganglion", "dermoid", "lipoma", "excision biopsy"],
        "standardWaitMonths": 24,
        "standardRule": "Benign lumps and cysts have a standard 2-year waiting period."
    },
    {
        "category": "Surgery on Tonsils / Adenoids",
        "keywords": ["tonsil", "tonsillitis", "tonsillectomy", "adenoid", "adenoidectomy"],
        "standardWaitMonths": 24,
        "standardRule": "Tonsillectomy and adenoid surgery are excluded for the first 24 months."
    },
    {
        "category": "Arthritis, Rheumatism & Spondylosis",
        "keywords": ["arthritis", "osteoarthritis", "rheumatoid", "rheumatism", "spondylosis", "cervical spondylosis", "lumbar spondylosis", "slip disc", "sciatica"],
        "standardWaitMonths": 24,
        "standardRule": "Degenerative joint/spinal conditions have a 24-month specific waiting period."
    },
    {
        "category": "Fissure, Fistula & Haemorrhoids",
        "keywords": ["fissure", "fistula", "haemorrhoids", "hemorrhoids", "piles", "anal fissure", "fistulectomy", "hemorrhoidectomy"],
        "standardWaitMonths": 24,
        "standardRule": "Anal canal benign disorders are subject to a 2-year waiting period."
    },
    {
        "category": "Hernia & Hydrocele",
        "keywords": ["hernia", "inguinal hernia", "umbilical hernia", "ventral hernia", "incisional hernia", "herniorrhaphy", "hernioplasty", "hydrocele"],
        "standardWaitMonths": 24,
        "standardRule": "All forms of hernia and hydrocele require 24 months continuous coverage."
    },
    {
        "category": "Sinusitis & Deviated Nasal Septum",
        "keywords": ["sinusitis", "dns", "septoplasty", "fess", "functional endoscopic sinus surgery"],
        "standardWaitMonths": 24,
        "standardRule": "Sinus surgeries are subject to a 24-month waiting period."
    },
    {
        "category": "Knee / Hip Joint Replacement",
        "keywords": ["joint replacement", "knee replacement", "hip replacement", "tkr", "thr", "arthroplasty"],
        "standardWaitMonths": 24,
        "standardRule": "Non-accidental joint replacements carry a 2-year waiting period."
    },
    {
        "category": "Varicose Veins",
        "keywords": ["varicose", "varicose veins", "vein stripping", "endovenous laser", "sclerotherapy"],
        "standardWaitMonths": 24,
        "standardRule": "Treatment of varicose veins and ulceration is subject to a 2-year waiting period."
    },
    {
        "category": "Benign Prostate Hypertrophy (BPH)",
        "keywords": ["bph", "prostate", "prostatomegaly", "turp", "transurethral resection"],
        "standardWaitMonths": 24,
        "standardRule": "BPH treatment is subject to a 2-year specific waiting period."
    },
    {
        "category": "Benign Hysterectomy & Gynaecological Conditions",
        "keywords": ["hysterectomy", "fibroid", "uterine fibroid", "myomectomy", "endometriosis", "ovarian cystectomy"],
        "standardWaitMonths": 24,
        "standardRule": "Hysterectomy for any benign disorder carries a 24-month waiting period."
    },
]

# ==============================================================================
# 4. 32 PERMANENT EXCLUSIONS MASTER CHECKLIST
# ==============================================================================
PERMANENT_EXCLUSIONS_CATALOG = [
    {"num": 1, "name": "Investigation & Evaluation Only", "keywords": ["investigation only", "evaluation only", "diagnostic admission", "routine checkup", "check-up only"]},
    {"num": 2, "name": "Rest Cure, Rehabilitation & Respite Care", "keywords": ["rest cure", "rehabilitation center", "respite care", "convalescence"]},
    {"num": 3, "name": "Obesity & Weight Control", "keywords": ["obesity", "bariatric", "weight loss", "gastric bypass", "weight control"]},
    {"num": 4, "name": "Change-of-Gender Treatments", "keywords": ["gender reassignment", "gender change", "sex change"]},
    {"num": 5, "name": "Cosmetic or Plastic Surgery", "keywords": ["cosmetic", "plastic surgery", "rhinoplasty", "aesthetic", "liposuction", "botox"]},
    {"num": 6, "name": "Hazardous or Adventure Sports", "keywords": ["adventure sports", "skydiving", "scuba diving", "mountaineering", "racing"]},
    {"num": 7, "name": "Breach of Law", "keywords": ["criminal act", "breach of law", "illegal activity"]},
    {"num": 8, "name": "Excluded / Blacklisted Providers", "keywords": ["blacklisted hospital", "excluded provider"]},
    {"num": 9, "name": "Alcoholism & Substance Abuse", "keywords": ["alcoholism", "drug abuse", "substance abuse", "addiction", "detoxification"]},
    {"num": 10, "name": "Health Hydros, Nature Cure & Spas", "keywords": ["nature cure", "spa", "hydrotherapy", "ayurvedic massage spa", "wellness retreat"]},
    {"num": 11, "name": "Dietary Supplements Purchased OTC", "keywords": ["otc supplement", "dietary supplement without prescription", "protein powder", "nutraceutical"]},
    {"num": 12, "name": "Refractive Error (Lasik < 7.5 D)", "keywords": ["refractive error", "lasik", "prk", "spectacle correction", "contoura"]},
    {"num": 13, "name": "Unproven & Experimental Treatments", "keywords": ["unproven", "experimental", "stem cell unauthorized", "clinical trial"]},
    {"num": 14, "name": "Sterility and Infertility", "keywords": ["infertility", "ivf", "icsi", "surrogacy", "sterility", "tubectomy reversal", "vasectomy reversal"]},
    {"num": 15, "name": "Maternity Expenses (Unless Covered by Rider)", "keywords": ["maternity", "normal delivery", "caesarean", "c-section", "childbirth", "prenatal", "postnatal"]},
    {"num": 16, "name": "Hospital Stay Charges Not Expressly Covered", "keywords": ["admission fee", "registration fee", "service charge", "attendant charge"]},
    {"num": 17, "name": "Circumcision (Unless for Disease)", "keywords": ["circumcision", "phimosis circumcision non-medical"]},
    {"num": 18, "name": "War, Conflict & Disaster", "keywords": ["war", "nuclear", "terrorism", "radiation", "civil commotion"]},
    {"num": 19, "name": "External Congenital Anomaly", "keywords": ["congenital anomaly", "birth defect", "external congenital"]},
    {"num": 20, "name": "Routine Dental / Oral Treatment", "keywords": ["dental", "teeth", "tooth extraction", "root canal", "rct", "orthodontic", "dentures"]},
    {"num": 21, "name": "Hormone Replacement Therapy (HRT)", "keywords": ["hormone replacement", "hrt"]},
    {"num": 22, "name": "Multifocal Lenses & Home Ambulatory Equipment", "keywords": ["multifocal lens", "bipap home", "cpap home", "wheelchair", "crutches", "hearing aid"]},
    {"num": 23, "name": "Sexually Transmitted Infections (Except HIV/AIDS)", "keywords": ["std", "syphilis", "gonorrhea", "chlamydia", "genital herpes"]},
    {"num": 24, "name": "Sleep Disorders", "keywords": ["sleep apnea", "snoring", "polysomnography sleep disorder"]},
    {"num": 25, "name": "Medical Services Outside India", "keywords": ["treatment abroad", "overseas medical", "foreign hospital"]},
    {"num": 26, "name": "Outpatient (OPD) Treatment (Unless Covered by Rider)", "keywords": ["opd consultation", "outpatient prescription", "day clinic"]},
    {"num": 27, "name": "Unrecognized Physician or Hospital", "keywords": ["unrecognized hospital", "quack", "unlicensed practitioner"]},
    {"num": 28, "name": "Non-Clinical Stress, Work Pressure & Academic Anxiety", "keywords": ["work pressure", "academic stress", "relationship difficulties", "acculturation"]},
    {"num": 29, "name": "Intentional Self-Inflicted Injury & Suicide Attempt", "keywords": ["suicide", "self harm", "intentional injury"]},
    {"num": 30, "name": "Neuro-developmental Delays & Disorders", "keywords": ["neurodevelopmental delay", "adhd", "learning disability"]},
    {"num": 31, "name": "Mental Retardation / Intellectual Disability", "keywords": ["mental retardation", "intellectual disability"]},
    {"num": 32, "name": "Artificial Life Maintenance (Brain Dead)", "keywords": ["brain dead", "vegetative state life support"]},
]
