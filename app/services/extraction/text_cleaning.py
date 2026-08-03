"""
Text Cleaning Service — migrated from TextCleaningService.js.
Cleans and normalizes extracted raw text (Stage 3).
"""

import re


def clean_text(raw_text: str | None) -> str:
    """
    Cleans and normalizes extracted raw text.
    Removes invisible chars, repeated headers/footers, and page numbers.
    """
    if not isinstance(raw_text, str):
        return ""

    text = raw_text

    # 1. Remove invisible/control characters except standard whitespace
    # Python equivalent for \x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", "", text)

    lines = text.split("\n")
    line_count_map = {}

    for line in lines:
        trimmed = line.strip()
        if trimmed:
            line_count_map[trimmed] = line_count_map.get(trimmed, 0) + 1

    # Patterns for page numbers
    page_num_regex = re.compile(
        r"^(page\s+\d+(\s+of\s+\d+)?|-\s*\d+\s*-|\d+\s*/\s*\d+)$", re.IGNORECASE
    )

    cleaned_lines = []

    for line in lines:
        if not line:
            cleaned_lines.append("")
            continue

        trimmed = line.strip()

        # Remove repeated headers/footers (lines repeating 3 or more times)
        if line_count_map.get(trimmed, 0) >= 3 and len(trimmed) < 100:
            continue

        # Remove page numbers
        if page_num_regex.match(trimmed):
            continue

        # Remove duplicate spaces within the line
        cleaned_line = re.sub(r"[ \t]{2,}", " ", line)
        cleaned_lines.append(cleaned_line)

    cleaned_text = "\n".join(cleaned_lines)

    # Normalize line breaks: collapse 3+ newlines to exactly 2
    cleaned_text = re.sub(r"\n{3,}", "\n\n", cleaned_text)

    return cleaned_text.strip()
