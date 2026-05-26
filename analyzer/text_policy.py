"""Layer 1 regex scan. Mirrors worker/src/policy_rules.ts."""
from __future__ import annotations
import re

# Keep in sync with worker/src/policy_rules.ts STOP_WORD_RULES
STOP_WORD_RULES = [
    {"id": "free", "pattern": r"\bfree\b", "section": "2.3", "severity": "error",
     "hint": "'free' запрещено в денежном контексте"},
    {"id": "guaranteed", "pattern": r"\bguarantee(d|s)?\b", "section": "2.3", "severity": "error",
     "hint": "обещание/гарантия запрещены"},
    {"id": "instantly", "pattern": r"\binstantl?y?\b", "section": "2.3", "severity": "error",
     "hint": "обещание мгновенного результата запрещено"},
    {"id": "near_you", "pattern": r"\bnear (you|me)\b", "section": "2.3", "severity": "error",
     "hint": "near you/me — нельзя без локального контента на ленде"},
    {"id": "city_state_placeholder", "pattern": r"\{(city|state)\}", "section": "2.3", "severity": "error",
     "hint": "{city}/{state} dynamic-локация — запрет"},
    {"id": "urgency", "pattern": r"\b(limited time|today only|act now|hurry|don'?t miss)\b",
     "section": "2.3", "severity": "error", "hint": "urgency-формулировки запрещены"},
    {"id": "apply_cta", "pattern": r"\bapply (now|here|today)\b", "section": "2.3, 2.4", "severity": "error",
     "hint": "Apply Now/Here — запрещённый CTA"},
    {"id": "claim_cta", "pattern": r"\bclaim (your|here|now|yours)\b", "section": "2.3, 2.4", "severity": "error",
     "hint": "Claim — запрещённый CTA"},
    {"id": "shop_buy", "pattern": r"\b(shop|buy) (now|here)\b", "section": "2.4", "severity": "error",
     "hint": "Shop/Buy Now — запрещённый CTA"},
    {"id": "click_tap_search_here", "pattern": r"\b(click here|tap (here|below|to)|search here)\b",
     "section": "2.3, 2.4", "severity": "error", "hint": "Click/Tap/Search Here — запрещённый CTA"},
    {"id": "dollar_amount", "pattern": r"\$\d|\d+\s*(dollars|usd|/mo|per month|/yr)",
     "section": "2.3", "severity": "warn",
     "hint": "цифра/сумма — проверим что дословно есть на ленде"},
    {"id": "percent_discount", "pattern": r"\d+\s*%\s*(off|below|down|cheaper|lower|discount|sale|savings)",
     "section": "2.3", "severity": "warn",
     "hint": "% скидки — проверим что дословно есть на ленде"},
    {"id": "up_to_claim", "pattern": r"\bup to \d",
     "section": "2.3", "severity": "warn",
     "hint": "'up to N' — проверим что дословно есть на ленде"},
    {"id": "miracle_cure", "pattern": r"\b(miracle|cure|heal)\b", "section": "5.2", "severity": "error",
     "hint": "медицинские обещания (cure/heal/miracle) запрещены"},
    {"id": "before_after_text", "pattern": r"\bbefore.{0,15}after\b", "section": "2.3, 4.3", "severity": "warn",
     "hint": "before/after — реджект только при split-screen или шок-сравнении"},
]


def scan_text(text: str, where: str) -> list[dict]:
    if not text:
        return []
    hits = []
    for rule in STOP_WORD_RULES:
        for m in re.finditer(rule["pattern"], text, re.IGNORECASE):
            hits.append({
                "where": where,
                "quote": m.group(0),
                "rule_id": rule["id"],
                "section": rule["section"],
                "hint": rule["hint"],
                "severity": rule["severity"],
            })
    return hits
