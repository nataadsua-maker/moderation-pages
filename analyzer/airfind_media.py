"""Разбор видео-креатива для связки Airfind (вкладка «🔎 Airfind» сервиса).

Airfind требует в adCopyTitle «any on-screen or spoken words», поэтому из ролика
нужны две вещи: текст плашек и озвучка. Это та же техника, что у модерации
(moderate.py), но без проверки полиси и без вердикта:
  - озвучка — faster-whisper (transcribe.py);
  - плашки — MiMo по видео целиком (mimo_video.py), фолбэк — по-кадровый NIM OCR;
  - вшитые субтитры (дубль озвучки) выкидываем, чтобы текст не задвоился.
Результат уходит в воркер: POST /api/internal/airfind-media.
"""
from __future__ import annotations
import argparse
import os
import re
import sys
import tempfile
import traceback
from pathlib import Path

import requests

import mimo_video
import r2_client
import subtitle_filter
import transcribe
import video as video_mod
import visual


def _post(payload: dict) -> None:
    r = requests.post(
        f"{os.environ['WORKER_URL']}/api/internal/airfind-media",
        headers={"Authorization": f"Bearer {os.environ['VERDICT_SHARED_SECRET']}"},
        json=payload,
        timeout=30,
    )
    r.raise_for_status()


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def run(job_id: str, key: str) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        print(f"[1/3] download {key}")
        local = r2_client.download(key, tmp / Path(key).name)

        print("[2/3] transcribe")
        try:
            tr = transcribe.transcribe(local)
        except Exception as e:  # озвучки может не быть — это не повод падать
            print(f"  transcribe failed: {e}")
            tr = {"language": "", "segments": [], "full_text": ""}

        print("[3/3] overlays")
        overlays = mimo_video.detect_overlays(local)
        if overlays is None:
            print("  MiMo недоступен, читаю кадры через NIM")
            frames = video_mod.extract_frames(local, tmp / "frames", n=8)
            fa = visual.analyze_video_frames(frames)
            overlays = [{"ts_sec": f.get("ts_sec", 0), "text": f.get("ocr_text", "")} for f in fa]

        frames_analysis = [{"ocr_text": o.get("text", ""), "ts_sec": o.get("ts_sec", 0)} for o in overlays]
        v = {"transcript": tr, "frames_analysis": frames_analysis}
        subtitle_filter.annotate_frames([v])

        texts: list[str] = []
        seen: set[str] = set()
        for fr in sorted(v["frames_analysis"], key=lambda f: f.get("ts_sec", 0)):
            t = re.sub(r"\s+", " ", fr.get("ocr_text", "")).strip()
            n = _norm(t)
            if not t or fr.get("is_subtitle") or n in seen:
                continue
            seen.add(n)
            texts.append(t)

        _post({
            "job_id": job_id,
            "ok": True,
            "overlays": texts,
            "transcript": tr.get("full_text", ""),
        })
        print(f"  done: {len(texts)} overlays, transcript {len(tr.get('full_text', ''))} chars")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job-id", required=True)
    ap.add_argument("--key", required=True)
    a = ap.parse_args()
    try:
        run(a.job_id, a.key)
    except Exception as e:
        traceback.print_exc()
        try:
            _post({"job_id": a.job_id, "ok": False, "error": str(e)[:300]})
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
