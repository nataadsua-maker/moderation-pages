#!/usr/bin/env python3
"""Entry point for the moderation analyzer (run from GH Actions).

Pipeline:
1. Fetch submission from Worker
2. Download videos from R2
3. For each video: extract frames → transcribe → vision OCR + visual policy
4. Fetch lander
5. Layer 1 regex scan (all text sources)
6. Layer 2 LLM checks
7. Assemble verdict
8. Render HTML page → write to docs/sub/<id>/index.html
9. POST verdict to Worker (which will notify the buyer via bot)
"""
from __future__ import annotations
import argparse
import os
import sys
import tempfile
import traceback
from pathlib import Path

import api_client
import lander as lander_mod
import llm_checks
import r2_client
import render
import subtitle_filter
import text_policy
import transcribe
import verdict as verdict_mod
import video as video_mod
import visual

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"
DOCS = ROOT / "docs"
PAGES_BASE = "https://nataadsua-maker.github.io/moderation-pages"


def run(submission_id: str) -> None:
    print(f"[1/9] Fetching submission {submission_id}")
    sub = api_client.fetch_submission(submission_id)
    print(f"  offer={sub.get('offer')!r} videos={len(sub.get('video_keys', []))}")

    print("[2/9] Fetching lander")
    lander = lander_mod.fetch(sub["lander_url"])
    print(f"  ok={lander['ok']} title={lander.get('title', '')[:80]!r}")

    print(f"[3-4/9] Processing {len(sub['video_keys'])} assets")
    IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        videos: list[dict] = []
        for i, key in enumerate(sub["video_keys"]):
            ext = Path(key).suffix.lower()
            kind = "image" if ext in IMAGE_EXTS else "video"
            print(f"  asset {i+1} ({kind}): download {key}")
            local = r2_client.download(key, tmp / f"a{i}_{Path(key).name}")
            if kind == "image":
                # Treat the image itself as a single frame at ts=0
                frames = [{"path": local, "ts_sec": 0.0}]
                transcript = {"language": "", "segments": [], "full_text": ""}
                print(f"  asset {i+1}: vision analyze 1 frame")
                frames_analysis = visual.analyze_video_frames(frames)
            else:
                print(f"  asset {i+1}: extract frames")
                frames = video_mod.extract_frames(local, tmp / f"frames_{i}", n=8)
                print(f"  asset {i+1}: transcribe")
                transcript = transcribe.transcribe(local)
                print(f"  asset {i+1}: vision analyze {len(frames)} frames")
                frames_analysis = visual.analyze_video_frames(frames)
            videos.append({
                "key": key,
                "kind": kind,
                "transcript": transcript,
                "frames_analysis": frames_analysis,
            })

        # Mark subtitle-style OCR (duplicates voiceover) so the report only shows real plашки.
        subtitle_filter.annotate_frames(videos)

        print("[5/9] Layer 1 regex scan")
        l1: list[dict] = []
        l1 += text_policy.scan_text(sub["adtitle"], "Adtitle")
        l1 += text_policy.scan_text(sub["description"], "Description")
        l1 += text_policy.scan_text(sub["button_cta"], "Button CTA")
        l1 += text_policy.scan_text(lander.get("text", ""), "Lander")
        for v_idx, v in enumerate(videos):
            label = "Картинка" if v.get("kind") == "image" else "Видео"
            for seg in v["transcript"]["segments"]:
                where = f"{label} {v_idx+1}, {_fmt_ts(seg['start'])} озвучка"
                l1 += text_policy.scan_text(seg["text"], where)
            for fr in v["frames_analysis"]:
                if fr["ocr_text"] and not fr.get("is_subtitle"):
                    suffix = "плашка" if v.get("kind") != "image" else "текст"
                    where = f"{label} {v_idx+1}" + (f", {fr['ts']} {suffix}" if v.get("kind") != "image" else f", {suffix}")
                    l1 += text_policy.scan_text(fr["ocr_text"], where)
        print(f"  layer 1 hits: {len(l1)}")

        print("[6/9] Layer 2 LLM checks")
        l2 = llm_checks.check(sub, lander, videos)
        print(f"  layer 2 violations: {len(l2.get('violations') or [])}")

        print("[7/9] Assemble verdict")
        v = verdict_mod.assemble(sub, l1, l2, videos)
        print(f"  overall={v['overall']} violations={len(v['violations'])} 18+={v['has_critical_18plus']}")

        print("[8/9] Render HTML page")
        html = render.render(sub, v, videos, lander, TEMPLATES)
        out_dir = DOCS / "sub" / sub["id"]
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "index.html").write_text(html, encoding="utf-8")
        page_url = f"{PAGES_BASE}/sub/{sub['id']}/"
        print(f"  → {page_url}")

        print("[9/9] POST verdict to Worker")
        api_client.post_verdict(sub["id"], v, page_url)
        print("  done")


def _fmt_ts(sec: float) -> str:
    return f"{int(sec // 60):02d}:{int(sec % 60):02d}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--submission-id", required=True)
    args = ap.parse_args()
    try:
        run(args.submission_id)
    except Exception:
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
