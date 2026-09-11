"""
Clinical Condition & Procedure Normalization Service.
Normalizes diverse medical terminology and clinical phrasing into canonical
insurance condition codes while preserving original text, confidence scores,
clinical categories, and source entity tags.
"""

import re
from typing import List, Dict, Any, Optional
from app.services.rule_engine.models import NormalizedCondition


class ConditionNormalizer:
    """
    Deterministically extracts and normalizes medical diagnoses, procedures,
    symptoms, and medicines into canonical insurance taxonomy.
    """

    # Mapping from canonical code to (Category, Default Confidence, Regex Pattern List)
    CANONICAL_PATTERNS = {
        "CATARACT": (
            "OPHTHALMIC",
            0.95,
            [
                r"\bcataract\b",
                r"\bphacoemulsification\b",
                r"\bphaco\b",
                r"\biol\s*implantation\b",
                r"\bsenile\s*cataract\b",
                r"\bbilateral\s*cataract\b",
                r"\bnuclear\s*sclerosis\b",
            ],
        ),
        "CHOLELITHIASIS": (
            "SURGICAL",
            0.95,
            [
                r"\bcholelithiasis\b",
                r"\bgall\s*stones?\b",
                r"\bgall\s*bladder\s*stones?\b",
                r"\bcholecystitis\b",
                r"\bcholecystectomy\b",
                r"\blap\s*chole\b",
            ],
        ),
        "KNEE_ARTHROPLASTY": (
            "SURGICAL",
            0.95,
            [
                r"\btkr\b",
                r"\btotal\s*knee\s*replacement\b",
                r"\bknee\s*osteoarthritis\b",
                r"\bknee\s*arthroplasty\b",
                r"\bknee\s*replacement\b",
                r"\bosteoarthritis\s*knee\b",
            ],
        ),
        "HERNIA": (
            "SURGICAL",
            0.95,
            [
                r"\bhernia\b",
                r"\binguinal\s*hernia\b",
                r"\bumbilical\s*hernia\b",
                r"\bventral\s*hernia\b",
                r"\bincisional\s*hernia\b",
                r"\bherniorrhaphy\b",
                r"\bhernioplasty\b",
            ],
        ),
        "HYDROCELE": (
            "SURGICAL",
            0.95,
            [
                r"\bhydrocele\b",
                r"\bhydrocelectomy\b",
                r"\bvaricocele\b",
                r"\bvaricocelectomy\b",
            ],
        ),
        "FISTULA_PILES": (
            "SURGICAL",
            0.95,
            [
                r"\bpiles\b",
                r"\bhemorrhoids?\b",
                r"\bfistula\b",
                r"\bfistula[\s\-]*in[\s\-]*ano\b",
                r"\bfissure\b",
                r"\bhemorrhoidectomy\b",
                r"\bfistulectomy\b",
            ],
        ),
        "TONSILS_ADENOIDS": (
            "ENT",
            0.95,
            [
                r"\btonsil(?:s|litis)?\b",
                r"\btonsillectomy\b",
                r"\badenoids?\b",
                r"\badenoidectomy\b",
                r"\btonsillitis\b",
            ],
        ),
        "VARICOSE_VEINS": (
            "VASCULAR",
            0.95,
            [
                r"\bvaricose\s*veins?\b",
                r"\bendovenous\s*laser\b",
                r"\bvein\s*stripping\b",
                r"\bsclerotherapy\b",
            ],
        ),
        "COSMETIC_SURGERY": (
            "COSMETIC",
            0.98,
            [
                r"\brhinoplasty\b",
                r"\bcosmetic(?:\s*surgery)?\b",
                r"\baesthetic(?:\s*surgery)?\b",
                r"\bliposuction\b",
                r"\bbreast\s*augmentation\b",
                r"\bbreast\s*reduction\b",
                r"\bbotox\b",
                r"\bfacial\s*aesthetics\b",
            ],
        ),
        "OBESITY_TREATMENT": (
            "WEIGHT_LOSS",
            0.98,
            [
                r"\bobesity\b",
                r"\bbariatric\b",
                r"\bgastric\s*bypass\b",
                r"\bsleeve\s*gastrectomy\b",
                r"\bweight\s*loss\s*surgery\b",
            ],
        ),
        "ROBOTIC_SURGERY": (
            "MODERN_TREATMENT",
            0.95,
            [
                r"\brobotic(?:\s*surgery|\s*procedure|\s*assistance)?\b",
                r"\brobot[\s\-]*assisted\b",
                r"\bcyberknife\b",
                r"\bda\s*vinci\b",
                r"\bintra\s*operative\s*neuro\s*monitoring\b",
                r"\bstem\s*cell\b",
            ],
        ),
        "CHANGE_OF_GENDER": (
            "GENDER",
            0.98,
            [
                r"\bchange[\s\-]*of[\s\-]*gender\b",
                r"\bgender\s*reassignment\b",
                r"\bsex\s*reassignment\b",
                r"\bgender\s*affirmation\b",
            ],
        ),
        "DENTAL_TREATMENT": (
            "DENTAL",
            0.95,
            [
                r"\bdental\b",
                r"\btooth\s*extraction\b",
                r"\broot\s*canal\b",
                r"\borthodontic\b",
                r"\bgingivitis\b",
            ],
        ),
        "UNPROVEN_TREATMENT": (
            "EXPERIMENTAL",
            0.95,
            [
                r"\bunproven\b",
                r"\bexperimental\b",
                r"\binvestigational\b",
                r"\bunorthodox\b",
            ],
        ),
        "APPENDICITIS": (
            "SURGICAL",
            0.95,
            [
                r"\bappendicitis\b",
                r"\bappendectomy\b",
                r"\bappendix\b",
                r"\bacute\s*appendicitis\b",
            ],
        ),
    }

    # Ambiguous clinical presentations that should trigger MANUAL_REVIEW if no specific diagnosis is given
    AMBIGUOUS_PATTERNS = [
        r"\bbody\s*pain\b",
        r"\bfatigue\b",
        r"\bgeneral(?:ized)?\s*weakness\b",
        r"\bunspecified\s*malaise\b",
        r"\bfever\s*unknown\b",
        r"\bpyrexia\s*of\s*unknown\s*origin\b",
        r"\bpuo\b",
        r"\billness\s*under\s*investigation\b",
        r"\bobservation\b",
    ]

    @classmethod
    def normalize_text(cls, text: str, source: str = "diagnosis") -> NormalizedCondition:
        """
        Normalizes a single text snippet (diagnosis, procedure, or symptom)
        into a canonical condition record.
        """
        raw = (text or "").strip()
        if not raw:
            return NormalizedCondition(
                original_text="",
                normalized_value="UNSPECIFIED",
                confidence=0.0,
                source=source,
                category="UNKNOWN",
                matched_keywords=[],
            )

        lower = raw.lower()

        # 1. Match canonical conditions
        for code, (category, conf, patterns) in cls.CANONICAL_PATTERNS.items():
            matched_kws = []
            for pat in patterns:
                found = re.findall(pat, lower, re.IGNORECASE)
                if found:
                    matched_kws.extend(found)
            if matched_kws:
                return NormalizedCondition(
                    original_text=raw,
                    normalized_value=code,
                    confidence=conf,
                    source=source,
                    category=category,
                    matched_keywords=list(set(matched_kws)),
                )

        # 2. Check for ambiguous presentations
        for pat in cls.AMBIGUOUS_PATTERNS:
            if re.search(pat, lower, re.IGNORECASE):
                return NormalizedCondition(
                    original_text=raw,
                    normalized_value="AMBIGUOUS_SYMPTOM",
                    confidence=0.35,  # Low confidence indicates ambiguity
                    source=source,
                    category="AMBIGUOUS",
                    matched_keywords=[raw],
                )

        # 3. Fallback: Cleaned alphanumeric canonical representation
        cleaned = re.sub(r"[^A-Za-z0-9\s]", " ", raw).strip().upper()
        norm_val = "_".join(cleaned.split()[:4]) or "UNSPECIFIED"

        return NormalizedCondition(
            original_text=raw,
            normalized_value=norm_val,
            confidence=0.70,
            source=source,
            category="GENERAL",
            matched_keywords=[raw],
        )

    @classmethod
    def normalize_claim(cls, prescription_json: Dict[str, Any]) -> List[NormalizedCondition]:
        """
        Extracts and normalizes all medical entities across diagnosis, procedures,
        symptoms, and medicines from a prescription payload.
        """
        normalized_list: List[NormalizedCondition] = []
        seen_keys = set()

        # Diagnosis
        diag = prescription_json.get("diagnosis")
        if diag and isinstance(diag, str) and diag.strip():
            norm = cls.normalize_text(diag, source="diagnosis")
            normalized_list.append(norm)
            seen_keys.add(norm.normalized_value)

        # Procedures
        procs = prescription_json.get("procedures") or []
        for p in procs:
            if p and isinstance(p, str) and p.strip():
                norm = cls.normalize_text(p, source="procedure")
                if norm.normalized_value not in seen_keys or norm.confidence > 0.9:
                    normalized_list.append(norm)
                    seen_keys.add(norm.normalized_value)

        # Symptoms
        symptoms = prescription_json.get("symptoms") or []
        for s in symptoms:
            if s and isinstance(s, str) and s.strip():
                norm = cls.normalize_text(s, source="symptom")
                if norm.normalized_value not in seen_keys:
                    normalized_list.append(norm)
                    seen_keys.add(norm.normalized_value)

        # Manual text
        manual = prescription_json.get("manualText")
        if manual and isinstance(manual, str) and manual.strip():
            norm = cls.normalize_text(manual, source="manualText")
            if norm.normalized_value not in seen_keys:
                normalized_list.append(norm)
                seen_keys.add(norm.normalized_value)

        return normalized_list
