"""NVIDIA NIM client (OpenAI-compatible). Used for both Vision (frames) and Text (lander/textual policy)."""
from __future__ import annotations
import base64
import json
import os
import time
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


def _post_chat(payload: dict, timeout: int = 120, retries: int = 3) -> dict:
    # NVIDIA NIM occasionally times out or returns 429/5xx — retry with backoff
    # instead of letting one flaky call abort the whole submission.
    last = None
    for attempt in range(retries):
        try:
            r = requests.post(f"{NIM_BASE}/chat/completions", headers=_headers(), json=payload, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500, 502, 503, 504):
                last = RuntimeError(f"NIM {r.status_code}: {r.text[:200]}")
            else:
                raise RuntimeError(f"NIM error {r.status_code}: {r.text[:500]}")
        except requests.exceptions.RequestException as e:
            last = e
        if attempt < retries - 1:
            time.sleep(3 * (attempt + 1))  # 3s, 6s backoff
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
