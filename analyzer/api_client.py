"""Client for the Worker API (auth via shared secret)."""
from __future__ import annotations
import os
import requests


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {os.environ['VERDICT_SHARED_SECRET']}"}


def fetch_submission(submission_id: str) -> dict:
    url = f"{os.environ['WORKER_URL']}/api/internal/submission/{submission_id}"
    r = requests.get(url, headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.json()


def post_verdict(submission_id: str, verdict: dict, page_url: str, media_analysis: list | None = None, notify: bool = True) -> None:
    url = f"{os.environ['WORKER_URL']}/api/verdict"
    payload = {
        "submission_id": submission_id,
        "page_url": page_url,
        "verdict": verdict,
        "media_analysis": media_analysis or [],
        "notify": notify,
    }
    r = requests.post(url, headers=_headers(), json=payload, timeout=60)
    r.raise_for_status()
