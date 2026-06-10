"""Per-frame visual analysis: OCR plашек + visual policy + 18+ detection."""
from __future__ import annotations
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from nim import vision_describe_frame
from video import format_ts


# Frames within a video are independent NIM calls — analyze them concurrently so a
# multi-video submission no longer runs ~2 min/video sequentially (the cause of the
# 8-min step timeouts → manual review). Bounded pool: too many in-flight requests
# risk NIM 429s. analyze_frame already retries with backoff and never raises, so a
# flaky frame degrades gracefully. Videos themselves stay sequential to keep total
# concurrency = VISION_CONCURRENCY, not VISION_CONCURRENCY × n_videos.
VISION_CONCURRENCY = int(os.environ.get("VISION_CONCURRENCY", "4"))


VISION_PROMPT = """You analyze a single frame from an advertising video for RSOC policy compliance.

TWO DIFFERENT STANDARDS APPLY depending on the violation type:

• For arrow_or_circle, fake_ui, before_after, fake_local, shock_content, weapons,
  gambling, politics, drugs — BE CONSERVATIVE. These are prone to false positives.
  When in doubt — DO NOT flag.

• For adult_18plus — BE SENSITIVE (HIGH RECALL). This is a high-priority, zero-tolerance
  category. When in doubt — DO flag. It is far better to send a borderline frame to a human
  than to let 18+ content slip through. Recall matters more than precision here.

Return STRICT JSON only:
{
  "ocr_text": "exact text overlay visible on this frame (captions/plашки), or empty string",
  "visual_violations": [
    // each item: {"type": "<code>", "detail": "<≤80 chars, what specifically is wrong>"}
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
ocr_text must be the actual text visible on screen, not your description of the image."""


def analyze_frame(frame_path: Path) -> dict:
    # A flaky vision call on one frame must NOT abort the whole submission —
    # skip the frame (no OCR / no visual violations) and let the verdict complete.
    try:
        raw = vision_describe_frame(frame_path, VISION_PROMPT)
    except Exception as e:
        print(f"  vision failed for {frame_path} (skipping frame): {e}")
        return {"ocr_text": "", "visual_violations": []}
    # Extract JSON (model may add prose around it)
    try:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        return json.loads(raw[start:end])
    except Exception:
        return {"ocr_text": "", "visual_violations": [], "_raw": raw[:500]}


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
