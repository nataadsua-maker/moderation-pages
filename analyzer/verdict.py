"""Assemble final verdict from Layer 1 + Layer 2 + visual results."""
from __future__ import annotations


def assemble(submission: dict, l1_hits: list[dict], l2_result: dict, videos: list[dict]) -> dict:
    """Returns:
    {
      "overall": "approve" | "reject",
      "violations": [{where, quote, reason, policy_section, category}],
      "has_critical_18plus": bool,
      "manual_review": bool,
      "stats": {...}
    }
    """
    violations: list[dict] = []
    critical_18plus = False

    # Layer 1
    for h in l1_hits:
        if h["severity"] == "error":
            violations.append({
                "where": h["where"],
                "quote": h["quote"],
                "reason": h["hint"],
                "policy_section": h["section"],
                "category": "standard",
            })
        # severity "warn" — не валит автоматически (числа/before-after), идёт во flags

    # Layer 2 LLM
    for v in l2_result.get("violations") or []:
        violations.append({
            "where": v.get("where", ""),
            "title": v.get("title", ""),
            "quote": v.get("quote", ""),
            "quote_ru": v.get("quote_ru", ""),
            "reason": v.get("reason", ""),
            "how_to_fix": v.get("how_to_fix", ""),
            "policy_section": v.get("policy_section", ""),
            "category": v.get("category") or "standard",
        })

    # Visual per-frame
    for v_idx, video in enumerate(videos):
        kind = video.get("kind", "video")
        label = "Картинка" if kind == "image" else "Видео"
        for fr in video.get("frames_analysis", []):
            for viol in fr.get("visual_violations", []):
                t = viol.get("type", "")
                detail = viol.get("detail", "")
                # Vision model is unreliable on fake_ui — it keeps flagging normal text
                # плашки as "имитация кнопки" (false positives). Textual CTA check already
                # catches real "Click/Tap/Search here" cases, so skip visual fake_ui.
                if t == "fake_ui":
                    continue
                is_18plus = t == "adult_18plus"
                if is_18plus:
                    critical_18plus = True
                where = f"{label} {v_idx+1}" if kind == "image" else f"{label} {v_idx+1}, {fr['ts']} кадр"
                violations.append({
                    "where": where,
                    "quote": detail or t,
                    "reason": _visual_reason(t),
                    "policy_section": "4.3" if t in ("adult_18plus", "weapons", "gambling", "politics", "drugs") else "2.3",
                    "category": "critical_18plus" if is_18plus else "standard",
                })

    confidence = l2_result.get("confidence", 1.0) or 1.0
    manual_review = confidence < 0.7 and not violations  # only when no decisive hits

    overall = "reject" if violations else "approve"

    return {
        "overall": overall,
        "violations": violations,
        "summary": (l2_result.get("summary") or "") if violations else "",
        "has_critical_18plus": critical_18plus,
        "manual_review": manual_review,
        "stats": {
            "layer1_count": len([h for h in l1_hits if h["severity"] == "error"]),
            "layer2_count": len(l2_result.get("violations") or []),
            "visual_count": sum(len(v.get("frames_analysis", [])) for v in videos),
            "confidence": confidence,
        },
    }


def _visual_reason(t: str) -> str:
    return {
        "arrow_or_circle": "стрелка/кружок указывает куда кликнуть",
        "fake_ui": "имитация UI кнопок / поиска / выдачи",
        "before_after": "split-screen «до|после» или шок-контраст",
        "shock_content": "shock-content (травмы / экстремальные эмоции)",
        "fake_local": "fake-локальный контент без подтверждения на ленде",
        "weapons": "оружие в кадре",
        "gambling": "gambling / casino визуал",
        "politics": "политическая символика",
        "drugs": "наркотики",
        "adult_18plus": "ADULT / 18+ контент (4.3, abs. prohibition)",
    }.get(t, t)
