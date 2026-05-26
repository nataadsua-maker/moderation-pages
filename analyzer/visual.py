"""Per-frame visual analysis: OCR plашек + visual policy + 18+ detection."""
from __future__ import annotations
import json
from pathlib import Path

from nim import vision_describe_frame
from video import format_ts


VISION_PROMPT = """You analyze a single frame from an advertising video for RSOC policy compliance.

BE EXTREMELY CONSERVATIVE. When in doubt — DO NOT flag. You will be evaluated by precision, not recall.

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
  ✓ ONLY if the creative shows a FAKE button, FAKE search box, or FAKE search results
    designed to trick the viewer into thinking they are interacting with a real system.
    Example: a fake "Search Now" button overlaid on the video that doesn't exist on the actual page.
  ✗ A real screenshot or demo of an actual product/website (auction site, app interface,
    real search engine, store) shown to demonstrate the product — NOT a violation.
  ✗ Standard subtitles / story captions / informational text overlays — NOT a violation.

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

- "adult_18plus"
  ✓ Sexualized imagery, suggestive poses, lingerie/swimwear as main subject, ED context.
  ✗ Normal clothing, beach photos without sexual framing — NOT a violation.

If nothing genuinely violates: return empty visual_violations array.
ocr_text must be the actual text visible on screen, not your description of the image."""


def analyze_frame(frame_path: Path) -> dict:
    raw = vision_describe_frame(frame_path, VISION_PROMPT)
    # Extract JSON (model may add prose around it)
    try:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        return json.loads(raw[start:end])
    except Exception:
        return {"ocr_text": "", "visual_violations": [], "_raw": raw[:500]}


def analyze_video_frames(frames: list[dict]) -> list[dict]:
    """frames: [{path, ts_sec}, ...]; returns same items enriched with 'ocr_text' and 'visual_violations' and 'ts'."""
    out = []
    for fr in frames:
        analysis = analyze_frame(fr["path"])
        out.append({
            "ts_sec": fr["ts_sec"],
            "ts": format_ts(fr["ts_sec"]),
            "path": str(fr["path"]),
            "ocr_text": analysis.get("ocr_text", "") or "",
            "visual_violations": analysis.get("visual_violations") or [],
        })
    return out
