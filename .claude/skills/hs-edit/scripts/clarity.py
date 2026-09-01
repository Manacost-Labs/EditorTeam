#!/usr/bin/env python3
"""Проверка понятности публичных статей по Полям сражений.

Это не литературный балл и не автоматическая перепись. Проверка ловит
ошибки, которые обычная сверка карт и ритма не видит: свойство, принятое за
карту, необъясненный англоязычный термин, перегруженный абзац и отсутствие
понятного тезиса в начале.

    python3 clarity.py статья.md --profile battlegrounds-article
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

# В релизе PyYAML лежит рядом со скриптами. Подключаем его явно: при прямом
# запуске Python не всегда видит sitecustomize из этой папки.
_VENDOR = Path(__file__).resolve().parents[1] / "vendor"
if _VENDOR.exists() and str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))
import yaml  # noqa: E402

CONFIG = C.ROOT / "config" / "editorial.yaml"


@lru_cache(maxsize=1)
def _config() -> dict:
    if not CONFIG.exists():
        return {}
    data = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def policy(profile: str) -> dict:
    """Вернуть правила понятности для профиля или пустой словарь."""
    profiles = _config().get("profiles") or {}
    return profiles.get(profile) or {}


def model_rules(profile: str) -> dict:
    """Короткие правила для промпта редактора, без технических деталей."""
    p = policy(profile)
    if not p:
        return {}
    entities = []
    for entity in _config().get("entities") or []:
        if not entity.get("public_forbidden"):
            continue
        entities.append(
            {
                "kind": entity.get("kind"),
                "avoid": entity.get("aliases", []),
                "use": entity.get("public_form") or entity.get("preferred"),
                "explain": entity.get("explanation", ""),
            }
        )
    preferred_terms = []
    for item in p.get("forbidden_terms", []):
        preferred = item.get("preferred")
        if preferred:
            preferred_terms.append(
                {
                    "avoid": item.get("terms", []),
                    "use": preferred,
                    "reason": item.get("message", ""),
                }
            )
    reader_contract = p.get("reader_contract") or {}
    return {
        "audience": p.get("audience", "читатель статьи"),
        "mode": p.get("mode", "public"),
        "avoid": [term for item in p.get("forbidden_terms", []) for term in item.get("terms", [])],
        "preferred_terms": preferred_terms,
        "entities": entities,
        "reader_contract": reader_contract,
        "formats": p.get("formats") or {},
        "quality": p.get("quality", {}),
        "thesis": p.get("thesis", {}),
    }


def validate_config() -> list[str]:
    """Проверить справочник ролей и публичные правила до запуска аудита."""
    data = _config()
    problems: list[str] = []
    entities = data.get("entities") or []
    entity_ids: set[str] = set()
    aliases: set[str] = set()
    for entity in entities:
        entity_id = entity.get("id")
        if not entity_id:
            problems.append("editorial.entities: понятие без id")
        elif entity_id in entity_ids:
            problems.append(f"editorial.entities: повторяющийся id {entity_id}")
        entity_ids.add(entity_id)
        for alias in entity.get("aliases") or []:
            key = str(alias).lower()
            if key in aliases:
                problems.append(f"editorial.entities: повторяющийся alias {alias}")
            aliases.add(key)
        if entity.get("public_forbidden") and not (
            entity.get("preferred") or entity.get("public_form")
        ):
            problems.append(f"editorial.entities.{entity_id}: нет русской формы")
        for raw_pattern in entity.get("misuse_patterns") or []:
            try:
                re.compile(raw_pattern)
            except re.error as exc:
                problems.append(f"editorial.entities.{entity_id}: неверный regex: {exc}")

    for profile_id, profile in (data.get("profiles") or {}).items():
        term_ids: set[str] = set()
        for item in profile.get("forbidden_terms") or []:
            term_id = item.get("id")
            if term_id in term_ids:
                problems.append(f"editorial.profiles.{profile_id}: повторяющийся id {term_id}")
            term_ids.add(term_id)
            if not item.get("terms"):
                problems.append(f"editorial.profiles.{profile_id}.{term_id}: нет terms")
        thesis = profile.get("thesis") or {}
        for group in ("problem_markers", "consequence_markers"):
            for raw_pattern in thesis.get(group) or []:
                try:
                    re.compile(raw_pattern)
                except re.error as exc:
                    problems.append(
                        f"editorial.profiles.{profile_id}.{group}: неверный regex: {exc}"
                    )
    return problems


def _line(text: str, position: int) -> int:
    return text.count("\n", 0, position) + 1


def _pattern(term: str) -> re.Pattern:
    # Граница слова нужна и для кириллицы, и для английских терминов.
    return re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE)


def _finding(
    text: str,
    *,
    finding_id: str,
    category: str,
    severity: str,
    confidence: float,
    message: str,
    evidence: str,
    suggestion: str,
    position: int,
    meta: dict | None = None,
) -> dict:
    item = {
        "id": finding_id,
        "analyzer": "clarity",
        "category": category,
        "severity": severity,
        "confidence": confidence,
        "message": message,
        "evidence": evidence.strip(),
        "suggestion": suggestion,
        "line": _line(text, position),
    }
    if meta:
        item["meta"] = meta
    return item


def _paragraphs(text: str) -> list[dict]:
    """Абзацы с позициями; заголовки не считаются отдельной мыслью."""
    out = []
    for match in re.finditer(r"(?ms)(?:^|\n\s*\n)(?P<body>.+?)(?=\n\s*\n|\Z)", text):
        body = match.group("body").strip()
        if not body:
            continue
        # Удаляем только служебные строки заголовков, не переписывая текст.
        body = re.sub(r"(?m)^\s*#{1,6}\s+[^\n]*\n?", "", body).strip()
        if len(re.findall(r"\b[А-Яа-яЁёA-Za-z]{2,}\b", body)) < 5:
            continue
        out.append(
            {
                "text": body,
                "start": match.start("body"),
                "words": len(body.split()),
                "sentences": len(C.sentences(body)),
            }
        )
    return out


def analyze(text: str, profile: str) -> tuple[list[dict], dict]:
    """Вернуть находки и измерения, не печатая их."""
    p = policy(profile)
    if not p:
        return [], {}

    findings: list[dict] = []
    masked = C.mask_protected(text)
    covered: list[tuple[int, int]] = []

    # Сначала точная ошибка роли: свойство использовано как карта/объект.
    for entity in _config().get("entities") or []:
        if not entity.get("public_forbidden"):
            continue
        aliases = entity.get("aliases") or []
        preferred = entity.get("public_form") or entity.get("preferred") or "понятное русское описание"
        for raw_pattern in entity.get("misuse_patterns") or []:
            for match in re.finditer(raw_pattern, masked, re.IGNORECASE):
                covered.append((match.start(), match.end()))
                findings.append(
                    _finding(
                        text,
                        finding_id=f"clarity.entity.{entity['id']}.role",
                        category="entity-type",
                        severity="error",
                        confidence=0.98,
                        message=f"свойство использовано как {entity.get('kind', 'игровой термин')}",
                        evidence=text[match.start() : match.end()],
                        suggestion=f"использовать: {preferred}",
                        position=match.start(),
                        meta={"kind": entity.get("kind"), "aliases": aliases},
                    )
                )

        # Любое оставшееся английское название свойства требует перевода или
        # короткого пояснения. Имена карт сюда не попадают: их проверяет cards.py.
        for alias in aliases:
            for match in _pattern(alias).finditer(masked):
                if any(start <= match.start() < end for start, end in covered):
                    continue
                findings.append(
                    _finding(
                        text,
                        finding_id=f"clarity.entity.{entity['id']}.unlocalized",
                        category="terminology",
                        severity="likely",
                        confidence=0.94,
                        message=f"необъясненное название свойства ({entity.get('kind', 'термин')})",
                        evidence=text[match.start() : match.end()],
                        suggestion=f"использовать «{preferred}» и объяснить его при первом упоминании",
                        position=match.start(),
                        meta={"kind": entity.get("kind"), "preferred": preferred},
                    )
                )

    # Технические и перегружающие слова проверяются только в публичном профиле.
    for item in p.get("forbidden_terms", []):
        if item.get("density_only"):
            continue
        for term in item.get("terms", []):
            for match in _pattern(str(term)).finditer(masked):
                findings.append(
                    _finding(
                        text,
                        finding_id=item["id"],
                        category="plain-language",
                        severity=item.get("severity", "likely"),
                        confidence=0.9,
                        message=item.get("message", "термин требует пояснения"),
                        evidence=text[match.start() : match.end()],
                        suggestion=item.get("suggestion", "объяснить понятие простыми словами"),
                        position=match.start(),
                    )
                )

    quality = p.get("quality") or {}
    paragraphs = _paragraphs(text)
    max_words = int(quality.get("max_paragraph_words", 105))
    max_sentences = int(quality.get("max_paragraph_sentences", 5))
    density_groups: dict[str, list[str]] = {}
    for item in p.get("forbidden_terms", []):
        group = item.get("density_group")
        if group:
            density_groups.setdefault(str(group), []).extend(
                str(term) for term in item.get("terms", [])
            )
    density_limits = {
        group: int(quality.get(f"max_{group}_terms_per_paragraph", 0))
        for group in density_groups
    }
    research_dense = 0
    numeric_dense = 0
    dense = 0
    for paragraph in paragraphs:
        masked_paragraph = C.mask_protected(paragraph["text"])
        for group, terms in density_groups.items():
            limit = density_limits.get(group, 0)
            if not limit:
                continue
            count = sum(len(_pattern(term).findall(masked_paragraph)) for term in terms)
            if count <= limit:
                continue
            if group == "research":
                research_dense += 1
            findings.append(
                _finding(
                    text,
                    finding_id=f"clarity.paragraph.{group}-density",
                    category="readability",
                    severity="review",
                    confidence=0.78,
                    message="в абзаце слишком много служебных терминов",
                    evidence=paragraph["text"][:180],
                    suggestion="оставить один понятный вывод, а методику и второстепенные детали вынести из основного текста",
                    position=paragraph["start"],
                    meta={"group": group, "terms": count, "limit": limit},
                )
            )
        numeric_limit = int(quality.get("max_numeric_groups_per_paragraph", 0))
        if numeric_limit:
            numeric_count = len(re.findall(r"(?<!\w)\d+(?:[.,]\d+)?", masked_paragraph))
            if numeric_count > numeric_limit:
                numeric_dense += 1
                findings.append(
                    _finding(
                        text,
                        finding_id="clarity.paragraph.numeric-density",
                        category="readability",
                        severity="review",
                        confidence=0.74,
                        message="в абзаце слишком много чисел подряд",
                        evidence=paragraph["text"][:180],
                        suggestion="оставить только числа, которые меняют вывод, и объяснить их одним предложением",
                        position=paragraph["start"],
                        meta={"numbers": numeric_count, "limit": numeric_limit},
                    )
                )
        if paragraph["words"] <= max_words and paragraph["sentences"] <= max_sentences:
            continue
        dense += 1
        findings.append(
            _finding(
                text,
                finding_id="clarity.paragraph.density",
                category="readability",
                severity="review",
                confidence=0.76,
                message="абзац совмещает слишком много материала",
                evidence=paragraph["text"][:180],
                suggestion="оставить в абзаце одну задачу: мысль, пример или вывод; соседние факты объединять вокруг нее",
                position=paragraph["start"],
                meta={"words": paragraph["words"], "sentences": paragraph["sentences"]},
            )
        )

    max_commas = int(quality.get("max_sentence_commas", 3))
    complex_sentences = 0
    for match in re.finditer(r"[^.!?…]+[.!?…]", masked):
        sentence = match.group(0)
        if len(sentence.split()) < 12:
            continue
        clauses = sentence.count(",") + sentence.count(";") + sentence.count(":")
        if clauses <= max_commas:
            continue
        complex_sentences += 1
        findings.append(
            _finding(
                text,
                finding_id="clarity.sentence.load",
                category="readability",
                severity="review",
                confidence=0.72,
                message="в предложении несколько логических поворотов",
                evidence=text[match.start() : match.end()].strip(),
                suggestion="разделить объяснение и следствие на две фразы",
                position=match.start(),
                meta={"clauses": clauses},
            )
        )

    thesis = p.get("thesis") or {}
    intro_limit = int(quality.get("intro_paragraphs", 3))
    intro = " ".join(item["text"] for item in paragraphs[:intro_limit])
    problem_markers = thesis.get("problem_markers") or []
    consequence_markers = thesis.get("consequence_markers") or []
    thesis_problem = any(re.search(pattern, intro, re.IGNORECASE) for pattern in problem_markers)
    thesis_consequence = any(
        re.search(pattern, intro, re.IGNORECASE) for pattern in consequence_markers
    )
    if quality.get("thesis_required") and (not thesis_problem or not thesis_consequence):
        findings.append(
            _finding(
                text,
                finding_id="clarity.thesis.missing",
                category="structure",
                severity="review",
                confidence=0.68,
                message="в начале не видны одновременно проблема и ее последствие",
                evidence=" ".join(intro.split())[:180],
                suggestion=thesis.get("suggestion", "сформулировать тезис и объяснить его последствие в первых абзацах"),
                position=0,
            )
        )

    metrics = {
        "paragraphs": len(paragraphs),
        "dense_paragraphs": dense,
        "research_dense_paragraphs": research_dense,
        "numeric_dense_paragraphs": numeric_dense,
        "complex_sentences": complex_sentences,
        "thesis_problem": thesis_problem,
        "thesis_consequence": thesis_consequence,
        "unlocalized_game_terms": sum(
            1 for finding in findings if finding["id"].endswith(".unlocalized")
        ),
    }
    return findings, metrics


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка понятности статьи для игроков")
    parser.add_argument("file")
    parser.add_argument("--profile", default="battlegrounds-article")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    args = parser.parse_args()
    path = Path(args.file)
    if not path.exists():
        print(f"нет файла: {path}", file=sys.stderr)
        return 2
    findings, metrics = analyze(path.read_text(encoding="utf-8"), args.profile)
    payload = {"profile": args.profile, "findings": findings, "metrics": metrics}
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if not findings:
            print("Проверяемых находок нет.")
        for finding in findings:
            print(f"[{finding['severity']}] стр.{finding['line']} {finding['message']}")
            print(f"    «{finding['evidence']}»")
            print(f"    → {finding['suggestion']}")
        print("\nИЗМЕРЕНО")
        for key, value in metrics.items():
            print(f"  {key:<24} {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
