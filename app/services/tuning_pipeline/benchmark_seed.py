"""
Reference Evaluation Benchmark Seed Dataset.
Provides standardized, curated reference evaluation cases covering all 12 insurance evaluation categories:
1. COVERED
2. PERMANENT_EXCLUSION
3. WAITING_PERIOD_ACTIVE
4. WAITING_PERIOD_COMPLETED
5. BENEFIT_UNAVAILABLE
6. LIMIT_EXCEEDED
7. NETWORK_MISMATCH
8. GEOGRAPHICAL_RESTRICTION
9. MANUAL_REVIEW
10. AMBIGUOUS_CASE
11. CONFLICTING_RULES
12. COMPLEX_CLAIM
"""

from typing import List
from app.models.dataset_tuning import (
    TrainingExample,
    TrainingExampleInput,
    TrainingExpectedOutput,
    EvidenceCitation,
    EvaluationCategory,
    DatasetSplitType,
)


def get_standard_benchmark_cases() -> List[TrainingExample]:
    """Returns standard reference evaluation cases spanning all 12 evaluation categories."""
    cases: List[TrainingExample] = []

    # 1. COVERED: Emergency Appendectomy
    cases.append(TrainingExample(
        exampleId="EVAL-BENCH-0001",
        category=EvaluationCategory.COVERED,
        split=DatasetSplitType.TEST,
        input=TrainingExampleInput(
            diagnosis="Acute Appendicitis",
            normalizedDiagnosis="APPENDICITIS",
            treatment="Emergency Laparoscopic Appendectomy",
            policyDurationMonths=14,
            insurer="Star Health and Allied Insurance Co Ltd",
            product="Family Health Optima",
            variant="Gold",
            policyVersion="2.0",
            applicableRules=["POLICY_VALIDITY", "WAITING_PERIOD"],
            retrievedEvidence=[
                EvidenceCitation(
                    documentName="Star_FHO_Wording.pdf",
                    pageNumber=3,
                    section="Section 1. Inpatient Care",
                    textSnippet="Emergency surgical procedures for acute conditions are covered from day 31.",
                )
            ],
            claimAmount=85000.0,
        ),
        expectedOutput=TrainingExpectedOutput(
            status="COVERED",
            reasonCode="POLICY_ACTIVE",
            explanation="Emergency surgery for acute appendicitis is covered after initial 30-day waiting period.",
            manualReviewRequired=False,
        ),
    ))

    # 2. PERMANENT_EXCLUSION: Cosmetic Rhinoplasty
    cases.append(TrainingExample(
        exampleId="EVAL-BENCH-0002",
        category=EvaluationCategory.PERMANENT_EXCLUSION,
        split=DatasetSplitType.TEST,
        input=TrainingExampleInput(
            diagnosis="Nasal Asymmetry and Aesthetic Correction",
            normalizedDiagnosis="COSMETIC_SURGERY",
            treatment="Cosmetic Rhinoplasty",
            policyDurationMonths=36,
            insurer="Niva Bupa Health Insurance Company Limited",
            product="ReAssure 3.0",
            variant="Titanium",
            policyVersion="1.0",
            applicableRules=["PERMANENT_EXCLUSION"],
            retrievedEvidence=[
                EvidenceCitation(
                    documentName="Standard_Exclusions.pdf",
                    pageNumber=12,
                    section="Code Excl 05",
                    textSnippet="Plastic surgery or cosmetic surgery for aesthetic purposes is permanently excluded.",
                )
            ],
            claimAmount=120000.0,
        ),
        expectedOutput=TrainingExpectedOutput(
            status="NOT_COVERED",
            reasonCode="PERMANENT_EXCLUSION",
            explanation="Cosmetic rhinoplasty for aesthetic correction is permanently excluded under statutory IRDAI Code Excl 05.",
            manualReviewRequired=False,
        ),
    ))

    # 3. WAITING_PERIOD_ACTIVE: Cataract at 17 Months (24 Month Waiting Period)
    cases.append(TrainingExample(
        exampleId="EVAL-BENCH-0003",
        category=EvaluationCategory.WAITING_PERIOD_ACTIVE,
        split=DatasetSplitType.TEST,
        input=TrainingExampleInput(
            diagnosis="Senile Cataract",
            normalizedDiagnosis="CATARACT",
            treatment="Phacoemulsification with Foldable Lens",
            policyDurationMonths=17,
            insurer="Niva Bupa Health Insurance Company Limited",
            product="ReAssure 3.0",
            variant="Classic",
            policyVersion="1.0",
            applicableRules=["WAITING_PERIOD"],
            retrievedEvidence=[
                EvidenceCitation(
                    documentName="2_Year_Waiting_Period.pdf",
                    pageNumber=1,
                    section="Section 2. Specific Illness Waiting Period",
                    textSnippet="A waiting period of 24 months applies to Cataract surgery.",
                )
            ],
            claimAmount=45000.0,
        ),
        expectedOutput=TrainingExpectedOutput(
            status="NOT_COVERED",
            reasonCode="WAITING_PERIOD_ACTIVE",
            explanation="Cataract surgery requires 24 completed months of continuous coverage; policy age is only 17 months.",
            manualReviewRequired=False,
        ),
    ))

    # 4. WAITING_PERIOD_COMPLETED: Cataract at 28 Months
    cases.append(TrainingExample(
        exampleId="EVAL-BENCH-0004",
        category=EvaluationCategory.WAITING_PERIOD_COMPLETED,
        split=DatasetSplitType.TEST,
        input=TrainingExampleInput(
            diagnosis="Nuclear Cataract",
            normalizedDiagnosis="CATARACT",
            treatment="Phacoemulsification with IOL Implantation",
            policyDurationMonths=28,
            insurer="Niva Bupa Health Insurance Company Limited",
            product="ReAssure 3.0",
            variant="Classic",
            policyVersion="1.0",
            applicableRules=["WAITING_PERIOD"],
            retrievedEvidence=[
                EvidenceCitation(
                    documentName="2_Year_Waiting_Period.pdf",
                    pageNumber=1,
                    section="Section 2. Specific Illness Waiting Period",
                    textSnippet="A waiting period of 24 months applies to Cataract surgery.",
                )
            ],
            claimAmount=50000.0,
        ),
        expectedOutput=TrainingExpectedOutput(
            status="COVERED",
            reasonCode="WAITING_PERIOD_COMPLETED",
            explanation="Specific 2-year waiting period of 24 months satisfied (28 completed months active).",
            manualReviewRequired=False,
        ),
    ))

    # 5. BENEFIT_UNAVAILABLE: Air Ambulance in Classic Tier
    cases.append(TrainingExample(
        exampleId="EVAL-BENCH-0005",
        category=EvaluationCategory.BENEFIT_UNAVAILABLE,
        split=DatasetSplitType.TEST,
        input=TrainingExampleInput(
            diagnosis="Severe Polytrauma",
            normalizedDiagnosis="TRAUMA",
            treatment="Air Ambulance Emergency Transfer",
            policyDurationMonths=20,
            insurer="Niva Bupa Health Insurance Company Limited",
            product="ReAssure 3.0",
            variant="Classic",
            policyVersion="1.0",
            applicableRules=["BENEFIT"],
            retrievedEvidence=[
                EvidenceCitation(
                    documentName="Benefit_Schedule.pdf",
                    pageNumber=5,
                    section="Clause 3. Ambulance Services",
                    textSnippet="Air ambulance cover is not available under Classic plan tier.",
                )
            ],
            claimAmount=250000.0,
        ),
        expectedOutput=TrainingExpectedOutput(
            status="NOT_COVERED",
            reasonCode="BENEFIT_NOT_INCLUDED",
            explanation="Air ambulance benefit is not included in the Classic plan variant.",
            manualReviewRequired=False,
        ),
    ))

    # 6. LIMIT_EXCEEDED: Modern Robotic Surgery Exceeding Cap
    cases.append(TrainingExample(
        exampleId="EVAL-BENCH-0006",
        category=EvaluationCategory.LIMIT_EXCEEDED,
        split=DatasetSplitType.TEST,
        input=TrainingExampleInput(
            diagnosis="Localized Prostate Cancer",
            normalizedDiagnosis="PROSTATE_CANCER",
            treatment="Robotic Assisted Radical Prostatectomy",
            policyDurationMonths=25,
            insurer="Niva Bupa Health Insurance Company Limited",
            product="ReAssure 3.0",
            variant="Classic",
            policyVersion="1.0",
            applicableRules=["LIMIT", "SUB_LIMIT"],
            retrievedEvidence=[
                EvidenceCitation(
                    documentName="Modern_Treatments.pdf",
                    pageNumber=7,
                    section="Clause 4. Modern Treatment Procedures",
                    textSnippet="Modern treatment procedures are subject to a sub-limit of INR 1,00,000 per hospitalization.",
                )
            ],
            claimAmount=250000.0,
        ),
        expectedOutput=TrainingExpectedOutput(
            status="PARTIALLY_COVERED",
            reasonCode="LIMIT_EXCEEDED",
            eligibleAmount=100000.0,
            subLimitApplied=100000.0,
            explanation="Modern robotic surgery is capped at INR 1,00,000 under the Classic plan; remaining INR 1,50,000 is non-payable.",
            manualReviewRequired=False,
        ),
    ))

    # 7. NETWORK_MISMATCH: Non-Network Hospital on Network-Only Policy
    cases.append(TrainingExample(
        exampleId="EVAL-BENCH-0007",
        category=EvaluationCategory.NETWORK_MISMATCH,
        split=DatasetSplitType.TEST,
        input=TrainingExampleInput(
            diagnosis="Elective Knee Arthroscopy",
            normalizedDiagnosis="KNEE_SURGERY",
            treatment="Elective Arthroscopic Meniscectomy",
            policyDurationMonths=30,
            insurer="Care Health Insurance",
            product="Care Network Select",
            variant="Network-Only",
            policyVersion="1.0",
            applicableRules=["NETWORK"],
            retrievedEvidence=[
                EvidenceCitation(
                    documentName="Network_Rules.pdf",
                    pageNumber=2,
                    section="Section 8. Provider Network",
                    textSnippet="Elective planned treatments are covered strictly in designated Tier-1 network hospitals.",
                )
            ],
            hospitalType="Non-Network",
            claimAmount=75000.0,
        ),
        expectedOutput=TrainingExpectedOutput(
            status="MANUAL_REVIEW",
            reasonCode="NETWORK_RESTRICTION",
            explanation="Planned elective treatment at a non-network provider requires pre-authorization manual review under the Network-Only policy.",
            manualReviewRequired=True,
        ),
    ))

    # 8. GEOGRAPHICAL_RESTRICTION: Treatment in Singapore on Domestic Policy
    cases.append(TrainingExample(
        exampleId="EVAL-BENCH-0008",
        category=EvaluationCategory.GEOGRAPHICAL_RESTRICTION,
        split=DatasetSplitType.TEST,
        input=TrainingExampleInput(
            diagnosis="Coronary Artery Disease",
            normalizedDiagnosis="CARDIAC",
            treatment="Coronary Angioplasty (Singapore General Hospital)",
            policyDurationMonths=36,
            insurer="Star Health and Allied Insurance Co Ltd",
            product="Family Health Optima",
            variant="Domestic",
            policyVersion="2.0",
            applicableRules=["GEOGRAPHICAL"],
            retrievedEvidence=[
                EvidenceCitation(
                    documentName="Territorial_Limits.pdf",
                    pageNumber=4,
                    section="Section 11. Territorial Scope",
                    textSnippet="Covered medical expenses must be incurred strictly within the territorial borders of India.",
                )
            ],
            claimAmount=650000.0,
        ),
        expectedOutput=TrainingExpectedOutput(
            status="NOT_COVERED",
            reasonCode="GEOGRAPHIC_RESTRICTION",
            explanation="Treatment incurred outside India is not covered under this domestic policy.",
            manualReviewRequired=False,
        ),
    ))

    # 9. MANUAL_REVIEW: Borderline Inpatient Stay Clarification
    cases.append(TrainingExample(
        exampleId="EVAL-BENCH-0009",
        category=EvaluationCategory.MANUAL_REVIEW,
        split=DatasetSplitType.TEST,
        input=TrainingExampleInput(
            diagnosis="Acute Viral Gastroenteritis",
            normalizedDiagnosis="GASTROENTERITIS",
            treatment="Day-care IV Rehydration for 3 hours",
            policyDurationMonths=12,
            insurer="Star Health and Allied Insurance Co Ltd",
            product="Family Health Optima",
            variant="Gold",
            policyVersion="2.0",
            applicableRules=["HOSPITALIZATION"],
            retrievedEvidence=[
                EvidenceCitation(
                    documentName="Hospitalization_Criteria.pdf",
                    pageNumber=6,
                    section="Section 5. Day Care vs Inpatient",
                    textSnippet="Requires 24 hours hospitalization unless procedure is listed on approved 24-hour daycare schedule.",
                )
            ],
            claimAmount=18000.0,
        ),
        expectedOutput=TrainingExpectedOutput(
            status="NOT_COVERED",
            reasonCode="HOSPITALIZATION_CRITERIA_NOT_MET",
            explanation="Stay of 3 hours for viral gastroenteritis does not satisfy the 24-hour mandatory inpatient hospitalization requirement.",
            manualReviewRequired=False,
        ),
    ))

    # 10. AMBIGUOUS_CASE: Generalized Malaise Without Clinical Diagnosis
    cases.append(TrainingExample(
        exampleId="EVAL-BENCH-0010",
        category=EvaluationCategory.AMBIGUOUS_CASE,
        split=DatasetSplitType.TEST,
        input=TrainingExampleInput(
            diagnosis="Generalized fatigue, weakness and body pain",
            normalizedDiagnosis="AMBIGUOUS_SYMPTOM",
            treatment="General tonic injections and multivitamins",
            policyDurationMonths=15,
            insurer="Niva Bupa Health Insurance Company Limited",
            product="ReAssure 3.0",
            variant="Classic",
            policyVersion="1.0",
            applicableRules=["POLICY_VALIDITY"],
            retrievedEvidence=[
                EvidenceCitation(
                    documentName="Exclusions.pdf",
                    pageNumber=8,
                    section="Section 4. Diagnostic & Evaluation",
                    textSnippet="Admission primarily for diagnostic or unconfirmed generalized symptoms is not covered.",
                )
            ],
            claimAmount=12000.0,
        ),
        expectedOutput=TrainingExpectedOutput(
            status="MANUAL_REVIEW",
            reasonCode="AMBIGUOUS_DIAGNOSIS",
            explanation="Generalized symptom description lacks definite clinical illness diagnosis; manual review required.",
            manualReviewRequired=True,
        ),
    ))

    # 11. CONFLICTING_RULES: Contradictory Endorsement Clauses
    cases.append(TrainingExample(
        exampleId="EVAL-BENCH-0011",
        category=EvaluationCategory.CONFLICTING_RULES,
        split=DatasetSplitType.TEST,
        input=TrainingExampleInput(
            diagnosis="Bariatric Metabolic Surgery",
            normalizedDiagnosis="OBESITY_TREATMENT",
            treatment="Laparoscopic Sleeve Gastrectomy",
            policyDurationMonths=40,
            insurer="Care Health Insurance",
            product="Care Supreme",
            variant="Elite",
            policyVersion="2.0",
            applicableRules=["PERMANENT_EXCLUSION", "BENEFIT"],
            retrievedEvidence=[
                EvidenceCitation(
                    documentName="Base_Policy.pdf",
                    pageNumber=10,
                    section="Exclusion #6",
                    textSnippet="Obesity and bariatric treatment is permanently excluded.",
                ),
                EvidenceCitation(
                    documentName="Endorsement_Rider.pdf",
                    pageNumber=1,
                    section="Rider Clause 2",
                    textSnippet="Bariatric surgery is covered after 36 months if BMI exceeds 35 with severe co-morbidities.",
                )
            ],
            claimAmount=320000.0,
        ),
        expectedOutput=TrainingExpectedOutput(
            status="MANUAL_REVIEW",
            reasonCode="CONFLICTING_RULES",
            explanation="Base policy exclusion conflicts with endorsement rider coverage clause; manual adjudication required.",
            manualReviewRequired=True,
        ),
    ))

    # 12. COMPLEX_CLAIM: Multi-Procedure Inpatient with Room Rent Proportionate Capping & Co-Pay
    cases.append(TrainingExample(
        exampleId="EVAL-BENCH-0012",
        category=EvaluationCategory.COMPLEX_CLAIM,
        split=DatasetSplitType.TEST,
        input=TrainingExampleInput(
            diagnosis="Bilateral Osteoarthritis with Hypertension",
            normalizedDiagnosis="KNEE_ARTHROPLASTY",
            treatment="Bilateral Total Knee Replacement",
            policyDurationMonths=48,
            insurer="Star Health and Allied Insurance Co Ltd",
            product="Senior Citizens Red Carpet",
            variant="Classic",
            policyVersion="1.0",
            applicableRules=["WAITING_PERIOD", "LIMIT", "CO_PAY"],
            retrievedEvidence=[
                EvidenceCitation(
                    documentName="Senior_Red_Carpet.pdf",
                    pageNumber=2,
                    section="Clause 5. Co-Pay and Room Rent",
                    textSnippet="Room rent capped at 1% of Sum Insured. A 30% co-pay applies to all admissible claims.",
                )
            ],
            roomType="Deluxe Suite",
            claimAmount=400000.0,
        ),
        expectedOutput=TrainingExpectedOutput(
            status="COVERED",
            reasonCode="COPAY_APPLICABLE",
            coPayPercent=30.0,
            subLimitApplied=200000.0,
            explanation="Covered subject to 30% mandatory co-pay and proportionate room rent deduction for deluxe suite accommodation.",
            manualReviewRequired=False,
        ),
    ))

    return cases
