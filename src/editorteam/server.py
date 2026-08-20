"""HTTP-сайдкар с анализаторами.

Go-сервис оркеструет и говорит с моделью, но проверки остаются здесь:
русской морфологии для Go практически нет, а без неё теряется распознавание
падежей и коротких имён — то, что далось труднее всего.

Только стандартная библиотека: сайдкар должен подниматься где угодно.

    python3 -m editorteam.server --port 8731

    POST /analyze  {text, game, profile}          -> отчёт
    POST /validate {before, after, game, profile} -> вердикт по правке
    GET  /health
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from editorteam import games as G
from editorteam import profiles as P
from editorteam.finding import Finding, Report

SKILL_SCRIPTS = Path(__file__).resolve().parents[2] / ".claude" / "skills" / "hs-edit" / "scripts"
MAX_BODY = 2 * 1024 * 1024


def _scripts():
    if str(SKILL_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SKILL_SCRIPTS))
    import common as C

    return C


def analyze(text: str, game: str, profile: str | None) -> dict:
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
            report.add(Finding(
                id=f"markers.{hit['id']}", analyzer="markers", category=hit["action"],
                severity=sev, confidence=0.6 if sev == "review" else 0.8,
                message=hit["name"], evidence=hit["text"], suggestion=hit["fix"],
                line=hit["line"], profile=prof.id))
    else:
        report.skipped.append("markers")

    skip = g.skip_reason("cards")
    if prof.enabled("cards") and not skip:
        cards = C.sibling("cards")
        cards.ensure_pymorphy()
        db = C.card_db()
        idx = cards.Index(db["карты"], C.morph())
        for (was, off), n in cards.check_apostrophes(text, idx).items():
            report.add(Finding(id="cards.apostrophe", analyzer="cards",
                               category="localization", severity="error",
                               message=f"апостроф в названии ({g.names_kind})",
                               evidence=was, suggestion=off, meta={"count": n}))
        for (was, off), n in cards.check_dashes(text, db["карты"]).items():
            report.add(Finding(id="cards.dash", analyzer="cards",
                               category="localization", severity="error",
                               message=f"тире в названии ({g.names_kind})",
                               evidence=was, suggestion=off, meta={"count": n}))
    else:
        report.skipped.append("cards")
        if skip:
            report.notes.append(f"сверка имён отключена: {skip}")

    if prof.enabled("consistency"):
        cons = C.sibling("consistency")
        for label, forms in cons.check_variants(C.mask_protected(text)):
            report.add(Finding(id="consistency.variants", analyzer="consistency",
                               category="spelling", severity="likely", confidence=0.85,
                               message=f"разнобой: {label}",
                               evidence=", ".join(f"{f} ×{c}" for f, c in forms[:4]),
                               suggestion="выбрать одно написание"))
    else:
        report.skipped.append("consistency")

    if prof.enabled("structure"):
        st = C.sibling("structure")
        heads = [h.lower() for _, h in st.headings(text)]
        for section in prof.required_sections:
            if not any(h in section.variants for h in heads):
                report.add(Finding(id=f"structure.missing.{section.id}",
                                   analyzer="structure", category="structure",
                                   severity="likely", confidence=0.7,
                                   message=f"нет обязательного раздела «{section.title}»",
                                   suggestion="; ".join(section.variants[:3])))
    else:
        report.skipped.append("structure")

    report.metrics.update(measure(text, g, prof))
    d = report.to_dict()
    d["game"] = g.id
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


def validate(before: str, after: str, game: str, profile: str | None) -> dict:
    """Затвор: что правка сделала с голосом, защищённым и ритмом.

    Это главная проверка сервиса. Модель правит, а принимается правка
    только если не потеряла того, ради чего текст пишется.
    """
    C = _scripts()
    g = G.load(game)
    soul = C.sibling("soul")
    rhythm = C.sibling("rhythm")
    report_mod = C.sibling("report")

    sa, wa = soul.measure(before)
    sb, wb = soul.measure(after)
    ra, rb = rhythm.measure(before), rhythm.measure(after)
    gone = report_mod.protected_lost(before, after)

    violations = []
    if sa and sb:
        for name in soul.SIGNALS:
            if soul.classify(sa[name], sb[name], wa, wb) == "сигналы удалены":
                violations.append({
                    "kind": "voice_lost", "signal": name,
                    "was": sa[name]["n"], "now": sb[name]["n"],
                    "message": f"вычищено живое: {name} — {sa[name]['n']} → {sb[name]['n']} мест",
                })
    for kind, items in gone.items():
        violations.append({
            "kind": "protected_lost", "signal": kind,
            "message": f"пропало защищённое ({kind}): "
                       + ", ".join(list(items)[:5]),
        })
    if ra and rb and rb["ratio"] < ra["ratio"] - 0.03:
        violations.append({
            "kind": "rhythm_flattened",
            "message": f"ритм выровнен: {ra['ratio']:.2f} → {rb['ratio']:.2f}",
        })

    delta = 100 * (len(after) - len(before)) / max(1, len(before))
    if delta < -5:
        violations.append({
            "kind": "text_shrunk",
            "message": f"текст усох на {abs(delta):.0f}% — вероятно пропали мысли",
        })

    return {
        "accepted": not violations,
        "violations": violations,
        "metrics": {
            "length_change_pct": round(delta, 1),
            "rhythm_before": round(ra["ratio"], 3) if ra else None,
            "rhythm_after": round(rb["ratio"], 3) if rb else None,
            "voice_before": round(sum(v["per1k"] for v in sa.values()), 1) if sa else None,
            "voice_after": round(sum(v["per1k"] for v in sb.values()), 1) if sb else None,
        },
        "norms_provisional": g.norms.provisional,
    }


def rules_for(game: str, profile: str | None) -> dict:
    """Правила, которые Go подставляет в запрос к модели."""
    from editorteam import rules as R

    g = G.load(game)
    prof = P.load(profile or (g.profiles[0] if g.profiles else P.DEFAULT))
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
            self._send(200, {"ok": True, "games": G.available(),
                             "profiles": P.available()})
        else:
            self._send(404, {"error": "нет такого пути"})

    def do_POST(self):
        data = self._read()
        if data is None:
            self._send(400, {"error": "нужен JSON в теле запроса"})
            return
        try:
            if self.path == "/analyze":
                self._send(200, analyze(data.get("text", ""),
                                        data.get("game", G.DEFAULT),
                                        data.get("profile")))
            elif self.path == "/validate":
                self._send(200, validate(data.get("before", ""), data.get("after", ""),
                                         data.get("game", G.DEFAULT), data.get("profile")))
            elif self.path == "/rules":
                self._send(200, rules_for(data.get("game", G.DEFAULT), data.get("profile")))
            else:
                self._send(404, {"error": "нет такого пути"})
        except (G.GameError, P.ProfileError) as e:
            self._send(400, {"error": str(e)})
        except Exception as e:                       # noqa: BLE001
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
