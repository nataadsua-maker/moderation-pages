"""Сборка и чистка строки headline для System1 (Keywords оффера в трекере).

Порт `buildHeadline()` из worker/src/clickflare_s1.ts — тот же алгоритм на Python,
чтобы отдать её на суд ИИ-проверки ЕЩЁ ДО одобрения заявки, а не только когда
баер жмёт «Отправить в трекер». Решение Nataliia 29.09.2026: к моменту запуска
всё уже должно быть готово, кнопка — механическое действие, а не вторая проверка.

Два разных вида проблем, по решению Nataliia:
- МУСОР (эмодзи, лишние точки, случайные вставки типа «Compliance Reviewer») —
  чистим молча, без вопросов баеру и без реджекта.
- СМЫСЛ (утверждает то, чего нет на лендинге; подразумевает скидку/товар) —
  это не список запрещённых слов, а вопрос соответствия лендингу. Через эту
  чистку НЕ проходит — уходит на суд той же ИИ-проверки, что уже судит
  Adtitle/Description (llm_checks.py), под тем же Golden Rule 2.1/2c.

Держать в паре с TS-версией (buildHeadline в clickflare_s1.ts): порядок
склейки частей и региксп эмодзи должны совпадать, иначе проверка судит не то,
что реально уедет в трекер.
"""
from __future__ import annotations
import re


# Тот же диапазон, что EMOJI_RE в worker/src/clickflare.ts (\p{Extended_Pictographic}
# и \p{Emoji_Presentation} в JS не имеют точного эквивалента в Python re без модуля
# `regex`, поэтому берём широкий блок юникод-пиктограмм + модификаторы тона/вариации,
# которые реально встречаются в крео — тот же практический охват).
# ⚠️ Проверено на реальной заявке 29.09.2026: первая версия без блока 2B00-2BFF
# (стрелки/звёзды — ⬇️⭐️⬆️) пропускала ⬇️ живьём. Блок добавлен и перепроверен.
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"  # символы, пиктограммы, эмодзи
    "\U00002600-\U000027BF"  # прочие символы + дингбаты (✅❌⚠️ и т.п.)
    "\U00002B00-\U00002BFF"  # прочие символы и стрелки (⬇️⭐️⬆️➡️ и т.п.)
    "\U0001F1E6-\U0001F1FF"  # региональные буквы (флаги)
    "\U0001F3FB-\U0001F3FF"  # тон кожи
    "‍️⃣"     # ZWJ, variation selector, combining enclosing keycap
    "]+"
)


def strip_emoji(s: str) -> str:
    """Как stripEmoji() в clickflare.ts: вырезать эмодзи, схлопнуть лишние пробелы."""
    return re.sub(r"[ \t]{2,}", " ", _EMOJI_RE.sub("", s or "")).strip()


# Мусорные вставки, которые модель иногда «дочитывает» на пустом кадре или которые
# попадают в крео как технический хвост. Известные случаи из разбора партнёрки
# (Authinsights, 24.09.2026) + наш собственный баг с "Compliance Reviewer" (июнь 2026,
# см. DESCRIBE_PROMPT в visual.py — промпт больше не называет читателя ревьюером,
# это вторая линия защиты на случай, если модель всё равно такое допишет).
_GIBBERISH_RE = re.compile(
    r"\b(compliance reviewer( reviewing the video)?|reviewing the video)\b", re.IGNORECASE
)


def clean_syntax(s: str) -> str:
    """Двойные/тройные точки → одна; точка без пробела перед словом → добавляем
    пробел (но не трогаем числа и сокращения вида "3.5" — только буква после точки)."""
    s = re.sub(r"\.{2,}", ".", s)
    s = re.sub(r"\.(?=[A-Za-zА-Яа-я])", ". ", s)
    return s


def strip_gibberish(s: str) -> str:
    s = _GIBBERISH_RE.sub("", s)
    # Мусорная вставка могла оставить после себя одинокую точку/пробел на стыке.
    s = re.sub(r"\.\s*\.", ".", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" .")
    return s


def _lines(s: str) -> list[str]:
    return [x.strip() for x in re.split(r"\r?\n", s or "") if x.strip()]


def build_headline(sub: dict, videos: list[dict]) -> str:
    """Сырая сборка — порт buildHeadline() из clickflare_s1.ts построчно.

    `videos` — общий формат пайплайна moderate.py: каждый элемент имеет
    `frames_analysis` (список {ocr_text, is_subtitle, ...}, по кадрам/картинкам)
    и `transcript` ({"full_text", "segments": [{"start","end","text"}]}).
    Тот же формат отдаёт и videos_from_media_analysis() для архивных копий —
    функция ниже работает на обоих путях без изменений.
    """
    # Первая НЕ-субтитровая плашка, в порядке видео → порядок кадров внутри видео.
    overlay = ""
    for v in videos:
        for fr in v.get("frames_analysis") or []:
            if fr.get("ocr_text") and not fr.get("is_subtitle"):
                overlay = fr["ocr_text"].strip()
                break
        if overlay:
            break

    # Озвучка ПЕРВОГО видео, у которого вообще есть сегменты (как firstVideo в TS).
    speech = ""
    for v in videos:
        segs = (v.get("transcript") or {}).get("segments") or []
        if segs:
            speech = " ".join((s.get("text") or "").strip() for s in segs if s.get("text"))
            break

    parts = [
        _lines(sub.get("adtitle", ""))[0] if _lines(sub.get("adtitle", "")) else "",
        _lines(sub.get("description", ""))[0] if _lines(sub.get("description", "")) else "",
        overlay,
        speech,
        (sub.get("button_cta") or "").strip(),
    ]
    parts = [strip_emoji(p) for p in parts if p]
    parts = [p for p in parts if p]
    return ".".join(parts).lower()


def build_clean_headline(sub: dict, videos: list[dict]) -> str:
    """То, что реально уйдёт в трекер: сборка + чистка мусора (эмодзи уже вырезаны
    внутри build_headline, здесь докручиваем синтаксис и мусорные вставки)."""
    raw = build_headline(sub, videos)
    return strip_gibberish(clean_syntax(raw))
