"""Fetch lander text via WebFetch (HTTP + BS4)."""
from __future__ import annotations
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def fetch(url: str) -> dict:
    """Returns {url, host, text, title, ok}."""
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=20, allow_redirects=True)
        r.raise_for_status()
    except Exception as e:
        return {"url": url, "host": urlparse(url).netloc, "text": "", "title": "", "ok": False, "error": str(e)}
    soup = BeautifulSoup(r.text, "html.parser")
    # Drop scripts/styles
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    title = (soup.title.string if soup.title else "") or ""
    text = " ".join(soup.get_text(" ", strip=True).split())
    return {
        "url": r.url,
        "host": urlparse(r.url).netloc,
        "text": text[:12000],
        "title": title.strip(),
        "ok": True,
    }
