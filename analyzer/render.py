"""Render verdict HTML page using Jinja2 template."""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape


PLATFORM_LABELS = {
    "newsbreak_only": "Только Newsbreak",
    "all_sources": "Все сорсы",
}


def render(submission: dict, verdict: dict, videos: list[dict], lander: dict,
           templates_dir: Path) -> str:
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    tpl = env.get_template("submission.html.j2")
    sub_view = dict(submission)
    sub_view["platform_label"] = PLATFORM_LABELS.get(submission.get("platform", ""), submission.get("platform", ""))
    ts_human = datetime.fromtimestamp(
        submission["created_at"] / 1000, tz=timezone.utc
    ).strftime("%Y-%m-%d %H:%M UTC")
    return tpl.render(sub=sub_view, verdict=verdict, videos=videos, lander=lander, ts_human=ts_human)
