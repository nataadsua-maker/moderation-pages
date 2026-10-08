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


def post_media_analysis(submission_id: str, media_analysis: list) -> None:
    """Persist transcripts EARLY (before the slow vision loop) so a later timeout
    still leaves the moderator something to read on the manual-review card."""
    url = f"{os.environ['WORKER_URL']}/api/internal/media-analysis"
    payload = {"submission_id": submission_id, "media_analysis": media_analysis}
    r = requests.post(url, headers=_headers(), json=payload, timeout=30)
    r.raise_for_status()


def post_verdict(submission_id: str, verdict: dict, page_url: str, media_analysis: list | None = None,
                 notify: bool = True, headline: str = "") -> None:
    url = f"{os.environ['WORKER_URL']}/api/verdict"
    payload = {
        "submission_id": submission_id,
        "page_url": page_url,
        "verdict": verdict,
        "media_analysis": media_analysis or [],
        "notify": notify,
    }
    # headline — уже проверенная строка для System1 (см. headline.py). Пустая
    # строка (заявка без крео/видео) — не шлём поле вовсе, воркер оставит
    # старое поведение (посчитает сам при запуске, как раньше).
    if headline:
        # Имя поля — headline_ready, как читает воркер (index.ts /api/verdict).
        # До 08.10.2026 тут стояло "headline", воркер его не видел, и чистая
        # строка ни разу не доходила до формы запуска (аудит Nataliia).
        payload["headline_ready"] = headline
    r = requests.post(url, headers=_headers(), json=payload, timeout=60)
    r.raise_for_status()
