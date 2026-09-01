#!/usr/bin/env python3
"""Прогон набора эвалов переплавки.

    python3 tools/run_evals.py --inputs-only                  # slop baseline: входы должны проваливаться
    python3 tools/run_evals.py --candidates build/evals/x/candidates   # оценить готовые результаты
    python3 tools/run_evals.py --gateway http://localhost:8080 --mode переплавка   # получить и оценить

Оценка живёт в скилле (scripts/evalscore.py), здесь — только обход кейсов,
получение кандидатов от шлюза и отчёт. Прогон с моделью не входит в CI:
это ручная или ночная проверка. Детерминированная часть — slop baseline —
живёт в tests/unit/test_evalset.py.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".claude" / "skills" / "hs-edit" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import common as C  # noqa: E402

CASES_DIR = ROOT / "tests" / "evals" / "cases"


def cases(selected: list[str] | None):
    out = []
    for d in sorted(CASES_DIR.iterdir()):
        if not (d / "case.yaml").exists():
            continue
        if selected and d.name not in selected and not any(d.name.startswith(s) for s in selected):
            continue
        out.append(d)
    return out


def fetch_candidate(gateway: str, case: dict, mode: str, timeout: int) -> str:
    payload = {
        "text": case["_input"],
        "game": case.get("game", "hearthstone"),
        "profile": case.get("profile", "constructed-guide"),
        "mode": mode,
        "editorial_mode": case.get("editorial_mode", "GUIDE"),
    }
    req = urllib.request.Request(
        gateway.rstrip("/") + "/edit",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — адрес задаёт пользователь
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("text", ""), data


def markdown_report(rows: list[dict]) -> str:
    head = ("| кейс | ок | голос | ритм | кор/дл % | маркеров /10к | author | покрытие | разделы | что провалено |\n"
            "|---|---|---|---|---|---|---|---|---|---|\n")
    lines = []
    for r in rows:
        m = r["candidate"]["metrics"] if r.get("candidate") else r["input"]["metrics"]
        target = r.get("candidate") or r["input"]
        cov = m.get("coverage") or {}
        cov_s = "/".join(str(cov.get(k, "—")) for k in ("numbers", "cards", "negations", "facts"))
        sec = m.get("sections") or {}
        lines.append(
            f"| {r['id']} | {'✓' if target['accepted'] else '✗'} | {m.get('soul_per_1k', '—')} | "
            f"{m.get('rhythm_ratio', '—')} | {m.get('short_pct', '—')}/{m.get('long_pct', '—')} | "
            f"{m.get('markers_per_10k', {}).get('total', '—')} | {m.get('author', '—')} | {cov_s} | "
            f"{len(sec.get('present', []))}{'+' if sec.get('order_ok', True) else '!'} | "
            f"{', '.join(target['failed']) or '—'} |"
        )
    return head + "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Прогон эвалов переплавки")
    ap.add_argument("--inputs-only", action="store_true", help="оценить входы: slop baseline")
    ap.add_argument("--candidates", help="папка с <case-id>.md")
    ap.add_argument("--gateway", help="адрес Go-шлюза, например http://localhost:8080")
    ap.add_argument("--mode", default="переплавка")
    ap.add_argument("--case", action="append", help="id кейса или префикс; можно несколько")
    ap.add_argument("--format", choices=["table", "json"], default="table")
    ap.add_argument("--out", help="папка отчёта (по умолчанию build/evals/<время>)")
    ap.add_argument("--fail-under", type=float, help="код 1, если доля принятых ниже")
    ap.add_argument("--baseline", help="report.json прошлого прогона для сравнения")
    ap.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args()
    C.ensure_venv("pymorphy3")
    evalscore = C.sibling("evalscore")
    thresholds = evalscore.load_thresholds()

    if not (args.inputs_only or args.candidates or args.gateway):
        ap.error("нужен один из режимов: --inputs-only, --candidates DIR или --gateway URL")
    source = "inputs" if args.inputs_only else ("candidates" if args.candidates else "gateway")
    out_dir = (Path(args.out) if args.out else ROOT / "build" / "evals" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")).resolve()
    cand_dir = out_dir / "candidates"

    rows = []
    model = None
    for d in cases(args.case):
        case = evalscore.load_case(d)
        row = {"id": case["id"], "profile": case.get("profile"),
               "input": evalscore.score(case["_input"], case, thresholds, is_input=True)}
        candidate_text = None
        if args.candidates:
            p = Path(args.candidates) / f"{case['id']}.md"
            if p.exists():
                candidate_text = p.read_text(encoding="utf-8")
            else:
                row["note"] = "кандидата нет"
        elif args.gateway:
            try:
                candidate_text, data = fetch_candidate(args.gateway, case, args.mode, args.timeout)
                model = data.get("model", model)
                row["gateway"] = {"accepted": data.get("accepted"), "attempts": len(data.get("attempts", [])),
                                  "missing_sections": data.get("missing_sections", [])}
                cand_dir.mkdir(parents=True, exist_ok=True)
                (cand_dir / f"{case['id']}.md").write_text(candidate_text, encoding="utf-8")
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                row["note"] = f"шлюз: {exc}"
        if candidate_text is not None:
            row["candidate"] = evalscore.score(candidate_text, case, thresholds)
        rows.append(row)
        if args.format == "table":
            target = row.get("candidate") or row["input"]
            label = "кандидат" if row.get("candidate") else "вход"
            print(f"{case['id']:<34} {label:<8} {'PASS' if target['accepted'] else 'FAIL':<5} "
                  f"{', '.join(target['failed'])}")

    scored = [r for r in rows if r.get("candidate")] if source != "inputs" else rows
    accepted = sum(1 for r in scored if (r.get("candidate") or r["input"])["accepted"])
    share = round(accepted / len(scored), 3) if scored else 0.0
    failed_by_check: dict[str, int] = {}
    for r in scored:
        for check in (r.get("candidate") or r["input"])["failed"]:
            failed_by_check[check] = failed_by_check.get(check, 0) + 1
    report = {
        "evals_schema_version": evalscore.EVALS_SCHEMA_VERSION,
        "run": {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source": source,
                "model": model, "corpus_version": C.corpus_manifest().get("current_version", "legacy-v1")},
        "cases": rows,
        "summary": {"cases": len(scored), "accepted": accepted, "share": share,
                    "failed_by_check": dict(sorted(failed_by_check.items()))},
    }
    if args.baseline:
        base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        before = {r["id"]: (r.get("candidate") or r["input"])["accepted"] for r in base.get("cases", [])}
        regressions = [r["id"] for r in scored
                       if before.get(r["id"]) and not (r.get("candidate") or r["input"])["accepted"]]
        report["summary"]["regressions"] = regressions
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "report.md").write_text(markdown_report(rows), encoding="utf-8")
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        shown = out_dir.relative_to(ROOT) if out_dir.is_relative_to(ROOT) else out_dir
        print(f"\nпринято {accepted} из {len(scored)} ({share:.0%}); отчёт: {shown}")
        if report["summary"].get("regressions"):
            print("регрессии: " + ", ".join(report["summary"]["regressions"]))
    if args.fail_under is not None and share < args.fail_under:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
