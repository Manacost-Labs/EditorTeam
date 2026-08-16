"""Единый CLI: `editor-team`.

Обёртка над анализаторами скилла — старые скрипты продолжают работать
как раньше, здесь добавлены профили, JSON и единый exit code.

Флаги объявляются только те, что действительно что-то делают.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from editorteam import profiles as P
from editorteam import rules
from editorteam.finding import Finding, Report, exit_code

SKILL_SCRIPTS = Path(__file__).resolve().parents[2] / ".claude" / "skills" / "hs-edit" / "scripts"


def _scripts():
    """Подключить анализаторы скилла, не ломая их расположение."""
    if str(SKILL_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SKILL_SCRIPTS))
    import common as C

    return C


def _read(path: Path) -> str:
    if not path.exists():
        print(f"нет файла: {path}", file=sys.stderr)
        raise SystemExit(2)
    # newline="" не нужен: CRLF схлопывается в LF единообразно на всех системах
    return path.read_text(encoding="utf-8")


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def audit(args) -> int:
    C = _scripts()
    text = _read(Path(args.file))
    profile_name = args.profile
    confidence = None
    if not profile_name:
        profile_name, confidence = P.detect(text)
    profile = P.load(profile_name)

    report = Report(document=str(args.file), profile=profile.id)
    if confidence is not None:
        report.notes.append(
            f"профиль определён автоматически: {profile.id} (уверенность {confidence}); "
            f"указать явно — --profile"
        )
    words = len(text.split())
    if words < profile.min_words:
        report.notes.append(
            f"текст короче минимума профиля ({words} из {profile.min_words} слов) — "
            f"частотные метрики шумят, категоричных выводов не будет"
        )

    # маркеры шаблона
    if profile.enabled("markers"):
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
                    profile=profile.id,
                )
            )
    else:
        report.skipped.append("markers")

    # названия карт — точная сверка со справочником
    if profile.enabled("cards"):
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
                    message="апостроф в названии карты",
                    evidence=was,
                    suggestion=off,
                    profile=profile.id,
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
                    message="тире в названии карты",
                    evidence=was,
                    suggestion=off,
                    profile=profile.id,
                    meta={"count": n},
                )
            )
    else:
        report.skipped.append("cards")

    # согласованность
    if profile.enabled("consistency"):
        cons = C.sibling("consistency")
        for label, forms in cons.check_variants(C.mask_protected(text), strict=args.strict):
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
                    profile=profile.id,
                )
            )
        for card, stance in cons.check_advice(text).items():
            report.add(
                Finding(
                    id="consistency.advice",
                    analyzer="consistency",
                    category="advice",
                    severity="review",
                    confidence=0.4,
                    message=f"советы по карте «{card}» расходятся",
                    evidence=stance["оставлять"][0][:90],
                    suggestion="проверить: возможно, речь о разных матч-апах",
                    profile=profile.id,
                )
            )
    else:
        report.skipped.append("consistency")

    # структура — по профилю
    if profile.enabled("structure"):
        st = C.sibling("structure")
        heads = [h.lower() for _, h in st.headings(text)]
        for section in profile.required_sections:
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
                        profile=profile.id,
                        meta={"corpus_share": section.corpus_share},
                    )
                )
        if profile.require_classes:
            found = st.find_blocks(st.headings(text))
            mu = st.check_matchups(text, st.headings(text), found)
            if mu and mu[1]:
                report.add(
                    Finding(
                        id="structure.matchups",
                        analyzer="structure",
                        category="coverage",
                        severity="review",
                        confidence=0.8,
                        message=f"матч-апы без {len(mu[1])} классов",
                        evidence=", ".join(mu[1]),
                        profile=profile.id,
                    )
                )
    else:
        report.skipped.append("structure")

    # измеряемые величины
    if profile.enabled("rhythm"):
        r = C.sibling("rhythm").measure(text)
        if r:
            report.metrics["rhythm_ratio"] = round(r["ratio"], 3)
            report.metrics["sentence_mean"] = round(r["mean"], 1)
    else:
        report.skipped.append("rhythm")

    if profile.enabled("soul"):
        soul = C.sibling("soul")
        s, w = soul.measure(text)
        if s:
            report.metrics["voice_per_1k"] = round(sum(v["per1k"] for v in s.values()), 1)
            report.metrics["voice_norm"] = soul.TOTAL_MED
    else:
        report.skipped.append("soul")

    report.metrics["words"] = words
    _emit(report, args)
    return exit_code(report, args.fail_on)


def cards_cmd(args) -> int:
    args.profile = args.profile or "constructed-guide"
    args.strict = False
    return audit(args)


def corpus_validate(args) -> int:
    C = _scripts()
    files = C.corpus_files()
    report = Report(document=str(C.CORPUS), profile="corpus")
    seen = {}
    for f in files:
        meta, text = C.read_document(f)
        if not text.strip():
            report.add(
                Finding(
                    id="corpus.empty",
                    analyzer="corpus",
                    category="data",
                    severity="error",
                    message="пустой файл",
                    evidence=f.name,
                )
            )
        key = C.guide_name(f)
        if key in seen:
            report.add(
                Finding(
                    id="corpus.duplicate",
                    analyzer="corpus",
                    category="data",
                    severity="likely",
                    message="дублирующееся имя",
                    evidence=key,
                )
            )
        seen[key] = f
    report.metrics["documents"] = len(files)
    report.metrics["words"] = sum(len(C.body(f).split()) for f in files)
    _emit(report, args)
    return exit_code(report, args.fail_on)


def config_validate(args) -> int:
    problems = rules.validate()
    report = Report(document="config/", profile="config")
    for p in problems:
        report.add(
            Finding(
                id="config.conflict",
                analyzer="config",
                category="rules",
                severity="error",
                message=p,
            )
        )
    _emit(report, args)
    return exit_code(report, args.fail_on)


def profiles_cmd(args) -> int:
    for name in P.available():
        p = P.load(name)
        req = ", ".join(s.title for s in p.required_sections) or "—"
        print(f"{p.id:<22} {p.title}")
        print(f"{'':<22} обязательные разделы: {req}")
        print(f"{'':<22} классы в матч-апах: {'да' if p.require_classes else 'нет'}")
    return 0


def _emit(report: Report, args) -> None:
    if args.format == "json":
        print(report.to_json())
        return
    print(f"\n{report.document}   профиль: {report.profile}")
    for note in report.notes:
        print(f"  ! {note}")
    if report.skipped:
        print(f"  пропущены анализаторы: {', '.join(report.skipped)}")
    if not report.findings:
        print("\n  Проверяемых находок нет.")
    for f in sorted(report.findings, key=lambda x: (x.severity, x.line or 0)):
        where = f" стр.{f.line}" if f.line else ""
        print(f"\n  [{f.severity}]{where} {f.message}")
        if f.evidence:
            print(f"      «{f.evidence}»")
        if f.suggestion:
            print(f"      → {f.suggestion}")
    if report.metrics:
        print("\n  ИЗМЕРЕНО")
        for k, v in report.metrics.items():
            print(f"    {k:<18} {v}")
    print("\n  Точная ошибка (error), вероятная (likely) и сигнал редактору (review)")
    print("  различаются: review — приглашение посмотреть, а не дефект.")


def _common() -> argparse.ArgumentParser:
    """Общие флаги. Добавляются и до подкоманды, и после неё: пользователь
    естественно пишет `audit file --format json`, а не наоборот."""
    # default=SUPPRESS обязателен: иначе подпарсер перезапишет значение,
    # заданное до подкоманды, своим None
    c = argparse.ArgumentParser(add_help=False)
    c.add_argument("--format", choices=["text", "json"], default=argparse.SUPPRESS)
    c.add_argument(
        "--fail-on",
        dest="fail_on",
        choices=["error", "likely", "review", "info"],
        default=argparse.SUPPRESS,
        help="при какой серьёзности возвращать код 1",
    )
    return c


def build_parser() -> argparse.ArgumentParser:
    common = _common()
    ap = argparse.ArgumentParser(
        prog="editor-team", parents=[common], description="Редактура материалов по Hearthstone"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("audit", help="полный разбор материала", parents=[common])
    a.add_argument("file")
    a.add_argument(
        "--profile", choices=P.available(), help="жанр; без него определяется автоматически"
    )
    a.add_argument("--strict", action="store_true", help="включить слабые сигналы согласованности")
    a.set_defaults(func=audit)

    c = sub.add_parser("cards", help="только сверка названий карт", parents=[common])
    c.add_argument("file")
    c.add_argument("--profile", choices=P.available())
    c.set_defaults(func=cards_cmd)

    corp = sub.add_parser("corpus", help="проверки корпуса", parents=[common])
    corp_sub = corp.add_subparsers(dest="corpus_cmd", required=True)
    cv = corp_sub.add_parser("validate", parents=[common])
    cv.set_defaults(func=corpus_validate)

    cfg = sub.add_parser("config", help="проверки конфигурации правил", parents=[common])
    cfg_sub = cfg.add_subparsers(dest="config_cmd", required=True)
    cfgv = cfg_sub.add_parser("validate", parents=[common])
    cfgv.set_defaults(func=config_validate)

    pr = sub.add_parser("profiles", help="список жанровых профилей", parents=[common])
    pr.set_defaults(func=profiles_cmd)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    # флаг мог стоять и до подкоманды, и после — берём то, что задано
    args.format = getattr(args, "format", None) or "text"
    args.fail_on = getattr(args, "fail_on", None) or "error"
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
