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
import drive
import lander as lander_mod
import llm_checks
import r2_client
import subtitle_filter
import text_policy
import transcribe
import translate
import verdict as verdict_mod
import video as video_mod
import visual


def run(submission_id: str) -> None:
    print(f"[1/9] Fetching submission {submission_id}")
    sub = api_client.fetch_submission(submission_id)
    print(f"  offer={sub.get('offer')!r} videos={len(sub.get('video_keys', []))}")

    print("[2/9] Fetching lander")
    lander = lander_mod.fetch(sub["lander_url"])
    print(f"  ok={lander['ok']} title={lander.get('title', '')[:80]!r}")

    print(f"[3-4/9] Processing {len(sub['video_keys'])} assets")
    IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
    local_assets: list[Path] = []  # tracked for Drive archive
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        videos: list[dict] = []
        for i, key in enumerate(sub["video_keys"]):
            ext = Path(key).suffix.lower()
            kind = "image" if ext in IMAGE_EXTS else "video"
            print(f"  asset {i+1} ({kind}): download {key}")
            local = r2_client.download(key, tmp / f"a{i}_{Path(key).name}")
            local_assets.append(local)
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

        print("[5/9] Layer 1 regex scan (creative only — lander is partner's responsibility)")
        l1: list[dict] = []
        l1 += text_policy.scan_text(sub["adtitle"], "Adtitle")
        l1 += text_policy.scan_text(sub["description"], "Description")
        l1 += text_policy.scan_text(sub["button_cta"], "Button CTA")
        # Lander is NOT scanned for stop-words — partner (System1/ADS) owns lander compliance.
        # We only fetch the lander to feed it as context to Layer 2 LLM for Ad-to-Page Match.
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
        # Guard: if the lander is unreachable or its text is suspiciously short,
        # auto-flag for manual review — we can't reliably do Ad-to-Page Match.
        lander_text_len = len(lander.get("text", "") or "")
        lander_ok = lander.get("ok", False)
        if not lander_ok or lander_text_len < 200:
            l2 = {
                "violations": [{
                    "where": "Lander",
                    "quote": lander.get("url", ""),
                    "reason": (
                        f"Ленд недоступен (ok={lander_ok}) или слишком мало контента ({lander_text_len} симв.) — "
                        f"автопроверка Ad-to-Page Match невозможна, нужна ручная проверка модератором."
                    ),
                    "policy_section": "manual_review",
                    "category": "standard",
                }],
                "confidence": 0.0,
                "_skipped": "lander_unavailable",
            }
            print(f"  layer 2 SKIPPED — lander unreachable, forcing manual_review")
        else:
            l2 = llm_checks.check(sub, lander, videos)
            print(f"  layer 2 violations: {len(l2.get('violations') or [])}")

        print("[7/9] Assemble verdict")
        v = verdict_mod.assemble(sub, l1, l2, videos)
        print(f"  overall={v['overall']} violations={len(v['violations'])} 18+={v['has_critical_18plus']}")

        print("[8/9] Build media_analysis (transcripts + overlays + RU)")
        media_analysis = build_media_analysis(videos)

        print("[9a/9] Archive assets to Google Drive")
        try:
            drive_url = drive.upload_submission_assets(
                local_assets,
                submission_id=sub["id"],
                offer=sub.get("offer", ""),
                buyer_username=sub.get("buyer_username"),
                metadata={
                    "id": sub["id"],
                    "tg_id": sub.get("tg_id"),
                    "buyer_username": sub.get("buyer_username"),
                    "buyer_first_name": sub.get("buyer_first_name"),
                    "platform": sub.get("platform"),
                    "offer": sub.get("offer"),
                    "lander_url": sub.get("lander_url"),
                    "adtitle": sub.get("adtitle"),
                    "description": sub.get("description"),
                    "button_cta": sub.get("button_cta"),
                    "created_at": sub.get("created_at"),
                    "verdict": v,
                },
            )
            if drive_url:
                print(f"  drive: {drive_url}")
        except Exception as e:
            print(f"  drive: archive failed (non-fatal): {e}")

        print("[9b/9] POST verdict to Worker (single source of truth: Worker /sub/<id>)")
        worker_url = os.environ["WORKER_URL"]
        page_url = f"{worker_url}/sub/{sub['id']}"
        api_client.post_verdict(sub["id"], v, page_url, media_analysis)
        print("  done")


def build_media_analysis(videos: list[dict]) -> list[dict]:
    """Pack transcript + non-subtitle overlays for each asset, with RU translations."""
    # Collect all EN strings in a single batch (segments + non-subtitle overlays)
    en_strings: list[str] = []
    locations: list[tuple[int, str, int]] = []  # (video_idx, kind, item_idx)
    for v_idx, v in enumerate(videos):
        for s_idx, seg in enumerate(v["transcript"].get("segments", [])):
            en_strings.append(seg["text"])
            locations.append((v_idx, "segment", s_idx))
        for f_idx, fr in enumerate(v.get("frames_analysis", [])):
            if not fr.get("ocr_text"):
                continue
            if fr.get("is_subtitle"):
                continue
            en_strings.append(fr["ocr_text"])
            locations.append((v_idx, "overlay", f_idx))

    print(f"  translating {len(en_strings)} strings to RU...")
    ru_strings = translate.batch_translate(en_strings) if en_strings else []

    # Build per-video media_analysis
    ru_map: dict[tuple[int, str, int], str] = {}
    for loc, ru in zip(locations, ru_strings):
        ru_map[loc] = ru

    out: list[dict] = []
    for v_idx, v in enumerate(videos):
        segments = []
        for s_idx, seg in enumerate(v["transcript"].get("segments", [])):
            segments.append({
                "start": seg["start"],
                "end": seg["end"],
                "text_en": seg["text"],
                "text_ru": ru_map.get((v_idx, "segment", s_idx), ""),
            })
        overlays = []
        for f_idx, fr in enumerate(v.get("frames_analysis", [])):
            if not fr.get("ocr_text"):
                continue
            overlays.append({
                "ts": fr["ts"],
                "ts_sec": fr["ts_sec"],
                "text_en": fr["ocr_text"],
                "text_ru": ru_map.get((v_idx, "overlay", f_idx), ""),
                "is_subtitle": bool(fr.get("is_subtitle")),
            })
        out.append({
            "key": v["key"],
            "kind": v.get("kind", "video"),
            "transcript_segments": segments,
            "overlays": overlays,
        })
    return out


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
