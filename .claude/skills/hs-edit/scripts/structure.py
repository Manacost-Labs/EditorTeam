#!/usr/bin/env python3
"""Проверка структуры гайда по архетипу.

    python3 structure.py черновик.md
    python3 structure.py черновик.md --profile constructed-guide --deep
    python3 structure.py --outline constructed-guide

Состав разделов снят с корпуса «гайды/» — 49 опубликованных гайдов.
Порядок и обязательность не выдуманы, рядом с каждым блоком стоит частота.

Скрипт ничего не переставляет: он показывает, чего не хватает и что стоит
не на своём месте. Порядок разделов — авторское решение.

Глубокая проверка (`analyze(deep=True)`) смотрит дальше присутствия
заголовков: тело раздела резолвится мимо оглавления, считаются порядок,
тонкие разделы, полотна, зачин и охват классов исходника. Она нужна
переплавке, где структура строится заново по скелету профиля.
"""

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

# В релизе PyYAML лежит рядом со скриптами (см. clarity.py).
_VENDOR = Path(__file__).resolve().parents[1] / "vendor"
if _VENDOR.exists() and str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))
try:
    import yaml  # noqa: E402
except ImportError:  # pragma: no cover - без yaml работает только старый путь по BLOCKS
    yaml = None

PROFILES_DIR = C.ROOT / "config" / "profiles"

# имена блоков -> id профиля: старый путь по BLOCKS и новый по YAML говорят на одном языке
BLOCK_IDS = {
    "Сборки": "builds",
    "Декбилдинг": "deckbuilding",
    "Муллиган": "mulligan",
    "Стратегия": "strategy",
    "Матч-апы": "matchups",
    "Заключение": "conclusion",
}

DEFAULT_SECTION_MIN_WORDS = 60   # в корпусе короче только 4% тел разделов
WALL_SENTENCES = 8               # абзац длиннее — полотно (норма автора 2,2 предложения)
TOC_MIN_LINES = 3

# блок -> (варианты названия, доля гайдов, обязателен ли)
BLOCKS = [
    ("Сборки",     ["сборки архетипа", "сборки", "топовые сборки", "списки колод"], 98, True),
    ("Декбилдинг", ["вопросы декбилдинга", "как собрать колоду", "основа колоды",
                    "замены", "опциональные и технические карты"], 98, True),
    ("Муллиган",   ["муллиган", "общие правила муллигана",
                    "муллиган против каждого класса"], 100, True),
    ("Стратегия",  ["стратегия игры", "основы геймплея", "как работает колода",
                    "тонкости геймплея", "способы победы", "полезные советы"], 96, True),
    ("Матч-апы",   ["матч-апы", "матчапы", "матч-апы колоды"], 100, True),
    ("Заключение", ["заключение"], 27, False),
]

# Составные имена идут первыми и «съедают» свои вхождения: иначе «Охотник на
# демонов» засчитывается как обычный Охотник, а сам он теряется. Для каждого
# класса — образец, допускающий падежные окончания.
CLASSES = [
    "Охотник на демонов",
    "Рыцарь смерти",
    "Воин", "Шаман", "Разбойник", "Паладин", "Охотник",
    "Друид", "Чернокнижник", "Маг", "Жрец",
]

# окончание первого слова свободное: «Охотника на демонов», «Рыцарю смерти»
CLASS_PATTERNS = {
    "Охотник на демонов": r"Охотник\w*\s+на\s+демонов",
    "Рыцарь смерти": r"Рыцар\w+\s+смерти",
    "Воин": r"Воин\w*",
    "Шаман": r"Шаман\w*",
    "Разбойник": r"Разбойник\w*",
    "Паладин": r"Паладин\w*",
    "Охотник": r"Охотник\w*",
    "Друид": r"Друид\w*",
    "Чернокнижник": r"Чернокнижник\w*",
    "Маг": r"Маг\w*",
    "Жрец": r"(?:Жрец|Жреца|Жрецу|Жрецом|Жреце|Жрецы|Жрецов|Жрецам|Жрецами)",
}


def headings(text):
    """Заголовки: markdown-решётки и короткие самостоятельные строки."""
    out = []
    for i, raw in enumerate(text.split("\n")):
        l = raw.strip()
        if not l:
            continue
        if l.startswith("#"):
            out.append((i, l.lstrip("# ").strip()))
        elif (3 <= len(l) <= 40 and 1 <= len(l.split()) <= 5
              and l[0].isupper() and not l.endswith((".", "!", "?", ",", ":"))):
            out.append((i, l))
    return out


def find_blocks(heads):
    found = {}
    for i, h in heads:
        low = h.lower().strip()
        for name, variants, _, _ in BLOCKS:
            if low in variants and name not in found:
                found[name] = (i, h)
    return found


def check_matchups(text, heads, found):
    """Матч-апы должны покрывать классы. Берём хвост текста от заголовка раздела."""
    if "Матч-апы" not in found:
        return None
    lines = text.split("\n")
    start = found["Матч-апы"][0]
    tail = "\n".join(lines[start:])
    if len(tail.split()) < 120:                 # заголовок нашёлся только в оглавлении
        tail = text
    seen, missing = [], []
    rest = tail
    for c in CLASSES:                       # составные первыми — см. комментарий у CLASSES
        pat = CLASS_PATTERNS[c]
        if re.search(rf"\b{pat}\b", rest, re.I):
            seen.append(c)
            # вычёркиваем найденное, чтобы «Охотник на демонов» не дал ещё и «Охотник»
            rest = re.sub(rf"\b{pat}\b", " ", rest, flags=re.I)
        else:
            missing.append(c)
    return seen, missing


# ── Профиль как данные ────────────────────────────────────────────────────


def _blocks_as_sections():
    out = []
    for name, variants, share, required in BLOCKS:
        out.append({
            "id": BLOCK_IDS.get(name, name.lower()),
            "title": name,
            "variants": [v.lower() for v in variants],
            "required": required,
            "purpose": "",
            "min_words": None,
            "corpus_share": share,
        })
    return out


@lru_cache(maxsize=None)
def _profile_yaml(profile_id):
    path = PROFILES_DIR / f"{profile_id}.yaml"
    if yaml is None or not path.exists():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None


def _normalize_section(raw, required):
    title = raw.get("title", raw["id"])
    variants = [v.lower() for v in raw.get("variants", [])] or [title.lower()]
    return {
        "id": raw["id"],
        "title": title,
        "variants": variants,
        "required": bool(raw.get("required", required)),
        "purpose": str(raw.get("purpose", "") or ""),
        "min_words": int(raw["min_words"]) if raw.get("min_words") is not None else None,
        "corpus_share": raw.get("corpus_share"),
    }


def profile_data(profile):
    """Профиль как словарь: разделы, зачин, подпись, классы.

    profile — id из config/profiles, готовый словарь (например skeleton из
    editorteam.profiles) или None. Без YAML работает старый путь по BLOCKS.
    """
    if isinstance(profile, dict):
        raw_sections = profile.get("sections") or []
        if isinstance(raw_sections, dict):          # сырой YAML: {required: [...], optional: [...]}
            sections = [_normalize_section(s, True) for s in raw_sections.get("required") or []]
            sections += [_normalize_section(s, False) for s in raw_sections.get("optional") or []]
        else:                                        # skeleton: список с полем required
            sections = [_normalize_section(s, s.get("required", True)) for s in raw_sections]
        return {
            "id": profile.get("id", "custom"),
            "sections": sections,
            "opening": profile.get("opening") or {},
            "closing": profile.get("closing") or {},
            "require_classes": bool((profile.get("matchups") or {}).get("require_classes",
                                    profile.get("require_classes", False))),
            "min_words": int(profile.get("min_words", 0) or 0),
            "form": profile.get("form") or {},
        }
    data = _profile_yaml(profile) if profile else None
    if data is None:
        return {
            "id": profile or "constructed-guide",
            "sections": _blocks_as_sections(),
            "opening": {},
            "closing": {},
            "require_classes": True,
            "min_words": 600,
            "form": {},
        }
    return profile_data(data)


def load_profile_sections(profile):
    """Разделы профиля в его порядке; fallback — BLOCKS."""
    return profile_data(profile)["sections"]


def outline(profile):
    """Скелет жанра для промпта и плана: разделы с назначением и порядком."""
    data = profile_data(profile)
    sections = []
    for order, s in enumerate(data["sections"], 1):
        sections.append({
            "id": s["id"],
            "title": s["title"],
            "purpose": s["purpose"],
            "min_words": s["min_words"],
            "order": order,
            "required": s["required"],
        })
    return {
        "profile": data["id"],
        "sections": sections,
        "opening": data["opening"],
        "closing": data["closing"],
        "require_classes": data["require_classes"],
        "min_words": data["min_words"],
    }


# ── Тела разделов ─────────────────────────────────────────────────────────


@dataclass
class Body:
    id: str
    title: str
    heading_line: int
    start_line: int
    end_line: int
    words: int
    paragraphs: int
    toc_only: bool


def _section_of(heading_text, sections):
    low = heading_text.lower().strip().rstrip(":")
    for s in sections:
        if low in s["variants"]:
            return s["id"]
    return None


def _toc_like(line):
    l = line.strip()
    if not l:
        return False
    if l.startswith("-") or l.startswith("•"):
        return len(l) <= 60
    if l.startswith("#"):
        return True
    return (3 <= len(l) <= 40 and 1 <= len(l.split()) <= 5
            and l[0].isupper() and not l.endswith((".", "!", "?", ",", ":")))


def toc_span(text, sections=None):
    """Оглавление: подряд идущие короткие строки-названия без прозы между ними.

    В PDF-корпусе раздел сначала перечислен в «Разделы гайда», и заголовок
    находится там, а не у тела. Без этого «тело раздела» — пустое оглавление.
    Возвращает (первая_строка, последняя_строка) или None.
    """
    lines = text.split("\n")
    run_start, run_len, best = None, 0, None
    for i, raw in enumerate(lines + [""]):
        if _toc_like(raw):
            if run_start is None:
                run_start = i
            run_len += 1
            continue
        if raw.strip() == "" and run_start is not None:
            continue                                  # пустая строка не рвёт оглавление
        if run_start is not None and run_len >= TOC_MIN_LINES:
            end = i - 1
            while end > run_start and not lines[end].strip():
                end -= 1
            # Настоящий заголовок первого раздела часто стоит сразу за оглавлением
            # и повторяет его строку. Повтор в хвосте — уже тело, а не оглавление.
            def _key(j):
                return lines[j].lstrip("#-• ").strip().lower().rstrip(":")
            while end > run_start:
                if not lines[end].strip():
                    end -= 1
                    continue
                earlier = {_key(j) for j in range(run_start, end) if lines[j].strip()}
                if _key(end) in earlier:
                    end -= 1
                    continue
                break
            if sections:
                hits = sum(1 for j in range(run_start, end + 1)
                           if _section_of(lines[j].lstrip("#-• ").strip(), sections))
                ok = hits >= 2
            else:
                ok = True
            if ok and (best is None or (end - run_start) > (best[1] - best[0])):
                best = (run_start, end)
        run_start, run_len = None, 0
    return best


def _paragraph_count(lines):
    blocks = 0
    for raw in lines:
        if len(raw.split()) >= 8:
            blocks += 1
    return blocks


def resolve_sections(text, sections):
    """Заголовок раздела вне оглавления с самым большим телом.

    Тело — строки до следующего опознанного заголовка. Если раздел встречается
    только в оглавлении, возвращается Body с toc_only=True и нулём слов.
    """
    lines = text.split("\n")
    toc = toc_span(text, sections)
    heads = headings(text)
    tagged = []
    for line_no, title in heads:
        sid = _section_of(title, sections)
        if sid:
            in_toc = toc is not None and toc[0] <= line_no <= toc[1]
            tagged.append((line_no, sid, title, in_toc))
    real = [t for t in tagged if not t[3]]
    real_lines = [t[0] for t in real]
    candidates = {}
    for idx, (line_no, sid, title, _) in enumerate(real):
        end = real_lines[idx + 1] - 1 if idx + 1 < len(real_lines) else len(lines) - 1
        body_lines = lines[line_no + 1:end + 1]
        words = sum(len(l.split()) for l in body_lines)
        body = Body(sid, title, line_no + 1, line_no + 2, end + 1, words,
                    _paragraph_count(body_lines), False)
        if sid not in candidates or words > candidates[sid].words:
            candidates[sid] = body
    for line_no, sid, title, in_toc in tagged:
        if in_toc and sid not in candidates:
            candidates[sid] = Body(sid, title, line_no + 1, line_no + 1, line_no + 1, 0, 0, True)
    return candidates


def check_order(bodies, sections):
    """Разделы, найденные в тексте, против порядка профиля."""
    expected = [s["id"] for s in sections if s["id"] in bodies]
    actual = sorted(expected, key=lambda sid: bodies[sid].heading_line)
    misplaced = [sid for sid, want in zip(actual, expected) if sid != want]
    return {"expected": expected, "actual": actual, "ok": actual == expected,
            "misplaced": misplaced}


def check_thin(bodies, sections, default_min_words=DEFAULT_SECTION_MIN_WORDS, min_paragraphs=1):
    """Разделы с телом короче нормы профиля."""
    out = []
    by_id = {s["id"]: s for s in sections}
    for sid, body in bodies.items():
        if body.toc_only:
            continue
        s = by_id.get(sid, {})
        limit = s.get("min_words")
        if limit is None:
            if not s.get("required", False):
                continue                          # «Заключение» у автора — две фразы, это норма
            limit = default_min_words
        if body.words < limit or body.paragraphs < min_paragraphs:
            out.append({"id": sid, "title": body.title, "words": body.words,
                        "min_words": limit, "paragraphs": body.paragraphs,
                        "line": body.heading_line})
    return out


OPENING_RE = re.compile(r"Геро[йи]\s+гайда\s+[—–-]\s+", re.I)
_TOKEN = re.compile(r"[А-Яа-яЁёA-Za-z'’-]{2,}")


def phrase_present(phrase, text):
    """Фраза есть в тексте с точностью до падежей: «Бомб Воин» ≈ «Бомб Воина».

    Морфология берётся из common; без неё — сравнение строчных префиксов.
    """
    parts = _TOKEN.findall(phrase.lower())
    if not parts:
        return False
    toks = _TOKEN.findall(text.lower())
    n = len(parts)
    try:
        # пересечение наборов лемм, а не первая по алфавиту: «Галакронда»
        # морфология разбирает и как женское имя, и первый разбор врёт
        targets = [C.lemmas(p) for p in parts]
        tok_lemmas = [C.lemmas(t) for t in toks]
        for i in range(len(toks) - n + 1):
            if all(tok_lemmas[i + k] & targets[k] for k in range(n)):
                return True
        return False
    except Exception:  # noqa: BLE001 — нет морфологии: грубое сравнение
        stems = [p[:max(4, len(p) - 2)] for p in parts]
        for i in range(len(toks) - n + 1):
            if all(toks[i + k].startswith(stems[k]) for k in range(n)):
                return True
        return False


def first_paragraph(text):
    """Первый содержательный абзац: с него начинается авторская интонация."""
    for p in C.paragraphs(text, min_words=25):
        if p.lstrip().startswith("#"):
            continue
        return p
    return " ".join(text.split()[:80])


def check_opening(text, *, archetype=None, expansion=None):
    """Зачин: архетип и дополнение названы сразу, формула «Герой гайда — …»."""
    para = first_paragraph(text)
    out = {"paragraph": para[:200], "formula": bool(OPENING_RE.search(para))}
    out["archetype"] = phrase_present(archetype, para) if archetype else None
    out["expansion"] = phrase_present(expansion, para) if expansion else None
    return out


def walls(text, limit=WALL_SENTENCES):
    """Абзацы-полотна: больше limit предложений без шва."""
    out = []
    for i, p in enumerate(C.paragraphs(text, min_words=25), 1):
        n = len(C.sentences(p))
        if n > limit:
            out.append({"paragraph": i, "sentences": n, "head": " ".join(p.split()[:8])})
    return out


def _finding(fid, severity, message, *, confidence=0.7, evidence="", suggestion="",
             line=None, meta=None):
    out = {"id": fid, "analyzer": "structure", "category": "structure",
           "severity": severity, "confidence": confidence, "message": message}
    if evidence:
        out["evidence"] = evidence
    if suggestion:
        out["suggestion"] = suggestion
    if line is not None:
        out["line"] = line
    if meta:
        out["meta"] = meta
    return out


def analyze(text, profile, *, archetype=None, expansion=None, expected_classes=None,
            deep=False):
    """Структура против профиля. deep=False даёт только отсутствие разделов."""
    data = profile_data(profile)
    sections = data["sections"]
    bodies = resolve_sections(text, sections)
    findings = []
    required = [s for s in sections if s["required"]]
    missing = [s for s in required if s["id"] not in bodies]
    for s in missing:
        findings.append(_finding(
            f"structure.missing.{s['id']}", "likely",
            f"нет обязательного раздела «{s['title']}»",
            suggestion="; ".join(s["variants"][:3]),
            meta={"corpus_share": s.get("corpus_share")} if s.get("corpus_share") else None,
        ))
    metrics = {
        "required": len(required),
        "present": len(required) - len(missing),
        "missing": [s["id"] for s in missing],
        "sections": {sid: b.words for sid, b in bodies.items()},
        "toc_detected": toc_span(text, sections) is not None,
    }
    if not deep:
        return findings, metrics

    toc_only = [sid for sid, b in bodies.items() if b.toc_only]
    for sid in toc_only:
        findings.append(_finding(
            f"structure.toc-only.{sid}", "info",
            f"раздел «{bodies[sid].title}» есть только в оглавлении",
            line=bodies[sid].heading_line, confidence=0.6,
        ))
    order = check_order(bodies, sections)
    if not order["ok"]:
        titles = {s["id"]: s["title"] for s in sections}
        findings.append(_finding(
            "structure.order", "review",
            "порядок разделов отличается от скелета жанра",
            evidence=" → ".join(titles.get(s, s) for s in order["actual"]),
            suggestion=" → ".join(titles.get(s, s) for s in order["expected"]),
            confidence=0.75,
        ))
    thin = check_thin(bodies, sections)
    for t in thin:
        findings.append(_finding(
            f"structure.thin.{t['id']}", "review",
            f"раздел «{t['title']}» слишком короткий: {t['words']} слов при норме {t['min_words']}",
            line=t["line"], confidence=0.7,
            suggestion="раскрыть раздел материалом исходника или честно объявить, что материала нет",
        ))
    wall_list = walls(text)
    for w in wall_list:
        findings.append(_finding(
            "structure.wall", "review",
            f"абзац {w['paragraph']} — полотно из {w['sentences']} предложений",
            evidence=w["head"] + "…", confidence=0.7,
            suggestion="разделить по смысловому шву: тезис / объяснение / пример / вывод",
        ))
    opening = check_opening(text, archetype=archetype, expansion=expansion)
    if archetype and opening["archetype"] is False:
        findings.append(_finding(
            "structure.opening.archetype", "likely",
            f"в первом абзаце не назван архетип «{archetype}»",
            evidence=opening["paragraph"][:120], confidence=0.75,
        ))
    if expansion and opening["expansion"] is False:
        findings.append(_finding(
            "structure.opening.expansion", "likely",
            f"в первом абзаце не названо дополнение «{expansion}»",
            evidence=opening["paragraph"][:120], confidence=0.7,
        ))
    if data["opening"].get("formula") and not opening["formula"]:
        findings.append(_finding(
            "structure.opening.formula", "info",
            "зачин без формулы «Герой гайда — …» (в корпусе 30 гайдов из 49)",
            confidence=0.5,
        ))
    classes_seen, classes_missing = [], []
    if "matchups" in bodies and (expected_classes or data["require_classes"]):
        want = expected_classes if expected_classes is not None else CLASSES
        heads = headings(text)
        found = {"Матч-апы": (bodies["matchups"].heading_line - 1, bodies["matchups"].title)}
        mu = check_matchups(text, heads, found)
        if mu:
            classes_seen = [c for c in mu[0] if c in want]
            classes_missing = [c for c in want if c not in mu[0]]
            if classes_missing:
                findings.append(_finding(
                    "structure.matchups", "review",
                    f"матч-апы не покрывают классы: {', '.join(classes_missing)}",
                    confidence=0.7,
                    suggestion="классы берутся из исходника; выдумывать разбор нельзя",
                ))
    metrics.update({
        "toc_only": toc_only,
        "order_ok": order["ok"],
        "thin": [t["id"] for t in thin],
        "walls": len(wall_list),
        "classes_seen": classes_seen,
        "classes_missing": classes_missing,
        "opening": opening,
    })
    return findings, metrics


# ── План переплавки ───────────────────────────────────────────────────────


def check_outline(outline_json, claims, profile):
    """План модели против скелета профиля и утверждений источника.

    outline_json: {"sections": [{"id"|"title", "claims": [str]}], "missing_sections": [id]}
    claims: результат claims.extract(source) или None.
    Возвращает находки со severity error/review.
    """
    data = profile_data(profile)
    sections = data["sections"]
    known = {s["id"]: s for s in sections}
    findings = []
    if not isinstance(outline_json, dict) or not isinstance(outline_json.get("sections"), list):
        return [_finding("structure.outline.shape", "error",
                         "план не в ожидаемой форме: нужен объект с sections[]", confidence=1.0)]
    missing_declared = [str(x) for x in outline_json.get("missing_sections") or []]
    seen_ids = []
    for i, sec in enumerate(outline_json["sections"], 1):
        if not isinstance(sec, dict):
            findings.append(_finding("structure.outline.shape", "error",
                                     f"раздел {i} плана — не объект", confidence=1.0))
            continue
        sid = sec.get("id") or _section_of(str(sec.get("title", "")), sections)
        if sid not in known:
            findings.append(_finding(
                "structure.outline.unknown-section", "error",
                f"раздел плана «{sec.get('title') or sec.get('id')}» не входит в профиль {data['id']}",
                confidence=0.95,
            ))
            continue
        seen_ids.append(sid)
        sec_claims = [c for c in sec.get("claims") or [] if str(c).strip()]
        if not sec_claims and known[sid]["required"] and sid not in missing_declared:
            findings.append(_finding(
                "structure.outline.empty-section", "error",
                f"обязательный раздел «{known[sid]['title']}» в плане без тезисов",
                suggestion="дать тезисы из исходника или перенести раздел в missing_sections",
                confidence=0.95,
            ))
    for sid in missing_declared:
        if sid not in known:
            findings.append(_finding(
                "structure.outline.unknown-section", "error",
                f"missing_sections содержит неизвестный раздел «{sid}»", confidence=0.95,
            ))
        elif sid in seen_ids:
            findings.append(_finding(
                "structure.outline.conflict", "error",
                f"раздел «{known[sid]['title']}» и в плане, и в missing_sections", confidence=1.0,
            ))
    for s in sections:
        if s["required"] and s["id"] not in seen_ids and s["id"] not in missing_declared:
            findings.append(_finding(
                "structure.outline.missing-section", "error",
                f"обязательный раздел «{s['title']}» ни в плане, ни в missing_sections",
                suggestion="раздел без материала объявляется отсутствующим, а не выдумывается",
                confidence=0.95,
            ))
    expected = [s["id"] for s in sections if s["id"] in seen_ids]
    if seen_ids != expected:
        findings.append(_finding(
            "structure.outline.order", "review",
            "порядок разделов плана отличается от скелета",
            evidence=" → ".join(seen_ids), suggestion=" → ".join(expected), confidence=0.7,
        ))
    if claims:
        all_claims_text = " ".join(
            str(c) for sec in outline_json["sections"] if isinstance(sec, dict)
            for c in sec.get("claims") or []
        )
        for card in claims.get("cards") or []:
            name = card["name"] if isinstance(card, dict) else str(card)
            if not phrase_present(name, all_claims_text):
                findings.append(_finding(
                    "structure.outline.orphan-claim", "review",
                    f"карта исходника «{name}» не попала ни в один тезис плана",
                    confidence=0.6,
                ))
    return findings


def main():
    ap = argparse.ArgumentParser(description="Проверка структуры гайда")
    ap.add_argument("file", nargs="?")
    ap.add_argument("--profile", help="жанровый профиль из config/profiles")
    ap.add_argument("--deep", action="store_true", help="порядок, тонкие разделы, полотна, зачин")
    ap.add_argument("--archetype")
    ap.add_argument("--expansion")
    ap.add_argument("--outline", metavar="PROFILE", help="показать скелет профиля и выйти")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    args = ap.parse_args()

    if args.outline:
        print(json.dumps(outline(args.outline), ensure_ascii=False, indent=2))
        return 0
    if not args.file:
        ap.error("нужен файл или --outline")
    p = Path(args.file)
    if not p.exists():
        print(f"нет файла: {p}", file=sys.stderr)
        return 2

    text = p.read_text(encoding="utf-8")
    if args.profile or args.deep or args.format == "json":
        findings, metrics = analyze(text, args.profile, archetype=args.archetype,
                                    expansion=args.expansion, deep=args.deep)
        if args.format == "json":
            print(json.dumps({"findings": findings, "metrics": metrics},
                             ensure_ascii=False, indent=2))
        else:
            print(f"\n{p.name}   профиль: {args.profile or 'constructed-guide'}")
            if not findings:
                print("  структура совпадает со скелетом")
            for f in findings:
                where = f" стр.{f['line']}" if f.get("line") else ""
                print(f"  [{f['severity']}]{where} {f['message']}")
                if f.get("suggestion"):
                    print(f"      → {f['suggestion']}")
            print(f"\n  разделов {metrics['present']} из {metrics['required']}")
        return 0
    heads = headings(text)
    found = find_blocks(heads)

    print(f"\n{p.name}\nзаголовков найдено: {len(heads)}\n")
    print(f"{'блок':<14}{'':<6}{'в корпусе':>11}")
    print("-" * 40)
    missing_req = []
    for name, _, freq, required in BLOCKS:
        if name in found:
            mark, note = "есть", ""
        elif required:
            mark, note = "НЕТ", "  ← обязательный"
            missing_req.append(name)
        else:
            mark, note = "нет", "  (необязательный)"
        print(f"{name:<14}{mark:<6}{freq:>10}%{note}")

    order_now = [n for n in (b[0] for b in BLOCKS) if n in found]
    order_by_pos = sorted(order_now, key=lambda n: found[n][0])
    if order_now != order_by_pos:
        print(f"\nПОРЯДОК отличается от обычного")
        print(f"  в корпусе: {' → '.join(b[0] for b in BLOCKS)}")
        print(f"  здесь:     {' → '.join(order_by_pos)}")

    mu = check_matchups(text, heads, found)
    if mu:
        seen, missing = mu
        print(f"\nМАТЧ-АПЫ: разобрано классов {len(seen)} из {len(CLASSES)}")
        if missing:
            print(f"  не упомянуты: {', '.join(missing)}")

    print("\nИТОГ")
    if missing_req:
        print(f"  ! Нет обязательных разделов: {', '.join(missing_req)}")
        print("    В корпусе они есть почти везде — проверить, черновик это или так задумано.")
    elif mu and mu[1]:
        print("  Разделы на месте, но матч-апы покрывают не все классы.")
    else:
        print("  Структура совпадает с обычной.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
