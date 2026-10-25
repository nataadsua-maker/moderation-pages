"""Anthropic Claude API — резерв для Layer 2 (llm_checks.check), последнее звено
цепочки «тексты» после MiMo/Gemini/NIM (решение Nataliia 25.10.2026). Платный, без
бесплатного тира — поэтому именно последний, а не первый: срабатывает только когда
все три бесплатных/дешёвых варианта разом отказали.

Контракт тот же, что у nim.text_check/gemini.text_json: на входе системный промпт +
payload, на выходе разобранный JSON (или исключение).

Модель по умолчанию — Haiku 4.5: на реальной заявке с 3 нарушениями (REQ-261019-082,
25.10.2026) нашла 2 из 3 за 8 секунд. Sonnet 5 на том же тесте нашёл то же самое (плюс
размножил одну находку на 3 карточки по видео — safe, воркер это схлопывает
группировкой), но за 26-39 секунд из-за extended thinking по умолчанию — для редкого
аварийного пути это дороже и медленнее не по делу, Haiku хватает с запасом.
"""
from __future__ import annotations
import json
import os
import random
import re
import time

import requests

API_BASE = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
MODEL = os.environ.get("ANTHROPIC_TEXT_MODEL", "claude-haiku-4-5-20251001")

TIMEOUT = int(os.environ.get("ANTHROPIC_TIMEOUT", "60"))
RETRIES = int(os.environ.get("ANTHROPIC_RETRIES", "3"))
RETRY_BASE = float(os.environ.get("ANTHROPIC_RETRY_BASE", "2"))
RETRY_CAP = float(os.environ.get("ANTHROPIC_RETRY_CAP", "20"))


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _backoff_delay(attempt: int, resp) -> float:
    if resp is not None:
        ra = resp.headers.get("retry-after")
        if ra:
            try:
                return max(0.0, min(float(ra), RETRY_CAP))
            except ValueError:
                pass
    return min(RETRY_CAP, RETRY_BASE ** attempt) + random.uniform(0, 1)


def text_json(system_prompt: str, user_payload: str, max_tokens: int = 4000, model: str | None = None) -> dict:
    if not available():
        raise RuntimeError("нет ANTHROPIC_API_KEY")
    payload = {
        "model": model or MODEL,
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_payload}],
    }
    headers = {
        "x-api-key": os.environ["ANTHROPIC_API_KEY"],
        "anthropic-version": API_VERSION,
        "content-type": "application/json",
    }
    last = None
    for attempt in range(RETRIES):
        resp = None
        try:
            r = requests.post(API_BASE, json=payload, headers=headers, timeout=TIMEOUT)
            if r.status_code == 200:
                data = r.json()
                # Модели с extended thinking (напр. Sonnet) кладут рассуждение отдельным
                # content-блоком ПЕРЕД текстом — берём только блоки type="text".
                text_blocks = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
                content = "\n".join(text_blocks).strip()
                if content.startswith("```"):
                    content = re.sub(r"^```[a-zA-Z]*\n?|```$", "", content, flags=re.M).strip()
                return json.loads(content)
            if r.status_code in (429, 500, 502, 503, 529):
                resp = r
                last = RuntimeError(f"Anthropic {r.status_code}: {r.text[:200]}")
            else:
                raise RuntimeError(f"Anthropic error {r.status_code}: {r.text[:300]}")
        except requests.exceptions.RequestException as e:
            last = e
        if attempt < RETRIES - 1:
            time.sleep(_backoff_delay(attempt, resp))
    raise last if last else RuntimeError("Anthropic failed")
