"""Единый CLI: `editor-team`.

Обёртка над анализаторами скилла — старые скрипты продолжают работать
как раньше, здесь добавлены профили, JSON и единый exit code.

Флаги объявляются только те, что действительно что-то делают.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from editorteam import corpus as Corpus
from editorteam import profiles as P
from editorteam import rules
from editorteam.bg_import import import_directory, import_guides_directory
from editorteam.corpus_learning import CorpusError, CorpusStore
from editorteam.finding import Finding, Report, exit_code

ROOT = Path(__file__).resolve().parents[2]
CORPUS_COLLECTIONS = ["main", "bg", "archive"]
REPO_SKILL_SCRIPTS = ROOT / ".claude" / "skills" / "hs-edit" / "scripts"
SKILL_SCRIPTS = REPO_SKILL_SCRIPTS if REPO_SKILL_SCRIPTS.exists() else ROOT / "scripts"


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

    # Evidence управляет смыслом совета, но в GUIDE не просачивается
    # в читательский текст как пересказ исследования.
    guide_voice = C.sibling("guide_voice")
    for hit in guide_voice.scan(text, args.mode, args.evidence_requested):
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
                profile=profile.id,
            )
        )

    # Понятность публичной статьи: роли игровых терминов, нагрузка абзацев
    # и наличие тезиса. Для старых профилей анализатор выключен, поэтому их
    # JSON и пороги остаются совместимыми.
    if profile.enabled("clarity"):
        clarity = C.sibling("clarity")
        clarity_findings, clarity_metrics = clarity.analyze(text, profile.id)
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
                    profile=profile.id,
                    meta=hit.get("meta", {}),
                )
            )
        report.metrics.update({f"clarity_{key}": value for key, value in clarity_metrics.items()})

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
    args.mode = "GUIDE"
    args.evidence_requested = False
    return audit(args)


def validate_edit(args) -> int:
    from editorteam.server import validate

    before = _read(Path(args.before))
    after = _read(Path(args.after))
    claims_before = json.loads(_read(Path(args.claims_before))) if args.claims_before else None
    claims_after = (
        json.loads(_read(Path(args.claims_after))) if args.claims_after else claims_before
    )
    result = validate(
        before,
        after,
        args.game,
        args.profile,
        mode=args.mode,
        claims_before=claims_before,
        claims_after=claims_after,
        current_meta_epoch=args.current_meta_epoch,
        current_patch=args.current_patch,
    )
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("PASS" if result["accepted"] else "REJECTED")
        for item in result["violations"]:
            print(f"  [{item['kind']}] {item['message']}")
        for item in result.get("warnings", []):
            print(f"  [REVIEW:{item['kind']}] {item['message']}")
    return 0 if result["accepted"] else 1


def corpus_validate(args) -> int:
    C = _scripts()
    report = Report(document=str(C.CORPUS), profile="corpus")
    for pr in Corpus.validate():
        report.add(
            Finding(
                id=f"corpus.{pr.kind}",
                analyzer="corpus",
                category="data",
                severity=pr.severity,
                message=pr.message,
                evidence=pr.document,
            )
        )
    st = Corpus.stats()
    report.metrics.update(
        {
            "documents": st["documents"],
            "words": st["words"],
            "unknown_values": st["unknown_values"],
        }
    )
    report.notes.append(f"жанры: {st['by_genre']}; режимы: {st['by_mode']}")
    report.notes.append(
        "значения unknown — не дефект: данных о датах и патчах в исходниках нет, "
        "и выдумывать их нельзя"
    )
    _emit(report, args)
    return exit_code(report, args.fail_on)


def corpus_split(args) -> int:
    """Детерминированное разбиение: калибровка и holdout."""
    parts = Corpus.split()
    if args.format == "json":
        import json as _json

        print(_json.dumps(parts, ensure_ascii=False, indent=2))
        return 0
    print(f"\nкалибровка: {len(parts['calibration'])} документов")
    print(f"holdout:    {len(parts['holdout'])} документов")
    print("  " + ", ".join(parts["holdout"]))
    print("\n  Разбиение по идентификатору, а не случайное: иначе калибровка")
    print("  меняется от запуска к запуску и числа перестают сходиться.")
    return 0


def _corpus_store(collection: str = "main") -> CorpusStore:
    if collection == "bg":
        return CorpusStore(ROOT, corpus_dir_name="corpus-bg", include_legacy=False)
    if collection == "archive":
        return CorpusStore(ROOT, corpus_dir_name="corpus-archive", include_legacy=False)
    return CorpusStore(ROOT)


def _corpus_output(data: dict | list, args) -> int:
    if args.format == "json":
        print(json.dumps(data, ensure_ascii=False, indent=2))
    elif isinstance(data, list):
        for item in data:
            print(f"{item['version']:<8} {item['guides']:>4} guides  {item['action']}")
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def corpus_add(args) -> int:
    try:
        data = _corpus_store(args.collection).add(
            Path(args.file),
            published_at=args.published_at,
            patch=args.patch,
            author=args.author,
            tags=args.tags,
            source=args.source,
            genre=args.genre,
            approve=args.approve,
            guide_id=args.id,
        )
    except CorpusError as exc:
        payload = {"error": exc.code, "message": str(exc)}
        if args.format == "json":
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"{exc.code}: {exc}", file=sys.stderr)
        return 2
    return _corpus_output(data, args)


def corpus_approve(args) -> int:
    return _corpus_mutation(args, "approve", args.guide_id)


def corpus_remove(args) -> int:
    return _corpus_mutation(args, "remove", args.guide_id)


def corpus_reject(args) -> int:
    return _corpus_mutation(args, "reject", args.guide_id)


def corpus_rollback(args) -> int:
    return _corpus_mutation(args, "rollback", args.version)


def _corpus_mutation(args, method: str, value: str) -> int:
    try:
        data = getattr(_corpus_store(args.collection), method)(value)
    except CorpusError as exc:
        if args.format == "json":
            print(json.dumps({"error": exc.code, "message": str(exc)}, ensure_ascii=False))
        else:
            print(f"{exc.code}: {exc}", file=sys.stderr)
        return 2
    return _corpus_output(data, args)


def corpus_versions(args) -> int:
    return _corpus_output(_corpus_store(args.collection).versions(), args)


def corpus_inspect(args) -> int:
    return _corpus_output(_corpus_store(args.collection).inspect(), args)


def corpus_compare(args) -> int:
    try:
        data = _corpus_store(args.collection).compare(args.before_version, args.after_version)
    except CorpusError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 2
    return _corpus_output(data, args)


def corpus_import_bg(args) -> int:
    try:
        data = import_directory(Path(args.directory), _corpus_store("bg"))
    except CorpusError as exc:
        if args.format == "json":
            print(json.dumps({"error": exc.code, "message": str(exc)}, ensure_ascii=False))
        else:
            print(f"{exc.code}: {exc}", file=sys.stderr)
        return 2
    return _corpus_output(data, args)


def corpus_import_guides(args) -> int:
    try:
        data = import_guides_directory(Path(args.directory), _corpus_store("archive"))
    except CorpusError as exc:
        if args.format == "json":
            print(json.dumps({"error": exc.code, "message": str(exc)}, ensure_ascii=False))
        else:
            print(f"{exc.code}: {exc}", file=sys.stderr)
        return 2
    return _corpus_output(data, args)


def config_validate(args) -> int:
    problems = rules.validate()
    # В дополнение к словарю и типографике проверяем справочник игровых
    # понятий и правила понятности публичных материалов.
    clarity = _scripts().sibling("clarity")
    problems.extend(clarity.validate_config())
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
    a.add_argument("--mode", choices=["GUIDE", "ANALYSIS", "REPORT"], default="GUIDE")
    a.add_argument(
        "--evidence-requested",
        action="store_true",
        help="автор явно попросил показать evidence в тексте",
    )
    a.set_defaults(func=audit)

    ve = sub.add_parser("validate-edit", help="затвор смысла и confidence", parents=[common])
    ve.add_argument("before")
    ve.add_argument("after")
    ve.add_argument("--game", default="hearthstone")
    ve.add_argument("--profile", choices=P.available())
    ve.add_argument("--mode", choices=["GUIDE", "ANALYSIS", "REPORT"], default="GUIDE")
    ve.add_argument("--claims-before")
    ve.add_argument("--claims-after")
    ve.add_argument("--current-meta-epoch")
    ve.add_argument("--current-patch")
    ve.set_defaults(func=validate_edit)

    c = sub.add_parser("cards", help="только сверка названий карт", parents=[common])
    c.add_argument("file")
    c.add_argument("--profile", choices=P.available())
    c.set_defaults(func=cards_cmd)

    corp = sub.add_parser("corpus", help="проверки корпуса", parents=[common])
    corp_sub = corp.add_subparsers(dest="corpus_cmd", required=True)
    cv = corp_sub.add_parser("validate", parents=[common])
    cv.set_defaults(func=corpus_validate)
    cs = corp_sub.add_parser(
        "split", parents=[common], help="детерминированное разбиение на калибровку и holdout"
    )
    cs.set_defaults(func=corpus_split)
    ca = corp_sub.add_parser("add", parents=[common], help="добавить candidate или approved guide")
    ca.add_argument("file")
    ca.add_argument("--id")
    ca.add_argument("--published-at", required=True)
    ca.add_argument("--patch", required=True)
    ca.add_argument("--author", default="manacost")
    ca.add_argument("--tag", dest="tags", action="append", default=[])
    ca.add_argument("--source", default="published")
    ca.add_argument("--genre", choices=P.available(), default="constructed-guide")
    ca.add_argument("--approve", action="store_true", help="явное решение человека")
    ca.add_argument("--collection", choices=CORPUS_COLLECTIONS, default="main")
    ca.set_defaults(func=corpus_add)
    cap = corp_sub.add_parser("approve", parents=[common], help="активировать candidate")
    cap.add_argument("guide_id")
    cap.add_argument("--collection", choices=CORPUS_COLLECTIONS, default="main")
    cap.set_defaults(func=corpus_approve)
    cr = corp_sub.add_parser(
        "remove", parents=[common], help="архивировать guide и пересчитать baseline"
    )
    cr.add_argument("guide_id")
    cr.add_argument("--collection", choices=CORPUS_COLLECTIONS, default="main")
    cr.set_defaults(func=corpus_remove)
    crj = corp_sub.add_parser("reject", parents=[common], help="отклонить candidate")
    crj.add_argument("guide_id")
    crj.add_argument("--collection", choices=CORPUS_COLLECTIONS, default="main")
    crj.set_defaults(func=corpus_reject)
    crv = corp_sub.add_parser("versions", parents=[common])
    crv.add_argument("--collection", choices=CORPUS_COLLECTIONS, default="main")
    crv.set_defaults(func=corpus_versions)
    crb = corp_sub.add_parser("rollback", parents=[common])
    crb.add_argument("version")
    crb.add_argument("--collection", choices=CORPUS_COLLECTIONS, default="main")
    crb.set_defaults(func=corpus_rollback)
    ci = corp_sub.add_parser("inspect", parents=[common])
    ci.add_argument("--collection", choices=CORPUS_COLLECTIONS, default="main")
    ci.set_defaults(func=corpus_inspect)
    cc = corp_sub.add_parser("compare", parents=[common])
    cc.add_argument("before_version")
    cc.add_argument("after_version")
    cc.add_argument("--collection", choices=CORPUS_COLLECTIONS, default="main")
    cc.set_defaults(func=corpus_compare)
    cib = corp_sub.add_parser(
        "import-bg",
        parents=[common],
        help="импортировать папку TXT о Полях сражений как отдельные candidates",
    )
    cib.add_argument("directory")
    cib.set_defaults(func=corpus_import_bg)
    cig = corp_sub.add_parser(
        "import-guides",
        parents=[common],
        help="импортировать архив обычных TXT-гайдов с PDF/TXT-dedup",
    )
    cig.add_argument("directory")
    cig.set_defaults(func=corpus_import_guides)

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
