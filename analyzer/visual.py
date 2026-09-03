"""Per-frame visual analysis: OCR plашек + visual policy + 18+ detection."""
from __future__ import annotations
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

from nim import text_check, vision_describe_frame
from video import format_ts


# Frames within a video are independent NIM calls — analyze them concurrently so a
# multi-video submission no longer runs ~2 min/video sequentially (the cause of the
# 8-min step timeouts → manual review). Bounded pool: too many in-flight requests
# risk NIM 429s. analyze_frame already retries with backoff and never raises, so a
# flaky frame degrades gracefully. Videos themselves stay sequential to keep total
# concurrency = VISION_CONCURRENCY, not VISION_CONCURRENCY × n_videos.
VISION_CONCURRENCY = int(os.environ.get("VISION_CONCURRENCY", "4"))


# Пропущенный кадр = кадр, который никто не посмотрел. Пустой OCR при этом
# неотличим от «на кадре ничего нет», поэтому при массовом отказе vision вердикт
# «нарушений не видно» ничего не значит (так 10-19.08 заявки одобрялись вслепую).
# Считаем долю непрочитанных кадров, решение принимает moderate.py.
_STATS = {"attempted": 0, "failed": 0}
_STATS_LOCK = Lock()


def _count(failed: bool) -> None:
    with _STATS_LOCK:
        _STATS["attempted"] += 1
        if failed:
            _STATS["failed"] += 1


def vision_stats() -> dict:
    """{'attempted': N, 'failed': M} за весь прогон."""
    with _STATS_LOCK:
        return dict(_STATS)


# Кадр разбирается в два шага. Рабочая на NIM vision-модель (llama-3.2-11b) описывает
# картинку прилично, но строгий JSON не отдаёт ни при каком промпте и даже в
# response_format=json_object — отвечает прозой. Поэтому: vision описывает кадр
# словами (DESCRIBE_PROMPT), а полиси применяет текстовая модель (CLASSIFY_PROMPT),
# у которой JSON-режим работает. Описание просим сразу под нужные признаки, иначе
# классификатору будет не за что зацепиться.
DESCRIBE_PROMPT = """Describe this frame from an advertising video for a compliance reviewer.

1. Transcribe ALL on-screen text verbatim, exactly as written, in a section "ON-SCREEN TEXT:".
   If there is no text, write "ON-SCREEN TEXT: none".
2. In a section "SCENE:", describe what is shown: people (clothing, pose, how much skin is
   visible, whether the framing is sexualized), objects, setting, product.
3. In a section "OVERLAYS:", list any graphics drawn ON TOP of the footage: arrows, circles or
   highlights pointing at something; buttons; search fields; lists of search results; text
   banners. For each say whether it looks like a real part of a shown app/website or an
   overlay added by the advertiser.
4. In a section "NOTABLE:", mention if you see any of: side-by-side before/after comparison,
   nudity or sexual content, injuries/blood/shocking imagery, weapons, gambling (casino, slots,
   betting), politicians or political symbols, drugs, alcohol.

Be literal and factual. Describe only what is actually visible, do not guess intent."""


CLASSIFY_PROMPT = """You judge a DESCRIPTION of a single frame from an advertising video for RSOC policy compliance.
You cannot see the image — rely ONLY on what the description states, never invent details.

TWO DIFFERENT STANDARDS APPLY depending on the violation type:

• For arrow_or_circle, fake_ui, before_after, fake_local, shock_content, weapons,
  gambling, politics, drugs — BE CONSERVATIVE. These are prone to false positives.
  When in doubt — DO NOT flag.

• For adult_18plus — BE SENSITIVE (HIGH RECALL). This is a high-priority, zero-tolerance
  category. When in doubt — DO flag. It is far better to send a borderline frame to a human
  than to let 18+ content slip through. Recall matters more than precision here.

Поле detail пишем ПО-РУССКИ всегда, каким бы ни был язык кадра и крео: его читает
русскоязычный баер. ocr_text, наоборот, цитируем дословно в языке оригинала.

Return STRICT JSON only:
{
  "ocr_text": "exact text overlay visible on this frame (captions/plашки), or empty string",
  "visual_violations": [
    // each item: {"type": "<code>", "detail": "<до 80 символов, ПО-РУССКИ, что именно не так>"}
  ]
}

Violation types and STRICT criteria:

- "arrow_or_circle"
  ✓ ONLY if there is a graphical arrow or circle OVERLAY (drawn on top of content)
    that points at a button/place where the viewer should click.
  ✗ Arrows that are part of the real product UI shown in the video — NOT a violation.
  ✗ Arrows in logos, illustrations, infographics — NOT a violation.

- "fake_ui"
  Flag ONLY a FAKE INTERACTIVE control overlaid to trick the viewer into thinking they can
  CLICK/INTERACT with it: a fake clickable BUTTON whose label is an ACTION (e.g. "Search Now",
  "Click Here", "Tap to Search", "Apply"), a fake SEARCH INPUT field, or a fake list of SEARCH
  RESULTS. The defining trait: it imitates a clickable UI element / search engine widget.
  ✗ A плашка / caption / text banner (product name, benefit, headline, descriptive phrase) is
    NOT fake_ui — EVEN IF it has a colored background, rounded corners, border, shadow or any
    button-like styling. A box with informative WORDS is a caption, not a button. Do NOT call it
    "имитация кнопки".
  ✗ A real screenshot or demo of an actual product/website (auction site, app interface,
    real search engine, store) shown to demonstrate the product — NOT a violation.
  ✗ Standard subtitles / story captions / informational text overlays — NOT a violation.
  Rule of thumb: if the overlay just conveys information (words), it is NOT fake_ui. Flag only
  when it imitates an actionable click target. When unsure — DO NOT flag.

- "before_after"
  ✓ ONLY if a SINGLE FRAME shows a split-screen "before | after" comparison
    OR an extremely shocking dramatic before/after contrast.
  ✗ Showing a product evolution across different frames over time — NOT a violation.
  ✗ Aesthetic comparison without shock — NOT a violation.

- "shock_content"
  ✓ Bloody injuries, extreme emotional distress, gore.
  ✗ Mild surprise, normal facial expressions — NOT a violation.

- "fake_local"
  ✓ Creative explicitly claims local relevance ({city}/"near you" text, fake local
    branding) without the lander actually being local.
  ✗ Showing a real location/city as part of product context — NOT a violation.

- "weapons" | "gambling" | "politics" | "drugs"
  ✓ These items shown as the MAIN visual subject (slot machine, gun, political figure, drugs).
  ✗ Background incidental presence — NOT a violation.

- "adult_18plus"   [HIGH PRIORITY — err toward flagging, see standards note above]
  ✓ Sexualized or suggestive imagery of people.
  ✓ Deep / plunging cleavage, exposed or visually emphasized chest, a crop or camera focus
    on breasts, buttocks or crotch.
  ✓ Suggestive / provocative poses, "bedroom" framing, undressing, bedsheets-and-skin shots.
  ✓ Lingerie, underwear, or swimwear as the main subject of the frame.
  ✓ Bare skin presented in a flirtatious / seductive context.
  ✓ ED / sexual-health context.
  ✓ AMBIGUOUS frames a reasonable viewer could read as sexual — flag them anyway
    (set detail to "двусмысленный кадр, возможно 18+").
  ✗ Ordinary clothing, and ordinary beach/pool photos with NO sexual framing and NO
    cleavage/skin emphasis — NOT a violation.
  When unsure whether a frame crosses the line — FLAG IT.

If nothing genuinely violates: return empty visual_violations array.
ocr_text must be the on-screen text quoted in the description (the "ON-SCREEN TEXT" section),
verbatim and nothing else — not the description itself. If it says there is no text, return ""."""


def analyze_frame(frame_path: Path) -> dict:
    # A flaky vision call on one frame must NOT abort the whole submission —
    # skip the frame (no OCR / no visual violations) and let the verdict complete.
    # Кадр без разбора считается непрочитанным (_count) — см. vision_stats().
    try:
        description = vision_describe_frame(frame_path, DESCRIBE_PROMPT)
    except Exception as e:
        print(f"  vision failed for {frame_path} (skipping frame): {e}")
        _count(failed=True)
        return {"ocr_text": "", "visual_violations": []}
    try:
        out = text_check(CLASSIFY_PROMPT, description)
    except Exception as e:
        print(f"  policy classify failed for {frame_path} (skipping frame): {e}")
        _count(failed=True)
        return {"ocr_text": "", "visual_violations": [], "_raw": description[:500]}
    _count(failed=False)
    return out


def analyze_video_frames(frames: list[dict]) -> list[dict]:
    """frames: [{path, ts_sec}, ...]; returns same items enriched with 'ocr_text' and 'visual_violations' and 'ts'.

    Frames are analyzed concurrently (bounded by VISION_CONCURRENCY); output order
    matches input order so timecodes/overlays stay aligned."""
    if not frames:
        return []
    workers = max(1, min(VISION_CONCURRENCY, len(frames)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        analyses = list(pool.map(lambda fr: analyze_frame(fr["path"]), frames))
    out = []
    for fr, analysis in zip(frames, analyses):
        out.append({
            "ts_sec": fr["ts_sec"],
            "ts": format_ts(fr["ts_sec"]),
            "path": str(fr["path"]),
            "ocr_text": analysis.get("ocr_text", "") or "",
            "visual_violations": analysis.get("visual_violations") or [],
        })
    return out
