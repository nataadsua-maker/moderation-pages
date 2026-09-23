"""Gemini — запасное чтение кадра, когда NIM vision отказал или завис.

Зачем: 23.09.2026 бесплатная vision-модель NVIDIA (llama-3.2-11b) начала терять
треть кадров — либо мгновенный 500, либо зависание на 45с+. Подкруткой таймаутов
это не лечится: успешный ответ приходит за 14с (медиана), самый медленный живой —
37с, а зависший висит 45с+, то есть живое от мёртвого таймаутом не отделить.
Замер на реальном крео: NIM 10 успешных из 14, Gemini 6 из 6.

Схема (вариант C, решение Nataliia): NIM остаётся основным и тянет те ~70%, что
может, Gemini подхватывает ТОЛЬКО кадры, которые NIM уронил. Платим за поломку, а
не за весь объём — ~$14/мес против ~$70/мес при полном переезде на Gemini.

Нет ключа — модуль молча отключается, поведение ровно как до него: кадр считается
непрочитанным и идёт в статистику vision_stats(), гейт слепых вердиктов на месте.
"""
from __future__ import annotations
import base64
import json
import os
import random
import time
from pathlib import Path

import requests

API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
# Модель вынесена в env по тому же принципу, что и у NIM: когда провайдер ломает
# конкретную модель, свап делается переменной в moderate.yml, без релиза.
# flash-lite выбран по цене: $0.30/$2.50 за 1M против $0.75/$3.75 у 3.6-flash,
# при этом на замере 6/6 успешных и текст с плашки читает дословно.
MODEL = os.environ.get("GEMINI_VISION_MODEL", "gemini-3.5-flash-lite")
# 60с: на бесплатном тарифе медиана 26с, максимум на замере 42с. На платном
# ожидается быстрее, но потолок держим с запасом — это запасной путь, он и так
# зовётся только по сбойным кадрам.
TIMEOUT = int(os.environ.get("GEMINI_TIMEOUT", "60"))
RETRIES = int(os.environ.get("GEMINI_RETRIES", "2"))

_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def available() -> bool:
    """Ключ проброшен? Без него запасной путь просто выключен."""
    return bool(os.environ.get("GEMINI_API_KEY"))


def describe_frame(frame_path: Path, question: str) -> str:
    """Описать кадр тем же промптом, что уходит в NIM — ответ взаимозаменяем.

    Держим ровно тот же контракт, что и nim.vision_describe_frame: на входе кадр
    и вопрос, на выходе текст описания. Полиси по описанию судит прежний
    текстовый шаг, так что подмена читателя ничего дальше по пайплайну не меняет.
    """
    frame_path = Path(frame_path)
    mime = _MIME.get(frame_path.suffix.lower(), "image/jpeg")
    b64 = base64.b64encode(frame_path.read_bytes()).decode("ascii")
    payload = {
        "contents": [{
            "role": "user",
            "parts": [
                {"text": question},
                {"inline_data": {"mime_type": mime, "data": b64}},
            ],
        }],
        # Температура как у NIM-ветки: описание должно быть протокольным, а не
        # творческим — выдуманная плашка уезжает в вердикт и в заголовок оффера.
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 800},
    }
    url = f"{API_BASE}/{MODEL}:generateContent?key={os.environ['GEMINI_API_KEY']}"
    last = None
    for attempt in range(RETRIES):
        try:
            r = requests.post(url, json=payload, timeout=TIMEOUT,
                              headers={"Content-Type": "application/json"})
            if r.status_code == 200:
                data = r.json()
                parts = data["candidates"][0]["content"]["parts"]
                return "".join(p.get("text", "") for p in parts).strip()
            if r.status_code in (429, 500, 502, 503, 504):
                last = RuntimeError(f"Gemini {r.status_code}: {r.text[:200]}")
            else:
                raise RuntimeError(f"Gemini error {r.status_code}: {r.text[:300]}")
        except requests.exceptions.RequestException as e:
            last = e
        if attempt < RETRIES - 1:
            # Джиттер по той же причине, что и в nim.py: кадры идут параллельно,
            # без разброса потоки просыпаются одной секундой и бьют залпом.
            time.sleep((2 ** attempt) * (0.5 + random.random()))
    raise last if last else RuntimeError("Gemini failed")
