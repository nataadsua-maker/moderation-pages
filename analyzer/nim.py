"""NVIDIA NIM client (OpenAI-compatible). Used for both Vision (frames) and Text (lander/textual policy)."""
from __future__ import annotations
import base64
import json
import os
from pathlib import Path

import requests

NIM_BASE = "https://integrate.api.nvidia.com/v1"
VISION_MODEL = "meta/llama-3.2-90b-vision-instruct"
TEXT_MODEL = "meta/llama-3.3-70b-instruct"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {os.environ['NIM_API_KEY']}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _post_chat(payload: dict, timeout: int = 120) -> dict:
    r = requests.post(f"{NIM_BASE}/chat/completions", headers=_headers(), json=payload, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"NIM error {r.status_code}: {r.text[:500]}")
    return r.json()


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
    data = _post_chat(payload)
    return data["choices"][0]["message"]["content"].strip()


def text_check(system_prompt: str, user_payload: str) -> dict:
    """Send text-only check to Llama 70B; expects JSON in response."""
    payload = {
        "model": TEXT_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_payload},
        ],
        "max_tokens": 1500,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    data = _post_chat(payload)
    content = data["choices"][0]["message"]["content"].strip()
    return json.loads(content)
