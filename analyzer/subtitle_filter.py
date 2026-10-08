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


# Японский/китайский/корейский: слов через пробел нет, а _normalize выше
# выкидывает всё, кроме латиницы — такой текст превращался в пустоту и никогда
# не считался субтитром. Обрывок вшитого субтитра («キッチンは毎») уезжал в
# headline как плашка (REQ-260929-036, нашла Nataliia 08.10.2026). Для них
# сравниваем по символам (запас на ошибки распознавания вроде «での» вместо «で その»).
_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")


def _chars(s: str) -> str:
    return re.sub(r"[\W_]+", "", s or "").lower()


def _is_cjk_subtitle(ocr_text: str, transcript_full: str) -> bool:
    """Ищем в озвучке кусок примерно той же длины, где совпадает ≥75% символов
    текста на кадре (по порядку). Окно, а не вся озвучка: иначе короткий текст
    «совпал» бы с разбросанными по всей озвучке символами."""
    from difflib import SequenceMatcher
    a, b = _chars(ocr_text), _chars(transcript_full)
    # 1-2 иероглифа («応急», «ン性») — осколок пословных субтитров, плашкой не бывает.
    if len(a) < 3:
        return True
    if not b:
        return False
    w = len(a) + 3
    best = 0
    for start in range(0, max(1, len(b) - len(a) + 1)):
        win = b[start:start + w]
        matched = sum(blk.size for blk in SequenceMatcher(None, a, win, autojunk=False).get_matching_blocks())
        best = max(best, matched)
        if best == len(a):
            break
    return best / len(a) >= 0.75


def is_subtitle(ocr_text: str, transcript_full: str) -> bool:
    """Returns True if ocr_text duplicates the spoken track (i.e. burned-in subtitle)."""
    if _CJK_RE.search(ocr_text or ""):
        return _is_cjk_subtitle(ocr_text, transcript_full)
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
