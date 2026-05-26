"""faster-whisper transcription, returns segments with timestamps."""
from __future__ import annotations
from pathlib import Path
from functools import lru_cache

from faster_whisper import WhisperModel


@lru_cache(maxsize=1)
def _model() -> WhisperModel:
    # small model: tradeoff size vs accuracy; CPU-only in GH Actions
    return WhisperModel("small", device="cpu", compute_type="int8")


def transcribe(video_path: Path) -> dict:
    """Returns {language, segments: [{start, end, text}], full_text}."""
    segments, info = _model().transcribe(str(video_path), beam_size=1, vad_filter=True)
    out = []
    full = []
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        out.append({"start": seg.start, "end": seg.end, "text": text})
        full.append(text)
    return {"language": info.language, "segments": out, "full_text": " ".join(full)}
