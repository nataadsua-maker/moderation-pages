"""Сборка и чистка строки headline для System1 (Keywords оффера в трекере).

Порт `buildHeadline()` из worker/src/clickflare_s1.ts — тот же алгоритм на Python,
чтобы отдать её на суд ИИ-проверки ЕЩЁ ДО одобрения заявки, а не только когда
баер жмёт «Отправить в трекер». Решение Nataliia 29.09.2026: к моменту запуска
всё уже должно быть готово, кнопка — механическое действие, а не вторая проверка.

Два разных вида проблем, по решению Nataliia:
- МУСОР — чистим молча, без вопросов баеру и без реджекта: эмодзи, лишние точки,
  известные мусорные вставки («Compliance Reviewer» — наш старый баг vision),
  манипулятивные фразы-крючки («Oh my god», «I really wish we'd known sooner» —
  партнёрка их зовёт Regret Clickbait), случайные короткие токены между точками
  («.lg lg.» — партнёрка зовёт Stray Tracking Tokens; фильтр только по форме
  токена, не по смыслу — настоящую короткую фразу вроде «take a look» не трогает).
  Фразы-«человеческие комментарии не по адресу» вроде «marthue's card» сюда
  намеренно НЕ входят — грамматически нормальны, отличить их от настоящего
  текста может только смысловая проверка, не regex; пока не покрыто.
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

# Манипулятивные фразы-крючки (партнёрка: «Regret Clickbait & Sensationalism» —
# «I really wish we'd known sooner», «Oh my god», «Honestly, I'm shocked»).
# Стартовый список по их примерам, 29.09.2026 — дополнять по новым находкам.
# Смысловая часть кликбейта (панические формулировки про симптомы/диагнозы) сюда
# НЕ входит — это решает уже существующая смысловая проверка, не список фраз.
_CLICKBAIT_RE = re.compile(
    r"\b(i really wish we'?d known sooner|oh my god|honestly,? i'?m shocked)\b", re.IGNORECASE
)

# Короткие бессмысленные токены между точками (партнёрка: «.lg lg.» — «Stray
# Tracking Tokens: non-advertised backend keywords»). Ловим ТОЛЬКО сегменты, где
# каждое слово ≤3 символов и это не обычное короткое английское слово — настоящая
# короткая фраза («take a look», «50% off», «new») почти всегда мимо этого фильтра
# (хотя бы одно слово длиннее 3 символов или из белого списка). Фразы-«человеческие
# комментарии не по адресу» вроде «marthue's card» сюда НЕ попадают намеренно —
# они грамматически нормальны, отличить их от настоящего текста может только
# смысловая проверка (см. ASSEMBLED HEADLINE в llm_checks.py), не regex.
_SHORT_WORD_WHITELIST = {
    "a", "an", "is", "to", "at", "in", "on", "of", "by", "or", "we", "it", "up", "no", "so",
    "ok", "go", "new", "off", "now", "see", "buy", "get", "try", "top", "for", "and", "the",
    "you", "use", "hot", "win", "fun", "fix", "add", "end", "yes", "why", "how",
}


def _looks_like_stray_token(segment: str) -> bool:
    words = re.findall(r"[a-zA-Z']+", segment.lower())
    if not words or len(words) > 3:
        return False
    return all(len(w) <= 3 and w not in _SHORT_WORD_WHITELIST for w in words)


def strip_stray_tokens(s: str) -> str:
    """Вырезать сегменты между точками, похожие на случайные технические токены."""
    parts = s.split(".")
    kept = [p for p in parts if not _looks_like_stray_token(p)]
    return ".".join(kept)


def clean_syntax(s: str) -> str:
    """Двойные/тройные точки → одна; точка без пробела перед словом → добавляем
    пробел (но не трогаем числа и сокращения вида "3.5" — только буква после точки)."""
    s = re.sub(r"\.{2,}", ".", s)
    s = re.sub(r"\.(?=[A-Za-zА-Яа-я])", ". ", s)
    return s


def _tidy_seams(s: str) -> str:
    """После вырезания куска из середины строки остаётся шов — лишняя точка,
    двойной пробел или точка в самом начале/конце. Подчищаем."""
    s = re.sub(r"\.{2,}", ".", s)
    s = re.sub(r"\.\s*\.", ".", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip(" .")


def strip_gibberish(s: str) -> str:
    s = _GIBBERISH_RE.sub("", s)
    return _tidy_seams(s)


def strip_clickbait(s: str) -> str:
    s = _CLICKBAIT_RE.sub("", s)
    return _tidy_seams(s)


def _lines(s: str) -> list[str]:
    return [x.strip() for x in re.split(r"\r?\n", s or "") if x.strip()]


def _ocr_tokens(s: str) -> set[str]:
    """Нормализация для сверки «тот же текст на другом кадре» — тот же принцип,
    что и в subtitle_filter._normalize (без цифр/пунктуации, нижний регистр)."""
    s = re.sub(r"[^a-zA-Zа-яА-Я0-9\s]", " ", (s or "").lower())
    return set(s.split())


def _is_stable_overlay(idx: int, frames: list[dict], min_overlap: float = 0.7) -> bool:
    """Настоящая плашка держится на экране несколько секунд и попадает минимум
    в 2 из 8 равномерно взятых кадров (video.extract_frames). Находка только на
    ОДНОМ кадре — почти всегда либо галлюцинация vision, либо случайная деталь
    сцены (бейджик, вывеска на фоне), а не текст, который баер специально
    наложил. Решение Nataliia 30.09.2026: такую находку в headline не берём —
    не спрашиваем баера, чинится само по данным, которые уже есть.
    Реальный случай-эталон: REQ-260911-104 «SUPER TRUCKS» — чистая галлюцинация
    на одном кадре, второй раз нигде не встречалась.
    Только для видео — на картинках кадр один, сверять не с чем, там
    распознавание и так надёжное (не трогаем, решение Nataliia)."""
    target = _ocr_tokens(frames[idx].get("ocr_text"))
    if not target:
        return False
    for j, fr in enumerate(frames):
        if j == idx or not fr.get("ocr_text"):
            continue
        other = _ocr_tokens(fr["ocr_text"])
        if not other:
            continue
        overlap = len(target & other) / max(len(target), len(other))
        if overlap >= min_overlap:
            return True
    return False


def build_headline(sub: dict, videos: list[dict]) -> str:
    """Сырая сборка — порт buildHeadline() из clickflare_s1.ts построчно.

    `videos` — общий формат пайплайна moderate.py: каждый элемент имеет
    `frames_analysis` (список {ocr_text, is_subtitle, ...}, по кадрам/картинкам)
    и `transcript` ({"full_text", "segments": [{"start","end","text"}]}).
    Тот же формат отдаёт и videos_from_media_analysis() для архивных копий —
    функция ниже работает на обоих путях без изменений.
    """
    # Первая НЕ-субтитровая плашка, в порядке видео → порядок кадров внутри видео.
    # На видео дополнительно требуем, чтобы та же плашка встретилась ещё хотя бы
    # на одном кадре ЭТОГО ЖЕ видео (см. _is_stable_overlay) — иначе пропускаем
    # кандидата и идём к следующему, а не берём случайную деталь кадра.
    overlay = ""
    for v in videos:
        frames = v.get("frames_analysis") or []
        is_video = v.get("kind") != "image"
        for idx, fr in enumerate(frames):
            if not (fr.get("ocr_text") and not fr.get("is_subtitle")):
                continue
            if is_video and not _is_stable_overlay(idx, frames):
                continue
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
    внутри build_headline, здесь докручиваем синтаксис, кликбейт-фразы, случайные
    короткие токены и мусорные вставки — все молча, без реджекта)."""
    raw = build_headline(sub, videos)
    s = clean_syntax(raw)
    s = strip_clickbait(s)
    s = strip_stray_tokens(s)
    s = strip_gibberish(s)
    return _tidy_seams(s)
