"""Batch translate short EN strings to RU via NIM Llama 70B."""
from __future__ import annotations
import json

from nim import text_check

SYSTEM_PROMPT = """You are a translator. Translate the given list of English strings into Russian.

Rules:
- Output a JSON object: {"translations": ["ru1", "ru2", ...]}
- Same length and order as input
- Natural Russian — not literal word-by-word
- Preserve numbers, currency symbols, brand names as-is
- If a string is already in Russian or empty, return it unchanged
- Concise, advertising tone where applicable
"""


def batch_translate(texts: list[str]) -> list[str]:
    """Translate a list of strings; returns RU list of the same length."""
    if not texts:
        return []
    # Filter out empty / very short non-alphabetic strings
    payload = {"strings": texts}
    try:
        resp = text_check(SYSTEM_PROMPT, json.dumps(payload, ensure_ascii=False))
        out = resp.get("translations") if isinstance(resp, dict) else None
        if isinstance(out, list) and len(out) == len(texts):
            return [str(t) for t in out]
    except Exception as e:
        print(f"[translate] batch failed: {e}")
    # Fallback: return originals (so the page still renders)
    return list(texts)
