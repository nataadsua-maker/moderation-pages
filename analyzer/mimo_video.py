"""Video-native overlay/plашка detection через Xiaomi MiMo (video understanding API).

Заменяет единственный шаг старого пайплайна — по-кадровый OCR текста плашек на
видео (per-frame NIM в visual.py), который систематически путает текст физической
сцены (вышивка на одежде, вывески, этикетки/наклейки, настенные таблички) с
оверлеем, добавленным рекламодателем, и даёт "OCR-дрейф" — одна и та же плашка
читается по-разному на соседних кадрах, рождая 2-5 почти одинаковых "нарушений"
вместо одного.

Тест на 7 реальных заявках 01-02.10.2026 (5 ложных находок NIM + 2 настоящих
оверлея, все проверены вручную по кадрам): MiMo-v2.6-flash правильно отфильтровал
все 5 галлюцинаций (больничная вывеска, наклейка-инструкция в авто, вышивка на
футболке ×2, чистая галлюцинация на пустом месте) И нашёл оба настоящих оверлея
слово-в-слово — 7 из 7. Решение Nataliia 02.10.2026: сразу заменить NIM на
плашках, старый по-кадровый путь остаётся только автоматическим фолбэком (см.
detect_overlays() ниже) — ничего третьего (Gemini и т.п.) под это заводить не
нужно, это решает узкий редкий край, а не системную проблему.

MiMo НЕ проверяет policy (arrows/fake_ui/shock/gambling/weapons/18+ и т.п.) — это
по-прежнему делает per-frame NIM-проход в visual.py, его эта замена не трогает.

Fail-soft: нет ключа, сетевая ошибка, битый JSON или надёжный truncation после
повторной попытки — возвращаем None. Вызывающий код (moderate.py) в этом случае
просто не трогает frames_analysis — старый по-кадровый NIM-путь работает как
раньше, ничего не падает.
"""
from __future__ import annotations
import base64
import json
import os
from pathlib import Path

import requests

PROMPT = """Analyze this video ad creative frame by frame. List EVERY piece of text that
appears as an ON-SCREEN OVERLAY/CAPTION added by the advertiser (subtitles, banners, callouts,
badges drawn over the video). For each one give the exact text and the approximate timestamp
(MM:SS) it first appears.

Do NOT include text that is part of the physical filmed scene itself (text on a person's
clothing, a badge, a house number, a mailbox, a door plate, product packaging/label, a
storefront sign, a wall sign, a warning/instruction sticker, a license plate) — only text that
was composited onto the video by the advertiser.

Answer in strict JSON, nothing else: {"overlays": [{"text": "...", "start": "MM:SS"}]}"""

MODEL = os.environ.get("MIMO_VIDEO_MODEL", "mimo-v2.6-flash")
# flash уходит в долгие рассуждения (видели 2000-5000 reasoning-токенов на видео с
# насыщенными субтитрами) — на max_tokens=2000 ответ обрывался пустым (тест 01.10.2026).
MAX_TOKENS = int(os.environ.get("MIMO_MAX_TOKENS", "8000"))
MAX_TOKENS_RETRY = int(os.environ.get("MIMO_MAX_TOKENS_RETRY", "12000"))
TIMEOUT_SEC = int(os.environ.get("MIMO_TIMEOUT_SEC", "180"))


def _call(video_path: Path, max_tokens: int) -> dict | None:
    key = os.environ.get("MIMO_API_KEY")
    if not key:
        return None
    video_b64 = base64.b64encode(video_path.read_bytes()).decode()
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": PROMPT},
            {"type": "video_url", "video_url": {"url": f"data:video/mp4;base64,{video_b64}"},
             "fps": 4, "media_resolution": "max"},
        ]}],
        "max_tokens": max_tokens,
        "temperature": 0.1,
    }
    r = requests.post(
        "https://api.xiaomimimo.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=payload,
        timeout=TIMEOUT_SEC,
    )
    if r.status_code != 200:
        print(f"  MiMo: HTTP {r.status_code} — {r.text[:300]}")
        return None
    return r.json()


def _ts_to_sec(ts: str) -> float:
    try:
        parts = [float(p) for p in ts.split(":")]
        while len(parts) < 2:
            parts.insert(0, 0.0)
        if len(parts) == 2:
            m, s = parts
            return m * 60 + s
        h, m, s = parts[-3:]
        return h * 3600 + m * 60 + s
    except Exception:
        return 0.0


def _parse(result: dict) -> list[dict] | None:
    content = (result.get("choices") or [{}])[0].get("message", {}).get("content", "")
    content = (content or "").strip()
    if not content:
        return None  # пусто — почти всегда обрыв по max_tokens, не "нет оверлеев"
    if content.startswith("```"):
        content = content.strip("`")
        if content.lower().startswith("json"):
            content = content[4:]
    try:
        data = json.loads(content)
    except Exception as e:
        print(f"  MiMo: не распарсился JSON ({e})")
        return None
    overlays = data.get("overlays")
    if not isinstance(overlays, list):
        return None
    out = []
    for o in overlays:
        if not isinstance(o, dict):
            continue
        text = (o.get("text") or "").strip()
        if not text:
            continue
        start = o.get("start", "00:00")
        out.append({"ts": start, "ts_sec": _ts_to_sec(start), "text": text})
    return out


def detect_overlays(video_path: Path) -> list[dict] | None:
    """[{"ts","ts_sec","text"}, ...] или None, если MiMo недоступен/не осилил
    (фолбэк на старый по-кадровый путь остаётся на вызывающей стороне, в
    moderate.py). Пустой список — валидный ответ "MiMo посмотрел, оверлеев нет"."""
    if not os.environ.get("MIMO_API_KEY"):
        return None
    try:
        result = _call(video_path, MAX_TOKENS)
        if result is None:
            return None
        overlays = _parse(result)
        if overlays is None:
            # Похоже на обрыв по max_tokens — пробуем один раз с большим бюджетом,
            # прежде чем сдаваться на фолбэк.
            print("  MiMo: пустой/необрезанный ответ, повтор с большим max_tokens")
            result = _call(video_path, MAX_TOKENS_RETRY)
            overlays = _parse(result) if result else None
        return overlays
    except Exception as e:
        print(f"  MiMo: исключение ({type(e).__name__}: {e}), фолбэк на по-кадровый OCR")
        return None
