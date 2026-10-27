"""Апелляция на реджект TikTok Ads (вкладка «🎫 TikTok» сервиса модерации).

Баер даёт ID кабинета + ID креатива и причину реджекта (текстом или скрином).
Здесь:
  1. заявка из воркера (GET /api/internal/tt-appeal/<id>) + удачные примеры;
  2. причина со скрина — цепочка «image» из Админки;
  3. объявление из TikTok API: текст, ленд, видео (Smart+ и обычные), какие
     материалы отклонены. Причину API для Smart+ не отдаёт (reject_info пустой),
     поэтому её всегда приносит баер;
  4. сцены ролика (склейки ffmpeg) → по кадру на сцену → описание + весь текст в
     кадре; цитату из причины ищем в тексте кадров, это и есть триггер;
  5. ленд: финальный URL после редиректов + скрин в мобильном виде (Chrome);
  6. текст апелляции EN + RU, вердикт и совет — цепочка «text»;
  7. PDF в стиле эталона (sources/tiktok/appeals/examples) — Chrome --print-to-pdf;
  8. PDF и кадр в R2, результат в воркер: POST /api/internal/tt-appeal.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

import ai_config
import lander
import r2_client
import video as video_mod

TT_API = "https://business-api.tiktok.com/open_api/v1.3"
MAX_SCENES = 12
MAX_VIDEOS = 6
UA_MOBILE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")


# ---------- воркер ----------

def _auth() -> dict:
    return {"Authorization": f"Bearer {os.environ['VERDICT_SHARED_SECRET']}"}


def get_job(appeal_id: str) -> dict:
    r = requests.get(f"{os.environ['WORKER_URL']}/api/internal/tt-appeal/{appeal_id}", headers=_auth(), timeout=30)
    r.raise_for_status()
    return r.json()


def post_result(payload: dict) -> None:
    r = requests.post(f"{os.environ['WORKER_URL']}/api/internal/tt-appeal", headers=_auth(), json=payload, timeout=30)
    r.raise_for_status()


# ---------- TikTok API ----------

def tt_get(path: str, params: dict) -> dict:
    q = {k: (json.dumps(v) if isinstance(v, (list, dict)) else v) for k, v in params.items()}
    r = requests.get(f"{TT_API}/{path}", params=q, headers={"Access-Token": os.environ["TT_ACCESS_TOKEN"]}, timeout=30)
    r.raise_for_status()
    d = r.json()
    if d.get("code") != 0:
        raise RuntimeError(f"TikTok {path}: {d.get('message')}")
    return d.get("data") or {}


def fetch_ad(adv: str, ad_id: str) -> dict:
    """{name, text, landing, videos:[{video_id, rejected}], images:[{image_id}]} или пусто."""
    # Smart+ (новые кампании почти все такие)
    try:
        lst = tt_get("smart_plus/ad/get/", {"advertiser_id": adv, "filtering": {"smart_plus_ad_ids": [ad_id]}}).get("list") or []
    except Exception as e:
        print(f"  smart_plus/ad/get: {e}")
        lst = []
    if lst:
        ad = lst[0]
        rejected: set[str] = set()
        try:
            ri = tt_get("smart_plus/ad/review_info/", {"advertiser_id": adv, "smart_plus_ad_ids": [ad_id]})
            for m in ri.get("material_review_infos") or []:
                if m.get("review_status") not in ("ALL_AVAILABLE",):
                    rejected.add(str(m.get("ad_material_id")))
        except Exception as e:
            print(f"  review_info: {e}")
        videos, images = [], []
        for c in ad.get("creative_list") or []:
            info = c.get("creative_info") or {}
            mid = str(c.get("ad_material_id"))
            vid = (info.get("video_info") or {}).get("video_id")
            if vid:
                videos.append({"video_id": vid, "rejected": mid in rejected})
            elif info.get("image_info"):
                images.append({"web_uri": info["image_info"][0].get("web_uri"), "rejected": mid in rejected})
        texts = [t.get("ad_text", "") for t in ad.get("ad_text_list") or []]
        lps = [u.get("landing_page_url", "") for u in ad.get("landing_page_url_list") or []]
        return {"name": ad.get("ad_name", ""), "text": " / ".join(t for t in texts if t),
                "landing": lps[0] if lps else "", "videos": videos, "images": images}
    # Обычное объявление
    lst = tt_get("ad/get/", {"advertiser_id": adv, "filtering": {"ad_ids": [ad_id]},
                             "fields": ["ad_id", "ad_name", "ad_text", "landing_page_url", "video_id", "image_ids"]}).get("list") or []
    if not lst:
        return {}
    ad = lst[0]
    return {"name": ad.get("ad_name", ""), "text": ad.get("ad_text", ""), "landing": ad.get("landing_page_url", ""),
            "videos": [{"video_id": ad["video_id"], "rejected": True}] if ad.get("video_id") else [],
            "images": [{"image_id": i, "rejected": True} for i in ad.get("image_ids") or []]}


def download_video(adv: str, video_id: str, dest: Path) -> Path | None:
    lst = tt_get("file/video/ad/info/", {"advertiser_id": adv, "video_ids": [video_id]}).get("list") or []
    url = lst[0].get("preview_url") if lst else None
    if not url:
        return None
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    dest.write_bytes(r.content)
    return dest


def download_image(adv: str, img: dict, dest: Path) -> Path | None:
    url = None
    if img.get("image_id"):
        lst = tt_get("file/image/ad/info/", {"advertiser_id": adv, "image_ids": [img["image_id"]]}).get("list") or []
        url = lst[0].get("image_url") if lst else None
    elif img.get("web_uri"):
        url = f"https://p16-common-sign.tiktokcdn.com/{img['web_uri']}~tplv-noop.image"
    if not url:
        return None
    r = requests.get(url, timeout=60)
    if r.status_code != 200:
        return None
    dest.write_bytes(r.content)
    return dest


# ---------- кадры ----------

OCR_PROMPT = (
    "This is a frame from a TikTok ad video. Reply in English in exactly two lines:\n"
    "SCENE: one short sentence describing what is shown.\n"
    "TEXT: every piece of visible text, copied exactly, including small text on phone screens, "
    "notifications, signs, packaging and captions; separate pieces with ' | '. Write NONE if there is no text."
)


def scene_cuts(path: Path, dur: float) -> list[tuple[float, float]]:
    r = subprocess.run(["ffmpeg", "-i", str(path), "-vf", "select='gt(scene,0.3)',showinfo", "-f", "null", "-"],
                       capture_output=True, text=True)
    cuts = sorted(float(t) for t in re.findall(r"pts_time:([0-9.]+)", r.stderr))
    edges = [0.0] + [c for c in cuts if 0.4 < c < dur - 0.4] + [dur]
    scenes = [(a, b) for a, b in zip(edges, edges[1:]) if b - a >= 0.3]
    if len(scenes) > MAX_SCENES:  # слишком дробно — равные отрезки
        step = dur / MAX_SCENES
        scenes = [(i * step, (i + 1) * step) for i in range(MAX_SCENES)]
    return scenes or [(0.0, dur)]


def grab(path: Path, ts: float, out: Path, scale: int = 540) -> Path:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{ts:.2f}", "-i", str(path), "-frames:v", "1",
                    "-vf", f"scale={scale}:-2", "-q:v", "3", str(out)], check=True)
    return out


def read_frame(p: Path) -> dict:
    try:
        txt, _ = ai_config.describe_image(p, OCR_PROMPT)
    except Exception as e:
        print(f"  кадр {p.name}: {e}")
        return {"scene": "", "text": ""}
    scene = re.search(r"SCENE:\s*(.*)", txt)
    text = re.search(r"TEXT:\s*(.*)", txt, re.S)
    t = (text.group(1).strip() if text else "").strip()
    return {"scene": scene.group(1).strip() if scene else txt[:200], "text": "" if t.upper().startswith("NONE") else t}


def analyze_video(path: Path, tag: str, tmp: Path) -> dict:
    dur = video_mod.probe_duration_sec(path)
    scenes = scene_cuts(path, dur)
    frames = [grab(path, (a + b) / 2, tmp / f"{tag}_s{i:02d}.jpg") for i, (a, b) in enumerate(scenes)]
    with ThreadPoolExecutor(4) as ex:
        reads = list(ex.map(read_frame, frames))
    return {"tag": tag, "path": path, "duration": dur, "scenes": [
        {"i": i, "start": round(a, 1), "end": round(b, 1), "frame": f, **rd}
        for i, ((a, b), f, rd) in enumerate(zip(scenes, frames, reads))]}


def _words(s: str) -> list[str]:
    # грубый стемминг: OCR и TikTok расходятся в окончаниях (ALERT / ALERTS)
    return [w[:-1] if len(w) > 3 and w.endswith("s") else w for w in re.findall(r"[a-z0-9]+", s.lower())]


def quotes_in(reason: str) -> list[str]:
    q = re.findall(r'["“”«»]([^"“”«»]{4,200})["“”«»]', reason)
    return [x.strip() for x in q if len(_words(x)) >= 2]


def match_quote(quotes: list[str], text: str) -> float:
    tw = set(_words(text))
    best = 0.0
    for q in quotes:
        qw = _words(q)
        if qw:
            best = max(best, sum(w in tw for w in qw) / len(qw))
    return best


# ---------- ленд ----------

def chrome() -> str:
    for c in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        if shutil.which(c):
            return c
    raise RuntimeError("нет Chrome на раннере")


def landing_info(url: str, tmp: Path) -> dict:
    if not url:
        return {}
    info = lander.fetch(url)
    final = info.get("url") or url
    shot = tmp / "landing.png"
    try:
        subprocess.run([chrome(), "--headless=new", "--no-sandbox", "--disable-gpu", f"--user-data-dir={tmp / 'chrome'}", "--hide-scrollbars",
                        f"--user-agent={UA_MOBILE}", "--window-size=500,1300", "--force-device-scale-factor=2", "--blink-settings=preferredColorScheme=1",
                        "--virtual-time-budget=6000", f"--screenshot={shot}", final],
                       check=True, capture_output=True, timeout=90)
    except Exception as e:
        print(f"  скрин ленда: {e}")
        shot = None
    return {"url": final, "host": urlparse(final).netloc.removeprefix("www."), "title": info.get("title", ""),
            "text": (info.get("text") or "")[:3000], "shot": shot if shot and shot.exists() else None}


# ---------- текст апелляции ----------

BASE_EXAMPLES = [
    {"reason": "Account suspension: impersonation / misrepresentation (Klarna referenced in ad and landing page)",
     "appeal_en": "Hello TikTok Ads Review Team. I request a manual review of the suspension for alleged impersonation. "
                  "The advertised domain, gengavox.com, is an independent editorial/review website. It does not claim to be "
                  "Klarna, a Klarna employee, or any other person or company. Klarna is mentioned only as the subject of an "
                  "informational review of deferred payments/BNPL in Portugal. The separate domain and \"Guide 2026\" wording "
                  "identify third-party editorial content. The videos depict the reviewed topic and a generic presenter; neither "
                  "states that the advertiser or presenter represents Klarna. A product-interface visual may have caused an "
                  "automated false positive, but there was no intent to mislead users. I am ready to add a prominent \"Independent "
                  "review - not affiliated with or endorsed by Klarna\" disclosure and replace any visual considered ambiguous. "
                  "Please reinstate the account after human review, or identify the exact element requiring correction. "
                  "Supporting screenshots are attached."},
    {"reason": "Financial services. The on-screen text \"wish to receive FRAUD ALERTS\" was flagged because the ad references "
               "prohibited financial services including fraud alert offerings.",
     "appeal_en": "Hello TikTok Ads Review Team. I request a manual review of the \"Financial services\" rejection of one video "
                  "material in ad 1880145004504450 (United States). The ad does not promote any financial service, bank, card "
                  "product or fraud alert service. It promotes an informational article about email marketing strategies for "
                  "small businesses on yonder.tips. The flagged text \"wish to receive FRAUD ALERTS\" is incidental: it is small "
                  "notification text inside a stock-footage shot of a phone, visible for about 2 seconds at the very end of the "
                  "video (13.8-15.8 s). It is not part of the voiceover, the on-screen headline or the ad text, and the landing "
                  "page contains no banking, credit or fraud alert content. There was no intent to reference financial services. "
                  "I am ready to remove this final scene so the video contains only email-marketing footage. Please approve the "
                  "material after human review, or confirm that the edited version without the final scene is acceptable. "
                  "Supporting screenshots are attached."},
]

SYSTEM = """You write appeals to TikTok Ads review for a performance-marketing team and explain the case to the buyer in Russian.
Both example appeals below were APPROVED by TikTok. Follow their structure:
1) ask for a manual/human review and name the rejection reason in TikTok's own words, with the ad or account ID;
2) state what the ad actually promotes (from the ad text and landing page);
3) deny the specific concern with facts (domain, wording on the page, what is visible in the frames), not opinions;
4) name the likely trigger yourself and explain why it is incidental or descriptive, without admitting a violation ("no intent to ...");
5) give concrete good-faith commitments (exact disclaimer text, removing a specific scene, replacing a specific visual);
6) fallback request: approve/reinstate, or identify the exact element to correct; end with "Supporting screenshots are attached."
Rules: one paragraph, 130-200 words, polite and calm, plain English. Use ONLY facts from the data; never invent disclaimers,
licenses, partnerships or numbers. Frame text comes from OCR and may be misread: never quote on-screen text other than the exact
quote given in the rejection reason; describe other visible text generically (e.g. "a phone notification"). The examples show the
structure only, write this case in your own words.
Do not use em dashes. If the violation is genuinely present in the creative (the flagged text
or visual really is there and is the message of the ad), still write the best honest appeal, but set verdict "real" and in
advice_ru recommend fixing and re-uploading as the faster path. If the trigger is incidental or the reason does not fit the ad,
verdict "false_positive". If you cannot tell, "unclear".
advice_ru: 1-2 short sentences in simple Russian for the buyer, no jargon, never use the "—" dash.
appeal_ru: faithful natural Russian translation of appeal_en; no English words except quotes, brand names and IDs; avoid
gendered first-person forms (write "Готовы удалить", "Просим", not "Я готов").
Return strict JSON with keys:
verdict, advice_ru, flagged_scene (scene id like "v1:3" or null), flagged_note_ru (one sentence: what exactly in that scene triggered it, or ""),
appeal_en, appeal_ru"""

SYSTEM_PDF = """You prepare the text of a PDF attachment for a TikTok Ads appeal. The appeal text is final; the PDF repeats the same
arguments with evidence. Use ONLY facts from the data and the appeal; do not add new claims or commitments. English, short,
factual, each bullet <= 20 words, no em dashes.
Return strict JSON:
{title, subtitle, intro (2 sentences: the request and what the ad/site really is), core_clarification,
 points: [{title (2-3 words), text (<= 20 words)}] x3, requested_resolution,
 landing_observations: [4 bullets about what is visible on the landing page], landing_why,
 frame_review: [3 bullets about the creative overall], flagged_review: [3 bullets about the flagged shot, or [] if none],
 interpretation, statement (2-3 sentences), commitments: [4 bullets taken from the appeal], request}"""


def write_appeal(job: dict, ad: dict, land: dict, videos: list[dict], flagged: dict | None) -> tuple[dict, str]:
    scenes = []
    for v in videos:
        for s in v["scenes"]:
            scenes.append({"id": f"{v['tag']}:{s['i']}", "time": f"{s['start']}-{s['end']} s of {v['duration']:.1f} s",
                           "scene": s["scene"], "text": s["text"]})
    data = {
        "case": "account suspension" if job["kind"] == "account" else "ad / creative rejection",
        "advertiser_id": job["advertiser_id"], "ad_id": job.get("ad_id") or None,
        "rejection_reason": job["reason"],
        # имя объявления внутреннее (нейминг команды) — модели не даём, чтобы не попало в PDF
        "ad_text": ad.get("text"),
        "landing": {"domain": land.get("host"), "url": land.get("url"), "title": land.get("title"), "text_excerpt": land.get("text")},
        "video_scenes": scenes,
        "quote_match": {"scene": flagged["id"], "time": flagged["time"]} if flagged else None,
        "buyer_note": job.get("note") or None,
    }
    examples = BASE_EXAMPLES + [{"reason": e["reason"], "appeal_en": e["appeal_en"]} for e in job.get("examples") or []]
    payload = "APPROVED EXAMPLES:\n" + json.dumps(examples, ensure_ascii=False, indent=1) + \
              "\n\nCASE DATA:\n" + json.dumps(data, ensure_ascii=False, indent=1)
    out, model = ai_config.text_json(SYSTEM, payload, max_tokens=2500)
    print(f"  апелляция: {model}")
    try:
        pdf, _ = ai_config.text_json(SYSTEM_PDF, "APPEAL:\n" + str(out.get("appeal_en", "")) +
                                     "\n\nCASE DATA:\n" + json.dumps(data, ensure_ascii=False, indent=1), max_tokens=2500)
    except Exception as e:  # без текстов PDF соберём из самой апелляции
        print(f"  тексты PDF: {e}")
        pdf = {"intro": out.get("appeal_en", "")}
    out["pdf"] = pdf
    return out, model


# ---------- PDF ----------

CSS = """@page{size:Letter;margin:0}*{box-sizing:border-box}
body{margin:0;font-family:Helvetica,Arial,'Liberation Sans',sans-serif;color:#1f2937;font-size:13px;line-height:1.5}
.page{width:8.5in;height:11in;position:relative;page-break-after:always;overflow:hidden}.page:last-child{page-break-after:auto}
.hd{background:#16233b;color:#fff;padding:34px 48px 26px}.tag{display:inline-block;background:#f0567f;color:#fff;font-weight:700;font-size:10px;letter-spacing:.06em;padding:6px 16px;border-radius:14px}
.pg{position:absolute;right:48px;top:40px;font-size:10px;font-weight:700;letter-spacing:.05em}h1{font-size:30px;margin:14px 0 4px}.sub{color:#c9d2e0;font-size:12px}
.bd{padding:26px 48px}.row{display:flex;gap:12px}.box{flex:1;border:1px solid #dbe2ea;border-radius:10px;background:#f3f6f9;padding:14px 16px}
.lbl{font-size:9px;font-weight:700;letter-spacing:.06em;color:#6b7280;text-transform:uppercase}.val{font-size:14px;font-weight:700;margin-top:4px;word-break:break-all}
h2{font-size:17px;margin:22px 0 8px}.pink{border:1.5px solid #f0567f;background:#fff1f5;border-radius:12px;padding:16px 20px;margin-top:16px}.pink .lbl{color:#f0567f}
.teal{background:#e7f6f5;border-radius:12px;padding:16px 20px;margin-top:16px}.teal .lbl{color:#21a39a}
.card{flex:1;border:1px solid #dbe2ea;border-radius:10px;padding:14px 16px;min-height:128px}.card .n{color:#21a39a;font-size:10px;font-weight:700}
.card b{display:block;font-size:13.5px;margin:6px 0}.card p{margin:0;color:#4b5563;font-size:12px}ul{margin:6px 0 0;padding-left:18px}li{margin:4px 0}
.ft{position:absolute;bottom:22px;left:48px;right:48px;border-top:1px solid #dbe2ea;padding-top:8px;font-size:9.5px;color:#6b7280;display:flex;justify-content:space-between}
.frames{display:flex;gap:10px;flex-wrap:wrap}.frames figure{margin:0;text-align:center;font-size:10px;color:#6b7280}
.frames img{width:100px;border-radius:6px;border:1px solid #dbe2ea;display:block;margin-bottom:4px}.flag img{border:2.5px solid #f0567f}
.ok{display:flex;gap:12px;align-items:center;border:1px solid #dbe2ea;background:#f3f6f9;border-radius:10px;padding:12px 16px;margin-top:10px}
.ok span{background:#21a39a;color:#fff;font-size:9px;font-weight:700;border-radius:50%;width:22px;height:22px;display:flex;align-items:center;justify-content:center;flex:none}
.sign{background:#16233b;color:#fff;border-radius:12px;padding:20px 24px;margin-top:20px;display:flex;justify-content:space-between;align-items:flex-end}.sign .lbl{color:#c9d2e0}"""


def _e(s) -> str:
    return html.escape(str(s or ""))


def _ul(items) -> str:
    return "<ul>" + "".join(f"<li>{_e(i)}</li>" for i in (items or [])[:5]) + "</ul>"


def build_pdf(out: Path, job: dict, ad: dict, land: dict, pdf: dict, frames: list[dict], flagged: dict | None,
              crop: Path | None, tmp: Path) -> None:
    date = datetime.now(timezone.utc).strftime("%-d %B %Y")
    domain = land.get("host") or "-"
    is_ad = job["kind"] == "ad"
    pages = []

    def page(tag: str, title: str, sub: str, body: str) -> None:
        pages.append((tag, title, sub, body))

    points = (pdf.get("points") or [])[:3]
    page("APPEAL", pdf.get("title") or ("Ad Rejection Appeal" if is_ad else "Account Suspension Appeal"),
         pdf.get("subtitle") or "Supporting evidence for manual review", f"""
  <div class="row"><div class="box"><div class="lbl">Platform</div><div class="val">TikTok Ads</div></div>
   <div class="box"><div class="lbl">{'Ad ID' if is_ad and job.get('ad_id') else 'Advertiser ID'}</div><div class="val">{_e(job.get('ad_id') if is_ad and job.get('ad_id') else job['advertiser_id'])}</div></div>
   <div class="box"><div class="lbl">Request</div><div class="val">Manual review</div></div></div>
  <h2>To the TikTok Ads Review Team</h2><p>{_e(pdf.get('intro'))}</p>
  <div class="pink"><div class="lbl">Core clarification</div><p style="margin:6px 0 0">{_e(pdf.get('core_clarification'))}</p></div>
  <h2>Why the {'rejection' if is_ad else 'suspension'} appears to be a false positive</h2>
  <div class="row">{''.join(f'<div class="card"><div class="n">0{i+1}</div><b>{_e(p.get("title"))}</b><p>{_e(p.get("text"))}</p></div>' for i, p in enumerate(points))}</div>
  <div class="teal"><div class="lbl">Requested resolution</div><p style="margin:6px 0 0">{_e(pdf.get('requested_resolution'))}</p></div>""")

    if land.get("shot"):
        page("EXHIBIT A", "Landing Page Evidence", f"The advertised destination: {domain}", f"""
  <div style="display:flex;gap:26px"><div style="flex:none;width:300px"><img src="{land['shot'].name}" style="width:300px;max-height:7.4in;object-fit:cover;object-position:top;border:1px solid #dbe2ea;border-radius:10px">
  <div style="font-size:10px;color:#6b7280;margin-top:6px">{_e(domain)} · mobile view, {date}</div></div>
  <div style="flex:1"><h2 style="margin-top:0">Visible observations</h2>{_ul(pdf.get('landing_observations'))}
  <div class="pink"><div class="lbl">Why this matters</div><p style="margin:6px 0 0">{_e(pdf.get('landing_why'))}</p></div>
  {f'<div class="teal"><div class="lbl">Ad text</div><p style="margin:6px 0 0">"{_e(ad.get("text"))}"</p></div>' if ad.get('text') else ''}</div></div>""")

    if frames:
        figs = "".join(
            f'<figure class="{"flag" if f.get("flag") else ""}"><img src="{f["path"].name}">{_e(f["label"])}</figure>' for f in frames[:5])
        crop_html = f'<h2 style="margin-top:14px">Flagged element (enlarged)</h2><img src="{crop.name}" style="max-width:100%;max-height:2.1in;display:block;margin:0 auto;border-radius:8px;border:2px solid #f0567f">' if crop else ""
        page("EXHIBIT B", "Creative Evidence", "Frames extracted from the submitted creative", f"""
  <div class="frames">{figs}</div>{crop_html}
  <div class="row" style="margin-top:14px"><div class="box" style="background:#fff"><div class="lbl">Frame review</div>{_ul(pdf.get('frame_review'))}</div>
  {f'<div class="box" style="background:#fff"><div class="lbl">About the flagged shot</div>{_ul(pdf.get("flagged_review"))}</div>' if flagged else ''}</div>
  <div class="pink"><div class="lbl">Interpretation</div><p style="margin:6px 0 0">{_e(pdf.get('interpretation'))}</p></div>""")

    oks = "".join(f'<div class="ok"><span>OK</span>{_e(c)}</div>' for c in (pdf.get("commitments") or [])[:4])
    page("STATEMENT", "Policy Alignment and Request", "A concise, good-faith explanation for the reviewing team", f"""
  <h2>{'Product statement' if is_ad else 'Identity and affiliation statement'}</h2><p>{_e(pdf.get('statement'))}</p>
  <h2 style="margin-top:28px">Good-faith commitments</h2>{oks}
  <div class="pink" style="margin-top:26px"><div class="lbl">Request to the reviewer</div><p style="margin:6px 0 0">{_e(pdf.get('request'))}</p></div>
  <div class="sign"><div><div class="lbl">Submitted respectfully</div><div style="font-size:17px;font-weight:700;margin-top:6px">Advertiser for {_e(domain)}</div></div><div style="font-size:11px;color:#c9d2e0">{date}</div></div>""")

    n = len(pages)
    body = "".join(f"""<div class="page"><div class="hd"><span class="tag">{_e(t)}</span><span class="pg">PAGE {i+1} / {n}</span>
<h1>{_e(ti)}</h1><div class="sub">{_e(su)}</div></div><div class="bd">{b}</div>
<div class="ft"><span>Supporting evidence for manual review · {date}</span><span>Advertised domain: {_e(domain)}</span></div></div>"""
                   for i, (t, ti, su, b) in enumerate(pages))
    src = tmp / "appeal.html"
    src.write_text(f'<!doctype html><html lang="en"><head><meta charset="utf-8"><style>{CSS}</style></head><body>{body}</body></html>')
    subprocess.run([chrome(), "--headless=new", "--no-sandbox", "--disable-gpu", f"--user-data-dir={tmp / 'chrome'}", "--no-pdf-header-footer",
                    f"--print-to-pdf={out}", src.as_uri()], check=True, capture_output=True, timeout=120)


# ---------- сборка ----------

def run(appeal_id: str) -> None:
    job = get_job(appeal_id)
    adv, ad_id = job["advertiser_id"], job.get("ad_id") or ""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        reason = job.get("reason") or ""
        if job.get("reason_key"):
            print("[1] причина со скрина")
            shot = r2_client.download(job["reason_key"], tmp / ("reason" + Path(job["reason_key"]).suffix))
            txt, _ = ai_config.describe_image(shot, "This is a screenshot of a TikTok Ads rejection notice. Copy the rejection "
                                                    "reason title and the full rejection details text exactly, nothing else.")
            reason = (reason + "\n" + txt).strip() if reason else txt
        job["reason"] = reason

        ad: dict = {}
        if job["kind"] == "ad" and ad_id:
            print(f"[2] объявление {ad_id} из кабинета {adv}")
            ad = fetch_ad(adv, ad_id)
            if not ad and not job.get("video_key"):
                raise RuntimeError("объявление не найдено в этом кабинете (проверь ID или загрузи креатив вручную)")

        print("[3] креатив")
        media: list[tuple[str, Path]] = []
        if job.get("video_key"):
            media.append(("v1", r2_client.download(job["video_key"], tmp / ("manual" + Path(job["video_key"]).suffix))))
        else:
            vids = ad.get("videos") or []
            rej = [v for v in vids if v["rejected"]]
            pick = rej or vids
            for i, v in enumerate(pick[:MAX_VIDEOS]):
                p = download_video(adv, v["video_id"], tmp / f"v{i+1}.mp4")
                if p:
                    media.append((f"v{i+1}", p))
            if not media:
                imgs = ad.get("images") or []
                for i, im in enumerate(([x for x in imgs if x["rejected"]] or imgs)[:3]):
                    p = download_image(adv, im, tmp / f"i{i+1}.jpg")
                    if p:
                        media.append((f"i{i+1}", p))

        quotes = quotes_in(reason)
        videos: list[dict] = []
        flagged = None
        for tag, p in media:
            if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
                rd = read_frame(p)
                v = {"tag": tag, "path": p, "duration": 0.0,
                     "scenes": [{"i": 0, "start": 0.0, "end": 0.0, "frame": p, **rd}]}
            else:
                print(f"  сцены {tag}")
                v = analyze_video(p, tag, tmp)
            videos.append(v)
            if quotes and not flagged:
                best = max(v["scenes"], key=lambda s: match_quote(quotes, s["text"]))
                if match_quote(quotes, best["text"]) >= 0.5:
                    flagged = {"id": f"{tag}:{best['i']}", "time": f"{best['start']}-{best['end']} s", "video": v, "scene": best}
            if flagged:
                break  # нашли ролик-нарушитель — остальные не смотрим

        print("[4] ленд")
        land = landing_info(job.get("landing_url") or ad.get("landing") or "", tmp)

        print("[5] текст апелляции")
        out, model = write_appeal(job, ad, land, videos, flagged)
        if not flagged and out.get("flagged_scene"):
            tag, _, idx = str(out["flagged_scene"]).partition(":")
            for v in videos:
                if v["tag"] == tag and idx.isdigit() and int(idx) < len(v["scenes"]):
                    s = v["scenes"][int(idx)]
                    flagged = {"id": out["flagged_scene"], "time": f"{s['start']}-{s['end']} s", "video": v, "scene": s}

        print("[6] PDF")
        frames: list[dict] = []
        crop = None
        flag_key = None
        if videos:
            v = flagged["video"] if flagged else videos[0]
            sc = v["scenes"]
            chosen = [s for s in sc if not (flagged and s is flagged["scene"])]
            step = max(1, len(chosen) // 3)
            picks = chosen[::step][:3] + ([flagged["scene"]] if flagged else [])
            picks = sorted(picks, key=lambda s: s["start"])[:5]
            for s in picks:
                is_flag = bool(flagged and s is flagged["scene"])
                label = f"{s['start']:.1f}s" + (" · flagged shot" if is_flag else "") if v["duration"] else ("flagged" if is_flag else "creative")
                frames.append({"path": s["frame"], "label": label, "flag": is_flag})
            if flagged and v["duration"]:
                s = flagged["scene"]
                big = grab(v["path"], (s["start"] + s["end"]) / 2, tmp / "flag_full.jpg", scale=720)
                crop = big
                flag_key = r2_client.upload(big, f"tt-appeals/{appeal_id}/flag.jpg", "image/jpeg")

        pdf_name = f"tiktok_{'ad_rejection' if job['kind'] == 'ad' else 'account_suspension'}_appeal_{ad_id or adv}.pdf"
        pdf_path = tmp / pdf_name
        build_pdf(pdf_path, job, ad, land, out.get("pdf") or {}, frames, flagged, crop, tmp)
        pdf_key = r2_client.upload(pdf_path, f"tt-appeals/{appeal_id}/{pdf_name}", "application/pdf")

        fl = None
        if flagged:
            s = flagged["scene"]
            fl = {"ts_start": s["start"], "ts_end": s["end"], "note": out.get("flagged_note_ru") or s["scene"], "frame_key": flag_key}
        post_result({
            "appeal_id": appeal_id, "ok": True,
            # имя объявления внутреннее (нейминг команды) — модели не даём, чтобы не попало в PDF
        "ad_text": ad.get("text"), "landing_url": land.get("url"),
            "verdict": out.get("verdict"), "advice_ru": out.get("advice_ru"), "flagged": fl,
            "appeal_en": out.get("appeal_en"), "appeal_ru": out.get("appeal_ru"),
            "pdf_key": pdf_key, "pdf_name": pdf_name, "model": model,
        })
        print(f"  done: verdict={out.get('verdict')} flagged={flagged['id'] if flagged else None} model={model}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--appeal-id", required=True)
    a = ap.parse_args()
    try:
        run(a.appeal_id)
    except Exception as e:
        traceback.print_exc()
        try:
            post_result({"appeal_id": a.appeal_id, "ok": False, "error": str(e)[:300]})
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
