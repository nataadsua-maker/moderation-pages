"""NVIDIA NIM client (OpenAI-compatible). Used for both Vision (frames) and Text (lander/textual policy)."""
from __future__ import annotations
import base64
import json
import os
import random
import time
from pathlib import Path

import requests

NIM_BASE = "https://integrate.api.nvidia.com/v1"
# Модели вынесены в env: когда NVIDIA перестаёт отдавать конкретную модель, свап
# делается переменной в moderate.yml, без правки кода и релиза.
# 10.08.2026 llama-3.2-90b-vision перестала отвечать на наш ключ (запрос висит до
# таймаута вместо ошибки, мелкие модели при этом отвечают за секунды) → vision
# переведён на 11b. Вернуть 90B можно одной переменной, когда она оживёт.
# llama-3.3-70b в те же дни отвечает через раз (503 / таймаут), поэтому текстовые
# проверки переведены на nemotron-3-super-120b: отвечает за 1-15с и держит JSON-режим.
VISION_MODEL = os.environ.get("NIM_VISION_MODEL", "meta/llama-3.2-11b-vision-instruct")
TEXT_MODEL = os.environ.get("NIM_TEXT_MODEL", "nvidia/nemotron-3-super-120b-a12b")

# Зависшая модель не должна съедать бюджет шага: 3 ретрая по 120с = 6 минут на
# ОДИН кадр, и 20-минутный timeout-minutes выгорает на второй-третьей картинке.
VISION_TIMEOUT = int(os.environ.get("NIM_VISION_TIMEOUT", "45"))
# 23.09.2026 (вечер): следом за текстовой просела и vision — 500 "Inference
# connection error" и read timeout. На перепрогоне REQ-260923-203 из 48 кадров
# 17 остались непрочитанными (14 из них vision), порог 30% пробит, заявка снова
# упала. Двух попыток мало: замер по живому ключу даёт ~17% отказов на запрос.
# Ключевое наблюдение — отказ 500 прилетает за 0.4с, а не по таймауту, поэтому
# лишние попытки почти ничего не стоят: 4 попытки роняют потери с ~9% до ~1%.
VISION_RETRIES = int(os.environ.get("NIM_VISION_RETRIES", "4"))
# ...но защиту от ЗАВИСШЕЙ модели (тот самый сценарий 90B, ради которого попыток
# и было всего две) терять нельзя: 4 попытки × 45с = 3 минуты на один кадр.
# Поэтому ретраи ограничены не только числом, но и общим временем на вызов:
# быстрые 500 успевают отработать все попытки, а таймауты обрываются после
# второго. Худший кадр ограничен сверху и не съедает бюджет шага.
VISION_BUDGET = float(os.environ.get("NIM_VISION_BUDGET", "90"))

# 23.09.2026: наплыв заявок (18 прогонов за 15 минут против обычных 3-5) выбил
# бесплатный ключ NVIDIA в 429 — 60 отказов из 62 пришлись на текстовый шаг
# (policy classify), и гейт слепых вердиктов уронил 18 прогонов в manual_review.
# Vision при этом не отказал НИ РАЗУ: лимит упирается в текстовую модель.
# Старого бэкоффа (3с, 6с) против залпа не хватало — 9 секунд ожидания на кадр,
# после чего кадр молча выбрасывался. Запасной текстовой модели на ключе нет:
# llama-3.3-70b/3.1-70b/3.1-8b отвечают 410 Gone, mistral/qwen — 404, поэтому
# переждать лимит здесь дешевле, чем куда-то переключаться.
TEXT_TIMEOUT = int(os.environ.get("NIM_TEXT_TIMEOUT", "120"))
TEXT_RETRIES = int(os.environ.get("NIM_TEXT_RETRIES", "5"))
RETRY_BASE = float(os.environ.get("NIM_RETRY_BASE", "2"))
RETRY_CAP = float(os.environ.get("NIM_RETRY_CAP", "30"))


def _backoff_delay(attempt: int, resp) -> float:
    """Пауза перед следующей попыткой: Retry-After, иначе экспонента с джиттером."""
    if resp is not None:
        ra = resp.headers.get("Retry-After")
        if ra:
            try:
                return max(0.0, min(float(ra), RETRY_CAP))
            except ValueError:
                pass
    # Джиттер обязателен: кадры прогона летят параллельно, и без него все
    # потоки просыпаются в одну секунду и повторяют тот же залп в тот же лимит.
    delay = min(RETRY_BASE * (2 ** attempt), RETRY_CAP)
    return delay * (0.5 + random.random())


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {os.environ['NIM_API_KEY']}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _post_chat(payload: dict, timeout: int = TEXT_TIMEOUT, retries: int = TEXT_RETRIES,
               budget: float | None = None) -> dict:
    # NVIDIA NIM occasionally times out or returns 429/5xx — retry with backoff
    # instead of letting one flaky call abort the whole submission.
    # budget — потолок общего времени на вызов со всеми ретраями (см. VISION_BUDGET).
    started = time.monotonic()
    last = None
    for attempt in range(retries):
        resp = None
        try:
            r = requests.post(f"{NIM_BASE}/chat/completions", headers=_headers(), json=payload, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500, 502, 503, 504):
                resp = r
                last = RuntimeError(f"NIM {r.status_code}: {r.text[:200]}")
            else:
                raise RuntimeError(f"NIM error {r.status_code}: {r.text[:500]}")
        except requests.exceptions.RequestException as e:
            last = e
        if attempt < retries - 1:
            if budget is not None and time.monotonic() - started >= budget:
                break  # время вышло — дальше ретраить дороже, чем пропустить кадр
            time.sleep(_backoff_delay(attempt, resp))
    raise last if last else RuntimeError("NIM failed")


def vision_describe_frame(frame_path: Path, question: str) -> str:
    """Send a single frame + question to Llama 90B Vision."""
    with open(frame_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    payload = {
        "model": VISION_MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": question},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ],
        }],
        "max_tokens": 800,
        "temperature": 0.2,
    }
    data = _post_chat(payload, timeout=VISION_TIMEOUT, retries=VISION_RETRIES, budget=VISION_BUDGET)
    return data["choices"][0]["message"]["content"].strip()


def text_check(system_prompt: str, user_payload: str) -> dict:
    """Send text-only check to Llama 70B; expects JSON in response."""
    payload = {
        "model": TEXT_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_payload},
        ],
        # Запас на reasoning-модели: у них видимый ответ идёт после длинного
        # внутреннего рассуждения, и на 1500 JSON рискует обрезаться.
        "max_tokens": int(os.environ.get("NIM_TEXT_MAX_TOKENS", "4000")),
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    data = _post_chat(payload, timeout=TEXT_TIMEOUT, retries=TEXT_RETRIES)
    content = data["choices"][0]["message"]["content"].strip()
    return json.loads(content)
