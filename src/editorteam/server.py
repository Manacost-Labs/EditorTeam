"""HTTP-сайдкар с анализаторами.

Go-сервис оркеструет и говорит с моделью, но проверки остаются здесь:
русской морфологии для Go практически нет, а без неё теряется распознавание
падежей и коротких имён — то, что далось труднее всего.

Только стандартная библиотека: сайдкар должен подниматься где угодно.

    python3 -m editorteam.server --port 8731

    POST /analyze  {text, game, profile, mode}          -> отчёт
    POST /validate {before, after, claims_before, ...}  -> вердикт по правке
    GET  /health
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
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
    claims_before: list[dict] | None = None,
    claims_after: list[dict] | None = None,
    current_meta_epoch: str | None = None,
    current_patch: str | None = None,
    evidence_requested: bool = False,
) -> dict:
    """Затвор: что правка сделала с голосом, защищённым и ритмом.

    Это главная проверка сервиса. Модель правит, а принимается правка
    только если не потеряла того, ради чего текст пишется.
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
    mode = guide_voice.normalize_mode(mode)
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
    if sa and sb:
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
        ra
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
    if delta <= -SEVERE_SHORT_SHRINK_PCT:
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

    violations.extend(certainty_guard.scan(before, after, claims_before))
    violations.extend(
        semantic_diff.compare(
            before,
            after,
            claims_before,
            claims_after,
            current_meta_epoch,
            current_patch,
            allowed_evidence_numbers,
        )
    )
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
        },
        "norms_provisional": g.norms.provisional,
        "editorial_mode": mode,
        "corpus_version": _corpus_version(),
    }


def rules_for(game: str, profile: str | None, mode: str = "GUIDE") -> dict:
    """Правила, которые Go подставляет в запрос к модели."""
    from editorteam import rules as R

    g = G.load(game)
    prof = P.load(profile or (g.profiles[0] if g.profiles else P.DEFAULT))
    guide_voice = _scripts().sibling("guide_voice")
    clarity = _scripts().sibling("clarity")
    replace, keep = [], []
    for r in R.terminology():
        if r.decision == "auto_replace" and r.preferred:
            replace.append({"from": r.subject, "to": r.preferred})
        elif r.decision == "allowed":
            keep.append(r.subject)
    return {
        "game": g.id,
        "profile": prof.id,
        "protected": g.protected,
        "replace": replace,
        "keep": keep,
        "typography": R.typography(),
        "sections_required": [s.title for s in prof.required_sections],
        "norms": {
            "rhythm_ratio": g.norms.rhythm_ratio,
            "voice_per_1k": g.norms.voice_per_1k,
            "provisional": g.norms.provisional,
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
                        data.get("game", G.DEFAULT), data.get("profile"), data.get("mode", "GUIDE")
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
