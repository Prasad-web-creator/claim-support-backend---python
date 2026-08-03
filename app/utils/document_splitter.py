"""
Document Splitter — migrated from documentSplitter.js.
Intelligently chunks large text for AI context windows and merges results.
"""

import json
from typing import Any


def split_text_intelligently(text: str, max_chars: int = 24000) -> list[str]:
    """Split text into chunks at paragraph boundaries to stay within AI context limits."""
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    current_chunk = ""

    # Split by paragraphs to avoid cutting mid-sentence
    paragraphs = text.split("\n\n")

    for para in paragraphs:
        if (len(current_chunk) + len(para) + 2) > max_chars:
            if len(current_chunk) > 0:
                chunks.append(current_chunk)
                current_chunk = ""
            # If a single paragraph is larger than max_chars, slice it
            if len(para) > max_chars:
                start = 0
                while start < len(para):
                    chunks.append(para[start : start + max_chars])
                    start += max_chars
            else:
                current_chunk = para
        else:
            current_chunk += ("\n\n" if current_chunk else "") + para

    if len(current_chunk) > 0:
        chunks.append(current_chunk)

    return chunks


def merge_extracted_json(jsons: list[dict]) -> dict:
    """
    Deep merge multiple extracted JSON objects.
    Prioritizes non-null values. Merges arrays by concatenating and deduping.
    """
    if not jsons:
        return {}
    if len(jsons) == 1:
        return jsons[0]

    merged: dict[str, Any] = {}
    keys: set[str] = set()

    # Collect all unique keys
    for j in jsons:
        if j:
            keys.update(j.keys())

    for key in keys:
        is_array = False
        array_values: list = []
        final_primitive = None

        for j in jsons:
            if not j:
                continue
            val = j.get(key)

            if val is not None and val != "":
                if isinstance(val, list):
                    is_array = True
                    array_values.extend(val)
                elif final_primitive is None:
                    final_primitive = val

        if is_array:
            # Dedupe simple arrays (strings, numbers)
            if array_values and not isinstance(array_values[0], dict):
                merged[key] = list(set(array_values))
            else:
                # For arrays of objects, deduplicate by JSON stringification
                unique_strs = set(json.dumps(v, sort_keys=True) for v in array_values)
                merged[key] = [json.loads(s) for s in unique_strs]
        else:
            merged[key] = final_primitive

    return merged
