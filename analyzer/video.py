"""Extract evenly-spaced frames from a video via ffmpeg."""
from __future__ import annotations
import json
import subprocess
from pathlib import Path


def probe_duration_sec(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(json.loads(r.stdout)["format"]["duration"])


def extract_frames(video_path: Path, out_dir: Path, n: int = 8) -> list[dict]:
    """Returns list of {path, ts_sec}."""
    out_dir.mkdir(parents=True, exist_ok=True)
    dur = probe_duration_sec(video_path)
    if dur <= 0:
        return []
    # Pick n equally spaced timestamps (0.05*dur .. 0.95*dur)
    if n == 1:
        timestamps = [dur / 2]
    else:
        timestamps = [dur * (0.05 + 0.9 * i / (n - 1)) for i in range(n)]

    frames = []
    for i, ts in enumerate(timestamps):
        out = out_dir / f"frame_{i:02d}.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{ts:.2f}",
             "-i", str(video_path), "-frames:v", "1", "-q:v", "3", str(out)],
            check=True,
        )
        frames.append({"path": out, "ts_sec": ts})
    return frames


def format_ts(ts_sec: float) -> str:
    m = int(ts_sec // 60)
    s = int(ts_sec % 60)
    return f"{m:02d}:{s:02d}"
