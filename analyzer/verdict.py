"""Assemble final verdict from Layer 1 + Layer 2 + visual results."""
from __future__ import annotations


# Priority tiers (lower number = higher priority, Nataliia's ranking):
#   1 — Ad-to-Page, identity, promises, numbers (semantic L2 + numeric backstop) + manipulation
#   2 — stop-words in creative text (deterministic L1)
#   3 — visual stop-techniques (arrows / before-after / weapons / 18+ …)
# Tiering drives ORDER (важное показываем первым) and visibility, not the reject toggle —
# любое нарушение по-прежнему = reject. manual_review-флаги держим в Tier 1 (самое важное —
# «главное не смогли проверить»).
TIER1 = 1
TIER2 = 2
TIER3 = 3


def assemble(submission: dict, l1_hits: list[dict], l2_result: dict, videos: list[dict],
             numeric_hits: list[dict] | None = None) -> dict:
    """Returns:
    {
      "overall": "approve" | "reject",
      "violations": [{where, quote, reason, policy_section, category, tier}],  # sorted by tier
      "has_critical_18plus": bool,
      "manual_review": bool,
      "stats": {...}
    }
    """
    violations: list[dict] = []
    critical_18plus = False

    # Tier 1 — deterministic numeric Ad-to-Page backstop (money/percent absent from lander)
    for h in numeric_hits or []:
        violations.append({
            "where": h.get("where", ""),
            "title": "Число не подтверждено на лендинге",
            "quote": h.get("quote", ""),
            "reason": h.get("reason", ""),
            "how_to_fix": h.get("how_to_fix", ""),
            "policy_section": h.get("policy_section", "2.1"),
            "category": "standard",
            "tier": TIER1,
        })

    # Layer 1 — stop-words. Tier 2, except prompt-injection (manipulation) which is Tier 1.
    for h in l1_hits:
        if h["severity"] == "error":
            tier = TIER1 if h["section"] == "manipulation" else TIER2
            violations.append({
                "where": h["where"],
                "quote": h["quote"],
                "reason": h["hint"],
                "policy_section": h["section"],
                "category": "standard",
                "tier": tier,
            })
        # severity "warn" — не валит автоматически (числа проверяет numeric_hits + L2;
        # before-after — визуальный слой), идёт во flags

    # Layer 2 LLM — semantic Ad-to-Page / identity / promises / numbers. Tier 1.
    # Ad-to-Page Match is one-way: мы судим КРЕО против ленда, ленд это эталон, а не объект
    # нарушения. Если модель всё же вынесла вердикт «на ленд» (where=Lander / «лендинг не
    # объясняет своё же предложение»), это мусор — мы ленд не модерируем. Режем такие, чтобы
    # баер/модератор их не разгребали руками (как было на REQ-260612-018).
    l2_lander_dropped = 0
    for v in l2_result.get("violations") or []:
        if _is_lander_where(v.get("where", "")):
            l2_lander_dropped += 1
            continue
        violations.append({
            "where": v.get("where", ""),
            "title": v.get("title", ""),
            "quote": v.get("quote", ""),
            "quote_ru": v.get("quote_ru", ""),
            "reason": v.get("reason", ""),
            "how_to_fix": v.get("how_to_fix", ""),
            "policy_section": v.get("policy_section", ""),
            "category": v.get("category") or "standard",
            "tier": TIER1,
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
                    "tier": TIER3,
                })

    # Order by priority tier (Tier 1 first) so the report and the buyer message surface the
    # important violations before minor ones. Stable sort keeps within-tier order.
    violations.sort(key=lambda v: v.get("tier", TIER3))

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
            "numeric_count": len(numeric_hits or []),
            "l2_lander_dropped": l2_lander_dropped,
            "visual_count": sum(len(v.get("frames_analysis", [])) for v in videos),
            "tier1_count": len([v for v in violations if v.get("tier") == TIER1]),
            "tier2_count": len([v for v in violations if v.get("tier") == TIER2]),
            "tier3_count": len([v for v in violations if v.get("tier") == TIER3]),
            "confidence": confidence,
        },
    }


def _is_lander_where(where: str) -> bool:
    """True если нарушение нацелено на сам лендинг (а не на крео).

    Ленд это эталон Ad-to-Page Match, не объект модерации. Нарушение с таким `where`
    невалидно по определению и не должно доходить до баера/модератора.
    """
    w = (where or "").strip().lower()
    if not w:
        return False
    return any(token in w for token in ("lander", "лендинг", "лэндер", "ленд", "целевая страниц"))


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
