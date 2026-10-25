"""Модели ИИ из Админки сервиса (вкладка «🤖 Сервисы ИИ», решение Nataliia 20.10.2026).

Одно место на весь сервис: сервер отдаёт цепочки моделей по задачам
(GET /api/internal/ai-models), анализатор идёт по ним до первой ответившей.
  image — описать кадр / прочитать текст с картинки,
  video — плашки по видео целиком (MiMo),
  text  — текстовые проверки со строгим JSON.
Id модели: "<провайдер>:<модель>", провайдеры mimo / gemini / nim / anthropic.
Сервер недоступен — работаем по DEFAULTS (те же, что стоят в Админке по умолчанию).

anthropic — платный резерв (решение Nataliia 25.10.2026), поэтому стоит ПОСЛЕДНИМ
в цепочке «text»: срабатывает только когда MiMo/Gemini/NIM отказали все разом. Для
image/video его нет — там три бесплатных/дешёвых варианта уже достаточно надёжны.
"""
from __future__ import annotations

import os
from pathlib import Path

import requests

import anthropic_client
import gemini
import mimo_video
import nim

DEFAULTS = {
    "image": ["mimo:mimo-v2.6-flash", "gemini:gemini-3.5-flash-lite", "nim:meta/llama-3.2-11b-vision-instruct"],
    "video": ["mimo:mimo-v2.6-flash"],
    "text": ["mimo:mimo-v2.6-flash", "gemini:gemini-3.5-flash-lite", "nim:nvidia/nemotron-3-super-120b-a12b",
              "anthropic:claude-haiku-4-5-20251001"],
}

_chains: dict | None = None


def chains() -> dict:
    """Цепочки из сервера, один раз за прогон."""
    global _chains
    if _chains is not None:
        return _chains
    _chains = dict(DEFAULTS)
    url, secret = os.environ.get("WORKER_URL"), os.environ.get("VERDICT_SHARED_SECRET")
    if url and secret:
        try:
            r = requests.get(f"{url}/api/internal/ai-models",
                             headers={"Authorization": f"Bearer {secret}"}, timeout=20)
            if r.status_code == 200:
                data = r.json()
                for k in DEFAULTS:
                    if isinstance(data.get(k), list) and data[k]:
                        _chains[k] = [str(x) for x in data[k]]
                print(f"  модели ИИ из Админки: картинки {_chains['image'][0]}, видео {_chains['video'][0]}, "
                      f"тексты {_chains['text'][0]}")
            else:
                print(f"  модели ИИ: сервер ответил {r.status_code}, беру по умолчанию")
        except Exception as e:
            print(f"  модели ИИ: сервер недоступен ({type(e).__name__}), беру по умолчанию")
    return _chains


def _split(mid: str) -> tuple[str, str]:
    p, _, m = mid.partition(":")
    return p, m


def describe_image(frame_path: Path, prompt: str) -> tuple[str, str]:
    """Описание кадра/картинки по цепочке «image». Возвращает (текст, модель)."""
    errors = []
    for mid in chains()["image"]:
        p, m = _split(mid)
        try:
            if p == "mimo":
                text = mimo_video.describe_image(frame_path, prompt, model=m)
            elif p == "gemini":
                if not gemini.available():
                    raise RuntimeError("нет GEMINI_API_KEY")
                text = gemini.describe_frame(frame_path, prompt, model=m)
            elif p == "nim":
                text = nim.vision_describe_frame(frame_path, prompt, model=m)
            else:
                raise RuntimeError("неизвестный провайдер")
            if text and text.strip():
                return text.strip(), mid
            errors.append(f"{mid}: пустой ответ")
        except Exception as e:
            errors.append(f"{mid}: {type(e).__name__}: {str(e)[:120]}")
    raise RuntimeError("; ".join(errors) or "нет моделей в цепочке")


def text_json(system_prompt: str, user_payload: str, max_tokens: int = 4000) -> tuple[dict, str]:
    """Текстовая проверка со строгим JSON по цепочке «text». Возвращает (json, модель)."""
    errors = []
    for mid in chains()["text"]:
        p, m = _split(mid)
        try:
            if p == "mimo":
                out = mimo_video.text_json(system_prompt, user_payload, max_tokens, model=m)
            elif p == "gemini":
                if not gemini.available():
                    raise RuntimeError("нет GEMINI_API_KEY")
                out = gemini.text_json(system_prompt, user_payload, max_tokens, model=m)
            elif p == "nim":
                out = nim.text_check(system_prompt, user_payload, model=m)
            elif p == "anthropic":
                out = anthropic_client.text_json(system_prompt, user_payload, max_tokens, model=m)
            else:
                raise RuntimeError("неизвестный провайдер")
            return out, mid
        except Exception as e:
            errors.append(f"{mid}: {type(e).__name__}: {str(e)[:120]}")
    raise RuntimeError("; ".join(errors) or "нет моделей в цепочке")


def video_model() -> str | None:
    """Модель MiMo для плашек по видео целиком (первая mimo в цепочке «video»)."""
    for mid in chains()["video"]:
        p, m = _split(mid)
        if p == "mimo":
            return m
    return None
