"""
Privacy and De-Identification Service for Claim Cases.
Strips all Personally Identifiable Information (PII) including patient names,
policyholder names, policy numbers, member IDs, contact numbers, email addresses,
and doctor names before storing or indexing cases into the trusted expert knowledge base.
"""

import re
from typing import Dict, Any, List
from app.models.expert_review import CaseEvidence


class CaseDeidentifier:
    """Removes patient and policyholder identifiers to preserve regulatory privacy."""

    # Patterns for regex-based identification scrubbing
    PHONE_REGEX = re.compile(r"(\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}")
    EMAIL_REGEX = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
    POLICY_NUM_REGEX = re.compile(r"\b(?:POL|NB|STAR|HDFC|ICICI|MAX)[\w\d\-]{5,20}\b", re.IGNORECASE)
    AADHAAR_REGEX = re.compile(r"\b\d{4}\s\d{4}\s\d{4}\b")
    PAN_REGEX = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")

    @classmethod
    def scrub_text(cls, text: str, known_names: List[str] = None) -> str:
        """
        Removes PII, phone numbers, emails, policy numbers, and known personal names.
        """
        if not text or not text.strip():
            return ""

        scrubbed = str(text)

        # 1. Strip emails, phones, IDs
        scrubbed = cls.EMAIL_REGEX.sub("[REDACTED_EMAIL]", scrubbed)
        scrubbed = cls.PHONE_REGEX.sub("[REDACTED_PHONE]", scrubbed)
        scrubbed = cls.AADHAAR_REGEX.sub("[REDACTED_ID]", scrubbed)
        scrubbed = cls.PAN_REGEX.sub("[REDACTED_PAN]", scrubbed)
        scrubbed = cls.POLICY_NUM_REGEX.sub("[REDACTED_POLICY_NO]", scrubbed)

        # 2. Strip known patient or member names
        if known_names:
            for name in known_names:
                clean_name = str(name).strip()
                if len(clean_name) > 2:
                    pattern = re.compile(re.escape(clean_name), re.IGNORECASE)
                    scrubbed = pattern.sub("[PATIENT]", scrubbed)

        # 3. Strip common honorific person patterns (e.g. Mr. John Doe -> [PATIENT])
        honorific_pattern = re.compile(
            r"\b(?:Mr\.|Mrs\.|Ms\.|Dr\.|Shri|Smt\.)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b"
        )
        scrubbed = honorific_pattern.sub(r"[INDIVIDUAL]", scrubbed)

        return scrubbed.strip()

    @classmethod
    def deidentify_claim_data(
        cls,
        policy_json: Dict[str, Any],
        prescription_json: Dict[str, Any],
        raw_text: str = "",
    ) -> Dict[str, Any]:
        """
        Extracts known identifiers from policy and prescription JSON and scrubs raw_text.
        """
        known_names: List[str] = []
        for k in ("patientName", "policyholderName", "doctorName", "primaryInsured"):
            val = (prescription_json or {}).get(k) or (policy_json or {}).get(k)
            if val and isinstance(val, str) and len(val.strip()) > 2:
                known_names.append(val.strip())
                # Also split into first/last name parts if long enough
                parts = val.strip().split()
                for p in parts:
                    if len(p) > 2 and p.lower() not in ("kumar", "singh", "sharma"):
                        known_names.append(p)

        sanitized_text = cls.scrub_text(raw_text, known_names)
        sanitized_policy = cls.scrub_text(str((policy_json or {}).get("policyNumber", "")), known_names)

        return {
            "sanitizedText": sanitized_text,
            "sanitizedPolicyNumber": sanitized_policy,
        }

    @classmethod
    def deidentify_case_data(cls, raw_case: Dict[str, Any]) -> Dict[str, Any]:
        """
        Produces a completely de-identified case dictionary ready for ApprovedExpertCase.
        """
        # Gather all personal names associated with this case to scrub
        known_names: List[str] = []
        for key in ("patientName", "policyholderName", "insuredName", "doctorName", "primaryInsured"):
            val = raw_case.get(key)
            if val and isinstance(val, str) and val.strip().lower() not in ("none", "null", "---", "unknown"):
                known_names.append(val.strip())

        # Also extract from insured members list if present
        for m in (raw_case.get("insuredMembers") or []):
            if isinstance(m, dict) and m.get("name"):
                known_names.append(m["name"].strip())
            elif isinstance(m, str):
                known_names.append(m.strip())

        # Clean diagnosis and treatment
        diag_clean = cls.scrub_text(raw_case.get("diagnosis", ""), known_names)
        treat_clean = cls.scrub_text(raw_case.get("treatment", ""), known_names)
        exp_clean = cls.scrub_text(raw_case.get("explanation", "") or raw_case.get("aiExplanation", ""), known_names)

        # Clean evidence snippets
        cleaned_evidence: List[Dict[str, Any]] = []
        for ev in (raw_case.get("evidence") or []):
            if isinstance(ev, dict):
                cleaned_evidence.append({
                    "documentName": ev.get("documentName") or ev.get("source_document") or "Policy_Document.pdf",
                    "pageNumber": ev.get("pageNumber") or ev.get("page"),
                    "section": ev.get("section") or "",
                    "clauseId": ev.get("clauseId") or ev.get("clause_id") or "",
                    "textSnippet": cls.scrub_text(ev.get("textSnippet") or ev.get("text_snippet") or ev.get("text") or "", known_names),
                    "authorityLevel": ev.get("authorityLevel") or ev.get("authority_level") or "CONTRACTUAL_POLICY_WORDING",
                })
            elif isinstance(ev, CaseEvidence):
                cleaned_evidence.append({
                    "documentName": ev.document_name,
                    "pageNumber": ev.page_number,
                    "section": ev.section,
                    "clauseId": ev.clause_id,
                    "textSnippet": cls.scrub_text(ev.text_snippet or "", known_names),
                    "authorityLevel": ev.authority_level,
                })

        return {
            "caseId": raw_case.get("caseId") or raw_case.get("case_id"),
            "insurer": raw_case.get("insurer") or "All",
            "product": raw_case.get("product") or "Standard Benchmark",
            "variant": raw_case.get("variant") or "All",
            "policyVersion": raw_case.get("policyVersion") or raw_case.get("policy_version") or "1.0",
            "diagnosis": diag_clean,
            "normalizedDiagnosis": raw_case.get("normalizedDiagnosis") or raw_case.get("normalized_diagnosis") or "GENERAL",
            "treatment": treat_clean,
            "normalizedTreatment": raw_case.get("normalizedTreatment") or raw_case.get("normalized_treatment") or "",
            "policyAgeMonths": raw_case.get("policyAgeMonths") or raw_case.get("policy_age_months"),
            "applicableRuleTypes": raw_case.get("applicableRuleTypes") or raw_case.get("applicable_rule_types") or [],
            "relevantRuleIds": raw_case.get("relevantRuleIds") or raw_case.get("relevant_rule_ids") or [],
            "decision": raw_case.get("finalExpertDecision") or raw_case.get("decision") or raw_case.get("aiDecision") or "COVERED",
            "reasonCode": raw_case.get("reasonCode") or raw_case.get("reason_code") or "POLICY_ACTIVE",
            "expertExplanation": exp_clean,
            "evidence": cleaned_evidence,
            "status": "APPROVED",
            "isActive": True,
        }
