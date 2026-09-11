"""
Semantic Clause Chunker for Insurance Policy Documents.
Splits insurance policy texts at logical clause boundaries (sections, numbered clauses,
tables, exclusion/waiting period lists) while preserving page numbers and section hierarchy.
"""

import re
from typing import List, Dict, Any, Optional


class ClauseChunker:
    """Intelligently chunks insurance policy documents preserving clause semantics."""

    # Heading patterns typical of insurance contracts and leaflets
    CLAUSE_START_REGEX = re.compile(
        r"^(?:"
        r"(?:Section|Clause|Article|Part)\s+[A-Z0-9.\-]+|"
        r"\d{1,2}\.\s+[A-Z]|"
        r"[A-Z][\w\s]{3,40}:|"
        r"2\s*YEAR(?:'S|\s)?\s*SPECIFIC\s*WAITING\s*PERIODS|"
        r"PERMANENT\s*EXCLUSION(?:S)?(?:\s*-\s*NOT\s*COVERED)?|"
        r"VARIANT\s*COMPARISON|"
        r"INDUSTRY\s*COMPARISON|"
        r"BENEFITS\s*(?:AND\s*COVERAGE)?|"
        r"OPTIONAL\s*BENEFITS|"
        r"GENERAL\s*CONDITIONS|"
        r"TERMS\s*AND\s*CONDITIONS|"
        r"DEFINITIONS|"
        r"CLAIM\s*PROCEDURE"
        r")",
        re.IGNORECASE | re.MULTILINE
    )

    PAGE_MARKER_REGEX = re.compile(r"---\s*PAGE\s*(\d+)\s*---", re.IGNORECASE)

    @classmethod
    def chunk_policy_text(
        cls,
        text: str,
        policy_meta: Dict[str, Any],
        document_type: str = "policy wording",
        source_name: str = "policy_document.pdf",
        max_chunk_chars: int = 1800,
        min_chunk_chars: int = 60,
    ) -> List[Dict[str, Any]]:
        """
        Chunks text into semantic policy clauses with enriched metadata.
        """
        if not text or not text.strip():
            return []

        # 1. Parse pages if page markers exist
        page_splits = cls._split_by_page_markers(text)

        all_chunks: List[Dict[str, Any]] = []
        chunk_counter = 1

        for page_num, page_content in page_splits:
            # 2. Split page content into semantic clauses
            raw_clauses = cls._split_into_clauses(page_content, max_chunk_chars, min_chunk_chars)

            for item in raw_clauses:
                clause_text = item["text"].strip()
                if len(clause_text) < min_chunk_chars:
                    continue

                section_title = item.get("section") or cls._extract_section_title(clause_text)
                rule_type = cls._infer_rule_type(clause_text, section_title)
                keywords = cls._extract_keywords(clause_text)

                p_id = str(policy_meta.get("policy_id") or policy_meta.get("policyId") or "GLOBAL_REFERENCE")
                prefix = policy_meta.get("insurer", "POL")[:4].upper().replace(" ", "")
                clean_pid = re.sub(r"[^A-Za-z0-9_\-]", "", p_id)
                chunk_id = f"CHK-{prefix}-{clean_pid}-{chunk_counter:04d}"

                chunk = {
                    "chunkId": chunk_id,
                    "policyId": p_id,
                    "insurer": policy_meta.get("insurer") or policy_meta.get("insuranceCompany") or "All",
                    "product": policy_meta.get("product") or policy_meta.get("policyName") or "Standard Benchmark",
                    "variant": policy_meta.get("variant") or policy_meta.get("policyType") or "All",
                    "policyVersion": policy_meta.get("policyVersion") or policy_meta.get("version") or "1.0",
                    "documentId": str(policy_meta.get("documentId") or p_id),
                    "documentType": document_type,
                    "pageNumber": page_num,
                    "section": section_title,
                    "ruleType": rule_type,
                    "effectiveFrom": policy_meta.get("effectiveFrom") or policy_meta.get("policyStartDate"),
                    "effectiveTo": policy_meta.get("effectiveTo") or policy_meta.get("policyEndDate"),
                    "source": source_name,
                    "language": policy_meta.get("language", "en"),
                    "text": clause_text,
                    "authorityLevel": policy_meta.get("authorityLevel", "CONTRACTUAL_POLICY_WORDING"),
                    "keywords": keywords,
                    "metadata": {
                        "length": len(clause_text),
                        "hasConditions": bool(keywords),
                    }
                }
                all_chunks.append(chunk)
                chunk_counter += 1

        return all_chunks

    @classmethod
    def _split_by_page_markers(cls, text: str) -> List[tuple[int, str]]:
        """Extracts (page_number, text) tuples by scanning for page delimiters."""
        matches = list(cls.PAGE_MARKER_REGEX.finditer(text))
        if not matches:
            return [(1, text)]

        pages: List[tuple[int, str]] = []
        last_pos = 0
        current_page = 1

        for match in matches:
            start, end = match.span()
            if start > last_pos:
                content = text[last_pos:start].strip()
                if content:
                    pages.append((current_page, content))
            try:
                current_page = int(match.group(1))
            except Exception:
                current_page += 1
            last_pos = end

        if last_pos < len(text):
            content = text[last_pos:].strip()
            if content:
                pages.append((current_page, content))

        return pages if pages else [(1, text)]

    @classmethod
    def _split_into_clauses(cls, page_text: str, max_chunk_chars: int, min_chunk_chars: int) -> List[Dict[str, str]]:
        """Splits page text at clause boundaries and numbered lists."""
        paragraphs = page_text.split("\n\n")
        clauses: List[Dict[str, str]] = []
        current_section = "General Provisions"
        buffer_text = ""

        for para in paragraphs:
            para_clean = para.strip()
            if not para_clean:
                continue

            # Check if this paragraph starts a new titled section
            header_match = cls.CLAUSE_START_REGEX.search(para_clean)
            is_new_section = header_match and header_match.start() < 10

            if is_new_section:
                if buffer_text:
                    clauses.append({"text": buffer_text.strip(), "section": current_section})
                    buffer_text = ""
                # Update current section name
                first_line = para_clean.split("\n")[0][:60].strip()
                current_section = first_line

            # If paragraph itself is too large, split by bullet points or sentences
            if len(para_clean) > max_chunk_chars:
                if buffer_text:
                    clauses.append({"text": buffer_text.strip(), "section": current_section})
                    buffer_text = ""

                sub_chunks = cls._split_large_paragraph(para_clean, max_chunk_chars)
                for sc in sub_chunks:
                    clauses.append({"text": sc, "section": current_section})
            else:
                if len(buffer_text) + len(para_clean) + 2 > max_chunk_chars:
                    clauses.append({"text": buffer_text.strip(), "section": current_section})
                    buffer_text = para_clean
                else:
                    buffer_text += ("\n\n" if buffer_text else "") + para_clean

        if buffer_text:
            clauses.append({"text": buffer_text.strip(), "section": current_section})

        return clauses

    @classmethod
    def _split_large_paragraph(cls, paragraph: str, max_chars: int) -> List[str]:
        """Subdivides an oversized paragraph by bullet points or sentence boundaries."""
        # Check bullet points or list items
        bullets = re.split(r"\n(?=[>•\-\*]|\d+\.|\([a-z0-9]\))", paragraph)
        if len(bullets) > 1:
            chunks = []
            curr = ""
            for b in bullets:
                if len(curr) + len(b) + 1 > max_chars and curr:
                    chunks.append(curr.strip())
                    curr = b
                else:
                    curr += ("\n" if curr else "") + b
            if curr:
                chunks.append(curr.strip())
            return chunks

        # Fallback to sentence splitting
        sentences = re.split(r"(?<=[.!?])\s+", paragraph)
        chunks = []
        curr = ""
        for s in sentences:
            if len(curr) + len(s) + 1 > max_chars and curr:
                chunks.append(curr.strip())
                curr = s
            else:
                curr += (" " if curr else "") + s
        if curr:
            chunks.append(curr.strip())
        return chunks

    @classmethod
    def _extract_section_title(cls, text: str) -> str:
        """Extracts the first heading or line as the section title."""
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        if not lines:
            return "General Policy Clause"
        first_line = lines[0]
        if len(first_line) <= 60 and not first_line.endswith("."):
            return first_line
        match = cls.CLAUSE_START_REGEX.search(first_line)
        if match:
            return first_line[:match.end()].strip(" :.-")
        return first_line[:50]

    @classmethod
    def _infer_rule_type(cls, text: str, section: str) -> str:
        """Infers the rule classification from text semantics."""
        corpus = f"{section} {text}".lower()

        if any(kw in corpus for kw in ["waiting period", "wait period", "specific waiting", "24 months", "ped waiting"]):
            return "WAITING_PERIOD"
        if any(kw in corpus for kw in ["permanent exclusion", "not covered", "shall not be liable", "permanently excluded", "exclusion"]):
            return "PERMANENT_EXCLUSION"
        if any(kw in corpus for kw in ["room rent", "room category", "sub-limit", "capped at", "limit of", "air ambulance"]):
            return "LIMIT"
        if any(kw in corpus for kw in ["co-pay", "copayment", "deductible"]):
            return "CO_PAY"
        if any(kw in corpus for kw in ["hospital daily cash", "personal accident", "cash bag", "wellness", "safeguard", "reassure"]):
            return "BENEFIT"
        if any(kw in corpus for kw in ["network hospital", "cashless", "blacklisted", "excluded provider"]):
            return "NETWORK_REQUIREMENT"
        if any(kw in corpus for kw in ["outside india", "borderless", "geographical", "abroad", "foreign"]):
            return "GEOGRAPHICAL_RESTRICTION"
        if any(kw in corpus for kw in ["hours hospitalization", "day care", "ayush 24"]):
            return "HOSPITALIZATION_REQUIREMENT"

        return "GENERAL_CONDITIONS"

    @classmethod
    def _extract_keywords(cls, text: str) -> List[str]:
        """Extracts clinically and contractually relevant keywords from clause text."""
        words = re.findall(r"[A-Za-z]{4,}", text.lower())
        stopwords = {
            "this", "that", "with", "from", "shall", "under", "policy", "which", "their",
            "there", "where", "other", "after", "before", "during", "insured", "company",
            "hospital", "treatment", "expenses", "period", "covered", "claim"
        }
        unique = []
        for w in words:
            if w not in stopwords and w not in unique:
                unique.append(w)
            if len(unique) >= 15:
                break
        return unique
