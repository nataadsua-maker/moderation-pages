"""Mark frame OCR text as subtitle if it duplicates the voiceover transcript.

We only want to show genuinely static plашки (text overlays carrying meaning
not present in the spoken track). Burned-in subtitles add noise.

Algorithm: normalize both texts, then check if the OCR words form a contiguous
or near-contiguous run within the transcript words.
"""
from __future__ import annotations
import re
import unicodedata


def _normalize(s: str) -> list[str]:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return s.split()


def _is_substring_match(needle_tokens: list[str], haystack_tokens: list[str]) -> bool:
    """True if all needle tokens appear in haystack in order (with up to 1 gap)."""
    if not needle_tokens:
        return True
    if len(needle_tokens) > len(haystack_tokens):
        return False
    n = len(needle_tokens)
    h = len(haystack_tokens)
    for start in range(h - n + 1):
        window = haystack_tokens[start:start + n + 2]  # allow 1-2 extra words
        matched = 0
        idx = 0
        for w in window:
            if idx < n and w == needle_tokens[idx]:
                matched += 1
                idx += 1
        if matched == n:
            return True
    return False


def is_subtitle(ocr_text: str, transcript_full: str) -> bool:
    """Returns True if ocr_text duplicates the spoken track (i.e. burned-in subtitle)."""
    ocr_tokens = _normalize(ocr_text)
    trans_tokens = _normalize(transcript_full)
    if len(ocr_tokens) < 2:
        # Single-word overlays — not informative enough to call subtitle,
        # but also harmless to keep. Default to NOT subtitle (show it).
        return False
    return _is_substring_match(ocr_tokens, trans_tokens)


def annotate_frames(videos: list[dict]) -> None:
    """Mutates videos in place: adds frame['is_subtitle']: bool."""
    for v in videos:
        transcript_full = v.get("transcript", {}).get("full_text", "")
        for fr in v.get("frames_analysis", []):
            fr["is_subtitle"] = bool(fr.get("ocr_text")) and is_subtitle(
                fr["ocr_text"], transcript_full
            )
