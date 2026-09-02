"""HTTP-сайдкар с анализаторами.

Go-сервис оркеструет и говорит с моделью, но проверки остаются здесь:
русской морфологии для Go практически нет, а без неё теряется распознавание
падежей и коротких имён — то, что далось труднее всего.

Только стандартная библиотека: сайдкар должен подниматься где угодно.

    python3 -m editorteam.server --port 8731

    POST /analyze  {text, game, profile, mode}          -> отчёт
    POST /validate {before, after, depth, claims_before, ...}  -> вердикт по правке
    POST /rules    {game, profile, mode, depth, text}   -> правила и образцы для модели
    POST /outline/validate {outline, source, game, profile} -> проверка плана переплавки
    GET  /health

Глубина правки `depth` (лёгкая | обычная | глубокая | переплавка) меняет
точку отсчёта затвора: в переплавке результат сравнивается с нормой автора
и с утверждениями исходника, а не с длиной и голосом исходника.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from editorteam import games as G
from editorteam import profiles as P
from editorteam.corpus_learning import CorpusError, CorpusStore
from editorteam.finding import Finding, Report

ROOT = Path(__file__).resolve().parents[2]
REPO_SKILL_SCRIPTS = ROOT / ".claude" / "skills" / "hs-edit" / "scripts"
SKILL_SCRIPTS = REPO_SKILL_SCRIPTS if REPO_SKILL_SCRIPTS.exists() else ROOT / "scripts"
MAX_BODY = 2 * 1024 * 1024
MIN_RHYTHM_SENTENCES = 15
MIN_SHRINK_WORDS = 8
SEVERE_SHORT_SHRINK_PCT = 50


def _scripts():
    if str(SKILL_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SKILL_SCRIPTS))
    import common as C

    return C


def _corpus_version() -> str:
    C = _scripts()
    return C.corpus_manifest().get("current_version", "legacy-v1")


def _norms(g) -> dict:
    """Нормы игры в виде словаря для rewrite_gate."""
    n = g.norms
    return {
        "voice_low": n.voice_low,
        "voice_per_1k": n.voice_per_1k,
        "rhythm_alarm": n.rhythm_alarm,
        "rhythm_ratio": n.rhythm_ratio,
        "markers_per_10k": n.markers_per_10k,
        "sentence_mean": n.sentence_mean,
        "paragraph_sentences": n.paragraph_sentences,
        "provisional": n.provisional,
    }


def analyze(
    text: str,
    game: str,
    profile: str | None,
    mode: str = "GUIDE",
    evidence_requested: bool = False,
) -> dict:
    C = _scripts()
    g = G.load(game)
    prof = P.load(profile or (g.profiles[0] if g.profiles else P.DEFAULT))
    report = Report(document="<текст>", profile=prof.id)

    caveat = g.norms.caveat()
    if caveat:
        report.notes.append(caveat)

    if prof.enabled("markers"):
        markers = C.sibling("markers")
        for hit in markers.scan(text, markers.load_patterns()):
            sev = {"remove": "likely", "rewrite": "likely", "review": "review"}[hit["action"]]
            report.add(
                Finding(
                    id=f"markers.{hit['id']}",
                    analyzer="markers",
                    category=hit["action"],
                    severity=sev,
                    confidence=0.6 if sev == "review" else 0.8,
                    message=hit["name"],
                    evidence=hit["text"],
                    suggestion=hit["fix"],
                    line=hit["line"],
                    profile=prof.id,
                )
            )
    else:
        report.skipped.append("markers")

    guide_voice = C.sibling("guide_voice")
    mode = guide_voice.normalize_mode(mode)
    for hit in guide_voice.scan(text, mode, evidence_requested):
        report.add(
            Finding(
                id=f"guide_voice.{hit['id']}",
                analyzer="guide_voice",
                category="editorial-mode",
                severity=hit["severity"],
                confidence=0.9,
                message="research-report narration в режиме GUIDE",
                evidence=hit["evidence"],
                suggestion=hit["suggestion"],
                line=hit["line"],
                profile=prof.id,
            )
        )

    if prof.enabled("clarity"):
        clarity = C.sibling("clarity")
        clarity_findings, clarity_metrics = clarity.analyze(text, prof.id)
        for hit in clarity_findings:
            report.add(
                Finding(
                    id=hit["id"],
                    analyzer="clarity",
                    category=hit["category"],
                    severity=hit["severity"],
                    confidence=hit.get("confidence", 0.7),
                    message=hit["message"],
                    evidence=hit.get("evidence", ""),
                    suggestion=hit.get("suggestion", ""),
                    line=hit.get("line"),
                    profile=prof.id,
                    meta=hit.get("meta", {}),
                )
            )
        report.metrics.update({f"clarity_{key}": value for key, value in clarity_metrics.items()})

    skip = g.skip_reason("cards")
    if prof.enabled("cards") and not skip:
        cards = C.sibling("cards")
        cards.ensure_pymorphy()
        db = C.card_db()
        idx = cards.Index(db["карты"], C.morph())
        for (was, off), n in cards.check_apostrophes(text, idx).items():
            report.add(
                Finding(
                    id="cards.apostrophe",
                    analyzer="cards",
                    category="localization",
                    severity="error",
                    message=f"апостроф в названии ({g.names_kind})",
                    evidence=was,
                    suggestion=off,
                    meta={"count": n},
                )
            )
        for (was, off), n in cards.check_dashes(text, db["карты"]).items():
            report.add(
                Finding(
                    id="cards.dash",
                    analyzer="cards",
                    category="localization",
                    severity="error",
                    message=f"тире в названии ({g.names_kind})",
                    evidence=was,
                    suggestion=off,
                    meta={"count": n},
                )
            )
    else:
        report.skipped.append("cards")
        if skip:
            report.notes.append(f"сверка имён отключена: {skip}")

    if prof.enabled("consistency"):
        cons = C.sibling("consistency")
        for label, forms in cons.check_variants(C.mask_protected(text)):
            report.add(
                Finding(
                    id="consistency.variants",
                    analyzer="consistency",
                    category="spelling",
                    severity="likely",
                    confidence=0.85,
                    message=f"разнобой: {label}",
                    evidence=", ".join(f"{f} ×{c}" for f, c in forms[:4]),
                    suggestion="выбрать одно написание",
                )
            )
    else:
        report.skipped.append("consistency")

    if prof.enabled("structure"):
        st = C.sibling("structure")
        heads = [h.lower() for _, h in st.headings(text)]
        for section in prof.required_sections:
            if not any(h in section.variants for h in heads):
                report.add(
                    Finding(
                        id=f"structure.missing.{section.id}",
                        analyzer="structure",
                        category="structure",
                        severity="likely",
                        confidence=0.7,
                        message=f"нет обязательного раздела «{section.title}»",
                        suggestion="; ".join(section.variants[:3]),
                    )
                )
    else:
        report.skipped.append("structure")

    report.metrics.update(measure(text, g, prof))
    d = report.to_dict()
    d["game"] = g.id
    d["editorial_mode"] = mode
    d["corpus_version"] = _corpus_version()
    d["norms_provisional"] = g.norms.provisional
    return d


def measure(text: str, g, prof) -> dict:
    C = _scripts()
    out: dict = {"words": len(text.split())}
    if prof.enabled("rhythm"):
        r = C.sibling("rhythm").measure(text)
        if r:
            out["rhythm_ratio"] = round(r["ratio"], 3)
            out["rhythm_norm"] = g.norms.rhythm_ratio
    if prof.enabled("soul"):
        soul = C.sibling("soul")
        s, _ = soul.measure(text)
        if s:
            out["voice_per_1k"] = round(sum(v["per1k"] for v in s.values()), 1)
            out["voice_norm"] = g.norms.voice_per_1k
    return out


def validate(
    before: str,
    after: str,
    game: str,
    profile: str | None,
    *,
    mode: str = "GUIDE",
    depth: str = "обычная",
    declared_missing: list[str] | None = None,
    claims_before: list[dict] | None = None,
    claims_after: list[dict] | None = None,
    current_meta_epoch: str | None = None,
    current_patch: str | None = None,
    evidence_requested: bool = False,
) -> dict:
    """Затвор: что правка сделала с голосом, защищённым и ритмом.

    Это главная проверка сервиса. Модель правит, а принимается правка
    только если не потеряла того, ради чего текст пишется.

    В глубине «переплавка» исходник — источник фактов, а не формы, поэтому
    относительные проверки голоса, ритма и длины выключены. Вместо них —
    абсолютные нормы автора (rewrite_gate) и покрытие утверждений (claims).
    """
    C = _scripts()
    g = G.load(game)
    prof = P.load(profile or (g.profiles[0] if g.profiles else P.DEFAULT))
    soul = C.sibling("soul")
    rhythm = C.sibling("rhythm")
    report_mod = C.sibling("report")
    guide_voice = C.sibling("guide_voice")
    certainty_guard = C.sibling("certainty_guard")
    semantic_diff = C.sibling("semantic_diff")
    rewrite_gate = C.sibling("rewrite_gate")
    mode = guide_voice.normalize_mode(mode)
    depth = rewrite_gate.normalize_depth(depth)
    rewrite = depth == rewrite_gate.REWRITE
    before_leaks = guide_voice.scan(before, mode, evidence_requested)
    allowed_evidence_numbers = [
        number for hit in before_leaks for number in semantic_diff.NUMBERS.findall(hit["evidence"])
    ]

    sa, wa = soul.measure(before)
    sb, wb = soul.measure(after)
    ra, rb = rhythm.measure(before), rhythm.measure(after)
    gone = report_mod.protected_lost(before, after)

    violations = []
    warnings = []
    voice_before = round(sum(v["per1k"] for v in sa.values()), 1) if sa else None
    voice_after = round(sum(v["per1k"] for v in sb.values()), 1) if sb else None
    lost_voice_signals = []
    if sa and sb and not rewrite:
        for name in soul.SIGNALS:
            if soul.classify(sa[name], sb[name], wa, wb) == "сигналы удалены":
                item = {
                    "kind": "voice_lost",
                    "signal": name,
                    "was": sa[name]["n"],
                    "now": sb[name]["n"],
                    "message": f"проверьте потерю живого: {name} — {sa[name]['n']} → {sb[name]['n']} мест",
                }
                warnings.append(item)
                lost_voice_signals.append(item)
        # A local voice signal is a review prompt, not a verdict by itself.
        # Reject only article-sized flattening that crosses the corpus safety
        # floor; short snippets and justified local deletions stay reviewable.
        if (
            lost_voice_signals
            and min(wa, wb) >= soul.MIN_WORDS
            and voice_before is not None
            and voice_after is not None
            and g.norms.voice_low is not None
            and voice_before >= g.norms.voice_low
            and voice_after < g.norms.voice_low
        ):
            violations.append(
                {
                    "kind": "voice_flattened",
                    "message": (
                        f"живой голос вышел ниже нижней границы: "
                        f"{voice_before:.1f} → {voice_after:.1f} на 1000 слов"
                    ),
                }
            )
    if allowed_evidence_numbers and "числа" in gone:
        protected_allowance = Counter(number.rstrip("%") for number in allowed_evidence_numbers)
        gone["числа"] -= protected_allowance
        if not gone["числа"]:
            del gone["числа"]
    if rewrite:
        # Счётчик повторов бессмыслен для пересборки: год из колонтитула PDF
        # встречается на каждой странице, а код колоды в PDF разорван переносом.
        # Числа сравниваются как факты с контекстом, коды — склеенными.
        claims_mod = C.sibling("claims")
        gone.pop("числа", None)
        gone.pop("коды колод", None)
        lost_numbers = sorted(claims_mod.fact_numbers(before) - claims_mod.fact_numbers(after))
        if lost_numbers:
            shown = ", ".join(f"{n}" + (f" ({c})" if c else "") for n, c in lost_numbers[:6])
            violations.append(
                {
                    "kind": "protected_lost",
                    "signal": "числа",
                    "message": f"пропало защищённое (числа с контекстом): {shown}",
                    "severity": "error",
                }
            )
        after_codes = set(claims_mod.deck_codes(after))
        lost_codes = [c for c in claims_mod.deck_codes(before) if c not in after_codes]
        if lost_codes:
            violations.append(
                {
                    "kind": "protected_lost",
                    "signal": "коды колод",
                    "message": "пропало защищённое (коды колод): "
                    + ", ".join(c[:24] + "…" for c in lost_codes[:5]),
                    "severity": "error",
                }
            )
    for kind, items in gone.items():
        violations.append(
            {
                "kind": "protected_lost",
                "signal": kind,
                "message": f"пропало защищённое ({kind}): " + ", ".join(list(items)[:5]),
            }
        )
    # Rhythm variance is meaningful on article-sized samples, but is extremely
    # noisy on snippets: changing one word in two sentences can move the ratio
    # more than the guardrail threshold. Keep the metric below, but only gate
    # edits when both samples are large enough for a stable comparison.
    if (
        not rewrite
        and ra
        and rb
        and ra["n"] >= MIN_RHYTHM_SENTENCES
        and rb["n"] >= MIN_RHYTHM_SENTENCES
        and rb["ratio"] < ra["ratio"] - 0.03
    ):
        item = {
            "kind": "rhythm_flattened",
            "message": f"проверьте ритм: {ra['ratio']:.2f} → {rb['ratio']:.2f}",
        }
        if g.norms.rhythm_alarm is not None and rb["ratio"] < g.norms.rhythm_alarm:
            violations.append(item)
        else:
            warnings.append(item)

    delta = 100 * (len(after) - len(before)) / max(1, len(before))
    lost_words = max(0, wa - wb)
    if rewrite:
        pass  # переплавка честно сжимает воду: потерю смысла ловит покрытие утверждений
    elif delta <= -SEVERE_SHORT_SHRINK_PCT:
        violations.append(
            {
                "kind": "text_shrunk",
                "message": f"текст усох на {abs(delta):.0f}% — вероятно пропали мысли",
            }
        )
    elif delta < -5 and lost_words >= MIN_SHRINK_WORDS:
        warnings.append(
            {
                "kind": "text_shrunk",
                "message": (
                    f"проверьте сокращение на {abs(delta):.0f}%: "
                    "каждое удаление должно быть повтором, пустой рамкой или частью задачи"
                ),
            }
        )

    for item in certainty_guard.scan(before, after, claims_before):
        # Документная проверка сравнивает самый сильный маркер во всём тексте.
        # При переплавке текст пишется заново, и «должна» в новом абзаце — ещё
        # не усиление совета; жёстким остаётся только контракт конкретного claim.
        if rewrite and not item.get("claim_id"):
            warnings.append({**item, "severity": "review"})
        else:
            violations.append(item)
    for item in semantic_diff.compare(
        before,
        after,
        claims_before,
        claims_after,
        current_meta_epoch,
        current_patch,
        allowed_evidence_numbers,
    ):
        # в переплавке набор чисел уже сверен по фактам с контекстом выше
        if rewrite and item.get("field") == "numbers":
            continue
        violations.append(item)
    for hit in guide_voice.scan(after, mode, evidence_requested):
        violations.append(
            {
                "kind": "EVIDENCE_NARRATION_LEAK",
                "message": "research-report narration попал в GUIDE: " + hit["evidence"],
                "suggestion": hit["suggestion"],
                "line": hit["line"],
                "severity": "error",
            }
        )

    # Публичный профиль дополнительно проверяет смысловую роль игровых
    # терминов и понятность финального текста. Ошибка роли блокирует правку;
    # плотность и тезис остаются предупреждениями для редактора.
    clarity_metrics = {}
    if prof.enabled("clarity"):
        clarity = C.sibling("clarity")
        clarity_findings, clarity_metrics = clarity.analyze(after, prof.id)
        for hit in clarity_findings:
            item = {
                "kind": hit["id"],
                "message": hit["message"],
                "suggestion": hit.get("suggestion", ""),
                "line": hit.get("line"),
                "severity": hit["severity"],
            }
            if hit["severity"] == "error":
                violations.append(item)
            else:
                warnings.append(item)

    # Переплавка: точка отсчёта — норма автора и утверждения исходника
    rewrite_metrics: dict = {}
    if rewrite:
        claims_mod = C.sibling("claims")
        source = claims_mod.extract(before, profile=prof.id)
        gate_v, gate_w, gate_m = rewrite_gate.analyze(
            after,
            norms=_norms(g),
            profile=prof.id,
            declared_missing=declared_missing,
            expected_classes=source.get("classes") or None,
            archetype=source.get("archetype"),
        )
        cov_v, cov_w, cov_m = claims_mod.coverage(source, after, declared_missing=declared_missing)
        violations.extend(gate_v)
        violations.extend(cov_v)
        warnings.extend(gate_w)
        warnings.extend(cov_w)
        rewrite_metrics = {
            **{f"rewrite_{key}": value for key, value in gate_m.items()},
            **{f"coverage_{key}": value for key, value in cov_m.items()},
        }

    return {
        "accepted": not violations,
        "violations": violations,
        "warnings": warnings,
        "metrics": {
            "length_change_pct": round(delta, 1),
            "rhythm_before": round(ra["ratio"], 3) if ra else None,
            "rhythm_after": round(rb["ratio"], 3) if rb else None,
            "voice_before": voice_before,
            "voice_after": voice_after,
            **{f"clarity_{key}": value for key, value in clarity_metrics.items()},
            **rewrite_metrics,
        },
        "norms_provisional": g.norms.provisional,
        "editorial_mode": mode,
        "edit_depth": depth,
        "declared_missing": list(declared_missing or []),
        "corpus_version": _corpus_version(),
    }


def validate_outline(outline: dict, source: str, game: str, profile: str | None) -> dict:
    """План переплавки против скелета профиля и утверждений исходника.

    Схема плана: {"sections": [{"id", "title", "claims": [str]}],
                  "missing_sections": [id], "notes": [str]}.
    OUTLINE_INVALID — форма и обязательные разделы, OUTLINE_INVENTED — карта
    или число, которых нет в исходнике. Карты исходника без тезиса — warning.
    """
    C = _scripts()
    g = G.load(game)
    prof = P.load(profile or (g.profiles[0] if g.profiles else P.DEFAULT))
    structure = C.sibling("structure")
    claims_mod = C.sibling("claims")
    source_claims = claims_mod.extract(source, profile=prof.id) if source else None

    violations, warnings = [], []
    for f in structure.check_outline(outline, source_claims, prof.id):
        item = {
            "kind": "OUTLINE_INVALID" if f["severity"] == "error" else "outline_review",
            "id": f["id"],
            "message": f["message"],
            "suggestion": f.get("suggestion", ""),
            "severity": f["severity"],
        }
        (violations if f["severity"] == "error" else warnings).append(item)

    if source_claims and isinstance(outline, dict) and isinstance(outline.get("sections"), list):
        plan_text = " ".join(
            str(c)
            for sec in outline["sections"]
            if isinstance(sec, dict)
            for c in sec.get("claims") or []
        )
        added = claims_mod.compare_sets(
            source_claims, claims_mod.extract(plan_text, profile=prof.id)
        )
        for card in added["added_cards"]:
            violations.append(
                {
                    "kind": "OUTLINE_INVENTED",
                    "field": "card",
                    "message": f"в плане карта «{card}», которой нет в исходнике",
                    "severity": "error",
                }
            )
        for number in added["added_numbers"]:
            violations.append(
                {
                    "kind": "OUTLINE_INVENTED",
                    "field": "number",
                    "message": f"в плане число {number}, которого нет в исходнике",
                    "severity": "error",
                }
            )
        for cls in added["added_classes"]:
            warnings.append(
                {
                    "kind": "outline_review",
                    "field": "class",
                    "message": f"в плане класс «{cls}», которого нет в исходнике",
                    "severity": "review",
                }
            )

    normalized = None
    if isinstance(outline, dict) and isinstance(outline.get("sections"), list):
        sections = structure.load_profile_sections(prof.id)
        normalized = {
            "sections": [],
            "missing_sections": [str(x) for x in outline.get("missing_sections") or []],
            "notes": [str(x) for x in outline.get("notes") or []],
        }
        for sec in outline["sections"]:
            if not isinstance(sec, dict):
                continue
            sid = sec.get("id") or structure._section_of(str(sec.get("title", "")), sections)
            normalized["sections"].append(
                {
                    "id": sid,
                    "title": sec.get("title")
                    or next((s["title"] for s in sections if s["id"] == sid), sid),
                    "claims": [str(c) for c in sec.get("claims") or []],
                }
            )
    return {
        "ok": not violations,
        "violations": violations,
        "warnings": warnings,
        "normalized": normalized,
        "profile": prof.id,
    }


# ── Правила и образцы для модели ─────────────────────────────────────────

VOICE_SECTIONS = ("Характерное", "Вход", "Выход", "Юмор", "Отношение к цифрам")
VOICE_BUDGET = 2500
EXAMPLE_MIN_WORDS, EXAMPLE_MAX_WORDS = 35, 90
EXAMPLES_MAX = 5
EXAMPLES_BUDGET_CHARS = 4500


@lru_cache(maxsize=1)
def voice_signature() -> str:
    """Сжатый почерк автора из ГОЛОС.md: только разделы про манеру."""
    path = ROOT / "ГОЛОС.md"
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    parts = []
    for title in VOICE_SECTIONS:
        m = re.search(rf"^## {re.escape(title)}\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
        if not m:
            continue
        lines = []
        for raw in m.group(1).splitlines():
            line = raw.strip()
            if not line or line.startswith("|") or line.startswith("###"):
                continue
            if line.startswith(">"):
                line = "Пример: " + line.lstrip("> ").strip()
            lines.append(line)
        if lines:
            parts.append(f"{title}: " + " ".join(lines))
    out = "\n".join(parts)
    return out[:VOICE_BUDGET]


@lru_cache(maxsize=1)
def _exemplars() -> dict:
    C = _scripts()
    path = C.ASSETS / "exemplars.json"
    if not path.exists():
        return {"profiles": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"profiles": {}}


@lru_cache(maxsize=2)
def _archive(corpus_version: str):
    """Индекс архива строится один раз на версию корпуса: это секунды, не миллисекунды."""
    C = _scripts()
    if not C.corpus_files():
        return None
    return C.sibling("echo").Archive()


def _good_example(text: str) -> bool:
    C = _scripts()
    words = len(text.split())
    if not EXAMPLE_MIN_WORDS <= words <= EXAMPLE_MAX_WORDS:
        return False
    if text.rstrip().endswith(":") or re.search(r"наверх|нажмите на изображение", text, re.I):
        return False
    if len(re.findall(r"(?<!\w)\d+(?:[.,]\d+)?%?(?!\w)", text)) > 2:
        return False
    markers = C.sibling("markers")
    if markers.scan(text, markers.load_patterns()):
        return False
    s, _ = C.sibling("soul").measure(text)
    return bool(s) and sum(1 for v in s.values() if v["n"] > 0) >= 2


def style_examples(text: str | None, profile_id: str) -> tuple[list[dict], str]:
    """Образцы манеры: сначала архив по темам исходника, потом отобранные заранее."""
    C = _scripts()
    out: list[dict] = []
    seen_names: set[str] = set()
    source = "archive"
    archive = _archive(_corpus_version()) if text else None
    if archive is not None:
        queries = C.paragraphs(text, min_words=25)[:8] or [text[:1500]]
        for q in queries:
            for score, name, para, meta in archive.search(q, top=2):
                if score <= 3.0 or name in seen_names or not _good_example(para):
                    continue
                seen_names.add(name)
                out.append(
                    {"role": "по теме", "name": name, "text": para, "score": round(score, 2)}
                )
                if len(out) >= EXAMPLES_MAX:
                    break
            if len(out) >= EXAMPLES_MAX:
                break
    if len(out) < 3:
        pool = _exemplars().get("profiles", {})
        fallback = pool.get(profile_id) or []
        if not fallback:
            fallback = pool.get("global") or []
            source = "global" if not out else "archive+global"
        else:
            source = "exemplars" if not out else "archive+exemplars"
        for item in fallback:
            if item.get("name") in seen_names:
                continue
            out.append(
                {
                    "role": item.get("role", "образец"),
                    "name": item.get("name", ""),
                    "text": item["text"],
                    "score": None,
                }
            )
            seen_names.add(item.get("name"))
            if len(out) >= EXAMPLES_MAX:
                break
    total = 0
    trimmed = []
    for item in out:
        total += len(item["text"])
        if total > EXAMPLES_BUDGET_CHARS:
            break
        trimmed.append(item)
    return trimmed, source


def marker_lists() -> dict:
    """Маркеры для промпта по действию: фразы, а не регексы."""
    C = _scripts()
    markers = C.sibling("markers")
    out = {"remove": [], "rewrite": [], "review": []}
    for p in markers.load_patterns():
        out[p["action"]].append(
            {
                "name": p["name"],
                "examples": p.get("examples", []),
                "fix": p.get("fix", ""),
            }
        )
    return out


RHYTHM_INSTRUCTION = [
    "В каждом разделе на 8 предложений — хотя бы одно короче 8 слов; на 15 — хотя бы одно длиннее 25.",
    "Не подгоняй предложения к 12–16 словам: одинаковая длина — главный признак машинного текста.",
    "Абзац — обычно 2–3 предложения; полотно из 6 и больше предложений режь по смысловому шву.",
    "Три обрубка подряд — приём, четыре — тик.",
]


def rules_for(
    game: str,
    profile: str | None,
    mode: str = "GUIDE",
    *,
    text: str | None = None,
    depth: str = "обычная",
) -> dict:
    """Правила, которые Go подставляет в запрос к модели.

    В глубине «переплавка» к правилам добавляются скелет жанра, почерк автора,
    образцы манеры из архива и списки маркеров фразами — модель должна видеть,
    как автор звучит, а не только числа норм.
    """
    from editorteam import corrections as CR
    from editorteam import rules as R

    C = _scripts()
    g = G.load(game)
    prof = P.load(profile or (g.profiles[0] if g.profiles else P.DEFAULT))
    guide_voice = C.sibling("guide_voice")
    clarity = C.sibling("clarity")
    rewrite_gate = C.sibling("rewrite_gate")
    depth = rewrite_gate.normalize_depth(depth)
    replace, keep = [], []
    for r in R.terminology():
        if r.decision in ("auto_replace", "forbidden") and r.preferred:
            replace.append({"from": r.subject, "to": r.preferred})
        elif r.decision == "allowed":
            keep.append(r.subject)
    out = {
        "game": g.id,
        "profile": prof.id,
        "depth": depth,
        "protected": g.protected,
        "replace": replace,
        "keep": keep,
        "typography": R.typography(),
        "sections_required": [s.title for s in prof.required_sections],
        "sections": prof.skeleton(),
        "min_words": prof.min_words,
        "require_classes": prof.require_classes,
        "form": prof.form,
        "corrections": CR.for_prompt(),
        "norms": {
            **_norms(g),
        },
        "editorial": guide_voice.mode_rules(mode),
        "reader_quality": clarity.model_rules(prof.id),
        "corpus_version": _corpus_version(),
        "style_memory": {
            "allowed": "approved guides from any patch",
            "purpose": ["voice", "rhythm", "terminology", "structure"],
        },
        "game_knowledge": {
            "allowed": "current validated evidence only",
            "requires": ["current_patch", "current_meta_epoch"],
        },
    }
    if depth == rewrite_gate.REWRITE:
        structure = C.sibling("structure")
        examples, source = style_examples(text, prof.id)
        out.update(
            {
                "skeleton": structure.outline(prof.id),
                "voice_signature": voice_signature(),
                "style_examples": examples,
                "style_examples_source": source,
                "markers": marker_lists(),
                "rhythm_instruction": RHYTHM_INSTRUCTION,
                "prompt_budget": {
                    "added_tokens_max": 1900,
                    "trim_order": ["style_examples", "markers.review", "voice_signature"],
                },
            }
        )
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "editorteam-sidecar"

    def log_message(self, fmt, *args):
        sys.stderr.write(f"  {self.address_string()} {fmt % args}\n")

    def _send(self, code: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read(self) -> dict | None:
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return None
        if n <= 0 or n > MAX_BODY:
            return None
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"ok": True, "games": G.available(), "profiles": P.available()})
        else:
            self._send(404, {"error": "нет такого пути"})

    def do_POST(self):
        data = self._read()
        if data is None:
            self._send(400, {"error": "нужен JSON в теле запроса"})
            return
        try:
            if self.path == "/analyze":
                self._send(
                    200,
                    analyze(
                        data.get("text", ""),
                        data.get("game", G.DEFAULT),
                        data.get("profile"),
                        data.get("mode", "GUIDE"),
                        data.get("evidence_requested", False),
                    ),
                )
            elif self.path == "/validate":
                self._send(
                    200,
                    validate(
                        data.get("before", ""),
                        data.get("after", ""),
                        data.get("game", G.DEFAULT),
                        data.get("profile"),
                        mode=data.get("mode", "GUIDE"),
                        depth=data.get("depth") or "обычная",
                        declared_missing=data.get("declared_missing"),
                        claims_before=data.get("claims_before"),
                        claims_after=data.get("claims_after"),
                        current_meta_epoch=data.get("current_meta_epoch"),
                        current_patch=data.get("current_patch"),
                        evidence_requested=data.get("evidence_requested", False),
                    ),
                )
            elif self.path == "/rules":
                self._send(
                    200,
                    rules_for(
                        data.get("game", G.DEFAULT),
                        data.get("profile"),
                        data.get("mode", "GUIDE"),
                        text=data.get("text"),
                        depth=data.get("depth") or "обычная",
                    ),
                )
            elif self.path == "/outline/validate":
                self._send(
                    200,
                    validate_outline(
                        data.get("outline") or {},
                        data.get("source", ""),
                        data.get("game", G.DEFAULT),
                        data.get("profile"),
                    ),
                )
            elif self.path == "/corpus/add":
                self._send(
                    200,
                    CorpusStore(ROOT).add(
                        Path(data["path"]),
                        published_at=data["published_at"],
                        patch=data["patch"],
                        author=data.get("author", "manacost"),
                        tags=data.get("tags", []),
                        source=data.get("source", "published"),
                        genre=data.get("genre", "constructed-guide"),
                        approve=data.get("approve", False),
                        guide_id=data.get("id"),
                    ),
                )
            elif self.path == "/corpus/approve":
                self._send(200, CorpusStore(ROOT).approve(data["guide_id"]))
            elif self.path == "/corpus/remove":
                self._send(200, CorpusStore(ROOT).remove(data["guide_id"]))
            elif self.path == "/corpus/reject":
                self._send(200, CorpusStore(ROOT).reject(data["guide_id"]))
            elif self.path == "/corpus/rollback":
                self._send(200, CorpusStore(ROOT).rollback(data["version"]))
            elif self.path == "/corpus/inspect":
                self._send(200, CorpusStore(ROOT).inspect())
            elif self.path == "/corpus/versions":
                self._send(200, {"versions": CorpusStore(ROOT).versions()})
            elif self.path == "/corpus/compare":
                self._send(
                    200,
                    CorpusStore(ROOT).compare(data["before_version"], data["after_version"]),
                )
            else:
                self._send(404, {"error": "нет такого пути"})
        except (G.GameError, P.ProfileError, ValueError) as e:
            self._send(400, {"error": str(e)})
        except CorpusError as e:
            self._send(409, {"error": e.code, "message": str(e)})
        except KeyError as e:
            self._send(400, {"error": f"нет обязательного поля: {e.args[0]}"})
        except Exception as e:  # noqa: BLE001
            self._send(500, {"error": f"{type(e).__name__}: {e}"})


def main() -> int:
    ap = argparse.ArgumentParser(description="HTTP-сайдкар с анализаторами")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8731)
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"сайдкар слушает http://{args.host}:{args.port}", file=sys.stderr)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
