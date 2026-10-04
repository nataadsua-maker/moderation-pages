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
# 30.09.2026: «oh my gosh» (смягчённый вариант «oh my god») проскочил в headline
# живой заявки — список ловил только «god», не смягчённые синонимы. Добавила
# gosh/goodness/lord — тот же приём, что уже встречался в разборе партнёрки
# («oh my lord!» в примере с чехлами для обуви).
# 30.09.2026 вечером: сверила с полным списком категорий партнёрки (колонка
# Sensationalism/Fear & Clickbait) — добавила два незакрытых паттерна:
# Cliffhanger clickbait («you won't believe / never believe me», 5 находок) и
# Conversational urgency hook («okay, i just found out...», 4 находки).
# «Never believe me» без уточнения НЕ берём отдельным паттерном — это ломало
# легитимный рассказ от первого лица («people never believe me when i tell
# them there's a whole house» — ровно кейс из теста этого файла, его нельзя
# трогать). Берём только явное обращение ко второму лицу — «you won't/never
# believe» — это однозначно крючок, а не рассказ.
# Ещё две категории партнёрки намеренно НЕ сюда: «Emotional hook ('dreaded
# paying')» — единственный пример, нет общей формулы, рискованно обобщать;
# «Personalized diagnostic alarmism» (мед-паника по симптомам) — это не фраза-
# мусор, а содержательная проблема, её ловит смысловая проверка, не эта чистка.
#
# Смысловая часть кликбейта (панические формулировки про симптомы/диагнозы) сюда
# НЕ входит — это решает уже существующая смысловая проверка, не список фраз.
# 04.10.2026 (решение Nataliia): раньше фраза вырезалась одна, и оставался мусор —
# висящий знак («oh my gosh! cheap flights» → «! cheap flights») и обрывок
# предложения («you won't believe these prices» → «these prices»). Теперь у
# каждого крючка своя замена: междометия и законченные фразы вырезаем вместе со
# знаками за ними, а там, где за крючком идёт продолжение мысли, ставим
# нейтральную информационную подводку (see / learn / take a look at — тот же ряд,
# что разрешён для кнопки; кликовых Click/Tap/Apply здесь быть не может).
# Если после замены от предложения осталось меньше 2 слов — убираем предложение
# целиком. Гладкую переформулировку сверху даёт ИИ-проверка (headline_rewrite в
# llm_checks.py), эта чистка — гарантированный запасной вариант.
# Добавлены «i wish we'd known sooner» без «really» и «i was shocked» (нашла
# Nataliia 04.10.2026). Тогда же Nataliia решила заменять и «people never believe
# me», и «dreaded paying», хотя выше они были исключены. Рассказ от первого лица
# не ломаем: вырезаем только вступление «people never believe me when i tell
# them», то, что человек рассказывает дальше, остаётся («… there's a whole house»
# → «there's a whole house»). «dreaded paying» → «paying»: эмоция уходит, факт
# (за что платят) остаётся.
_HOOK_TAIL = r"[\s,!?.]*"
_CLICKBAIT_RULES: list[tuple[re.Pattern, str]] = [
    # Междометия — просто вырезать вместе с запятой/восклицанием после.
    (re.compile(r"\boh my (?:god|gosh|goodness|lord)\b" + _HOOK_TAIL, re.I), ""),
    # Сожаление — законченная фраза, хвоста нет.
    (re.compile(r"\bi (?:really )?wish (?:we|i)'?d known (?:about (?:this|it) |this |it )?sooner\b" + _HOOK_TAIL, re.I), ""),
    # Шок с продолжением «by/at …» → «take a look at …».
    (re.compile(r"\b(?:honestly,?\s*)?i(?:'?m| am| was) (?:so |really |honestly )?shocked (?:by|at)\s+", re.I), "take a look at "),
    # Шок без продолжения (или с «that …») — вырезать, мысль после «that» остаётся.
    (re.compile(r"\b(?:honestly,?\s*)?i(?:'?m| am| was) (?:so |really |honestly )?shocked\b(?:\s+that\b)?" + _HOOK_TAIL, re.I), ""),
    # «you won't believe …» → «see …».
    (re.compile(r"\byou(?:'ll)? (?:won'?t|never) believe\s+(?!me\b)(?=[a-z0-9$])", re.I), "see "),
    (re.compile(r"\byou(?:'ll)? (?:won'?t|never) believe(?: me)?\b" + _HOOK_TAIL, re.I), ""),
    # «people never believe me when i tell them (that) …» → то, что дальше.
    (re.compile(r"\b(?:people|nobody|no one) (?:never |don'?t |doesn'?t )?believes? me\s+when i tell (?:them|people|anyone)(?: that)?\s+", re.I), ""),
    (re.compile(r"\b(?:people|nobody|no one) (?:never |don'?t |doesn'?t )?believes? me\b" + _HOOK_TAIL, re.I), ""),
    # «we dreaded paying for repairs» → «paying for repairs».
    (re.compile(r"\b(?:(?:i|we|you|they)(?: all)?(?: always| used to)? )?dread(?:ed)? paying\b", re.I), "paying"),
    # «okay, i just found out that …» → мысль после «that» без подводки;
    # «… about / что угодно ещё» → «learn …».
    (re.compile(r"\bokay,? i (?:just )?(?:found out|realized|discovered) that\s+", re.I), ""),
    (re.compile(r"\bokay,? i (?:just )?(?:found out|realized|discovered)\s+(?=[a-z0-9$])", re.I), "learn "),
    (re.compile(r"\bokay,? i (?:just )?(?:found out|realized|discovered)\b" + _HOOK_TAIL, re.I), ""),
]

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


def _strip_clickbait_sentence(sent: str) -> str:
    """Один кусок между . ! ? — применяем правила; если крючок был, а от
    предложения осталось меньше 2 слов, кусок выбрасываем целиком."""
    out = sent
    for rx, repl in _CLICKBAIT_RULES:
        out = rx.sub(repl, out)
    if out == sent:
        return sent
    out = re.sub(r"^[\s,]+|[\s,]+$", "", out)
    out = re.sub(r"\s{2,}", " ", out)
    if len(re.findall(r"[a-z0-9$%']+", out, re.I)) < 2:
        return ""
    return (" " if sent[:1].isspace() else "") + out


def strip_clickbait(s: str) -> str:
    # Режем по концам предложений, знаки сохраняем отдельными элементами.
    pieces = re.split(r"([.!?]+)", s)
    kept: list[str] = []
    for i in range(0, len(pieces), 2):
        sent = pieces[i]
        punct = pieces[i + 1] if i + 1 < len(pieces) else ""
        cleaned = _strip_clickbait_sentence(sent)
        if cleaned.strip():
            kept.append(cleaned + punct)
        elif not sent.strip() and punct and kept:
            kept.append(punct)  # пустой кусок без крючка («..») — шов, подчистит _tidy_seams
    return _tidy_seams("".join(kept))


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
            # mimo_verified — эта запись пришла из видео-нативного разбора MiMo
            # (moderate.py), а не из по-кадрового OCR: там нет дрейфа между
            # кадрами, который проверяет _is_stable_overlay, проверять нечего.
            if is_video and not fr.get("mimo_verified") and not _is_stable_overlay(idx, frames):
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


# Подводки, которые ИИ-версия может добавить сверх исходной строки (те же, что
# ставит механическая чистка выше). Любое другое новое слово = ИИ что-то
# придумал, такой вариант не берём.
_REWRITE_ALLOWED_NEW = {"see", "learn", "take", "a", "look", "at", "about", "the", "this"}


def _words(s: str) -> list[str]:
    return re.findall(r"[a-z0-9$%']+", (s or "").lower())


def accept_rewrite(clean: str, rewrite) -> str:
    """Предохранитель для headline_rewrite от ИИ-проверки (llm_checks.py).
    Решение Nataliia 04.10.2026: берём гладкую версию ИИ, только если она не
    добавила своих слов (кроме подводок see/learn/take a look at), не потеряла
    больше 40% текста и в ней не осталось крючков. Иначе — механическая чистка."""
    if not clean or not isinstance(rewrite, str) or not rewrite.strip():
        return clean
    r = _tidy_seams(clean_syntax(strip_emoji(rewrite.lower())))
    if strip_clickbait(r) != r:
        return clean
    src, new = _words(clean), _words(r)
    if set(new) - set(src) - _REWRITE_ALLOWED_NEW:
        return clean
    if len(new) < 0.6 * len(src) or len(new) > len(src) + 5:
        return clean
    return r
