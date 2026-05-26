"""Per-frame visual analysis: OCR plашек + visual policy + 18+ detection."""
from __future__ import annotations
import json
from pathlib import Path

from nim import vision_describe_frame
from video import format_ts


VISION_PROMPT = """You analyze a single frame from an advertising video for RSOC policy compliance.

Return a strict JSON object with these fields:
{
  "ocr_text": "exact text overlay visible on this frame (плашки/captions), or empty string",
  "visual_violations": [
    // each item: {"type": "...", "detail": "..."}
    // possible types:
    //   "arrow_or_circle"     — стрелка/кружок указывает куда кликнуть
    //   "fake_ui"             — имитация UI кнопок / поиска / выдачи
    //   "before_after"        — split-screen «до|после» на одном кадре или экстремальный шок-контраст
    //   "shock_content"       — травмы/кровь/экстремальные эмоции
    //   "fake_local"          — локализованный контент ({city}/near you/локальные логотипы) без оснований
    //   "weapons" | "gambling" | "politics" | "drugs"
    //   "adult_18plus"        — сексуализированный контент, suggestive позы, бельё/купальники, ED
  ]
}

Only flag what you actually see. Do NOT hallucinate. Be especially careful with adult_18plus — flag only obvious sexualized content."""


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
