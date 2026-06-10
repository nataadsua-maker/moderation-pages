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

    # === Prompt injection — попытки манипуляции LLM через тексты крео ===
    {"id": "pi_ignore", "pattern": r"\bignore (previous|all|prior|the) (instructions|prompts|rules|context)",
     "section": "manipulation", "severity": "error", "hint": "попытка манипуляции LLM (prompt injection)"},
    {"id": "pi_disregard", "pattern": r"\bdisregard (the |all )?(above|previous|instructions)",
     "section": "manipulation", "severity": "error", "hint": "попытка манипуляции LLM"},
    {"id": "pi_new_instructions", "pattern": r"\bnew instructions\s*[:.]",
     "section": "manipulation", "severity": "error", "hint": "попытка манипуляции LLM"},
    {"id": "pi_approve_regardless", "pattern": r"\bapprove (this|regardless|always|anyway)",
     "section": "manipulation", "severity": "error", "hint": "попытка манипуляции LLM"},
    {"id": "pi_skip_violations", "pattern": r"\bskip (violations|policy|checks)",
     "section": "manipulation", "severity": "error", "hint": "попытка манипуляции LLM"},
    {"id": "pi_output_json", "pattern": r'\boutput\s*\{|\breturn\s*\{["\']violations',
     "section": "manipulation", "severity": "error", "hint": "попытка инжектить JSON-ответ"},
    {"id": "pi_role", "pattern": r"\b(you are now|act as|pretend to be|system:|assistant:|user:)\b",
     "section": "manipulation", "severity": "error", "hint": "попытка role-play injection"},
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


# === Deterministic numeric Ad-to-Page backstop (Tier 1) ===
# Money/percent amounts are the highest-risk numbers. The LLM checks these contextually,
# but it's flaky — so we also verify deterministically: a money/percent amount in the
# creative whose digits do NOT appear anywhere on the lander is a hard Ad-to-Page break.
MONEY_CLAIM_RE = re.compile(
    r"\$\s?\d[\d,]*(?:\.\d+)?"          # $1,200  $50  $1,200.00
    r"|\b\d[\d,]*\s?%"                  # 50%
    r"|\b\d[\d,]*\s?(?:dollars|usd)\b",  # 1200 dollars / 1200 usd
    re.IGNORECASE,
)


def _num_key(token: str) -> str:
    """Normalize a numeric token to its integer-part digits so creative and lander
    compare apples-to-apples: '$1,200' / '1,200.00' / '1200' all -> '1200'."""
    digits_part = token.split(".")[0]          # drop cents: 1,200.00 -> 1,200
    return re.sub(r"\D", "", digits_part)


def lander_number_set(lander_text: str) -> set[str]:
    """All numbers present on the lander, normalized to integer-part digits."""
    return {_num_key(m) for m in re.findall(r"\d[\d.,]*", lander_text or "")}


def extract_money_claims(text: str) -> list[str]:
    if not text:
        return []
    return [m.group(0).strip() for m in MONEY_CLAIM_RE.finditer(text)]


def check_numeric_claims(sources: list[tuple[str, str]], lander_text: str) -> list[dict]:
    """Tier-1 deterministic backstop. `sources` = [(where, text), ...].
    Returns a violation for each money/percent amount whose digits are absent from the lander.
    Caller must only invoke this when the lander was fetched successfully (else everything
    would falsely fail)."""
    lander_nums = lander_number_set(lander_text)
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for where, text in sources:
        for token in extract_money_claims(text):
            d = _num_key(token)
            if len(d) < 2:  # skip noisy single digits ("$5")
                continue
            if d in lander_nums:
                continue
            key = (where, d)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "where": where,
                "quote": token,
                "reason": (
                    f"Сумма/число «{token}» из крео не найдено на лендинге. "
                    f"По правилу Ad-to-Page любая цифра должна быть дословно на целевой странице."
                ),
                "how_to_fix": "Убери цифру из крео или поставь ровно ту, что есть на лендинге.",
                "policy_section": "2.1",
            })
    return out
