#!/usr/bin/env python3
"""Сканер маркеров неестественного текста для русскоязычных материалов по Hearthstone.

Использование:
    python3 markers.py черновик.md
    python3 markers.py черновик.md --format json
    python3 markers.py черновик.md --only remove,rewrite

Скрипт не выносит приговор и не правит текст. Он показывает кандидатов —
решение по каждому принимает редактор, глядя на контекст.
"""

import argparse
import json
import re
import sys
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "markers.json"

ACTION_LABEL = {
    "remove": "убрать",
    "rewrite": "переписать",
    "review": "смотреть",
}
ACTION_ORDER = {"remove": 0, "rewrite": 1, "review": 2}


def load_patterns():
    data = json.loads(ASSETS.read_text(encoding="utf-8"))
    out = []
    for p in data["patterns"]:
        try:
            p["_re"] = re.compile(p["re"], re.IGNORECASE | re.MULTILINE | re.UNICODE)
        except re.error as e:
            print(f"! битый шаблон {p['id']}: {e}", file=sys.stderr)
            continue
        out.append(p)
    return out


def mask_protected(text):
    """Гасит фрагменты, которые не редактируются: код, цитаты, ссылки, списки карт.

    Заменяет их пробелами, чтобы смещения символов не поехали.
    """
    def blank(m):
        return re.sub(r"\S", " ", m.group(0))

    text = re.sub(r"```.*?```", blank, text, flags=re.DOTALL)
    text = re.sub(r"`[^`\n]+`", blank, text)
    text = re.sub(r"^>.*$", blank, text, flags=re.MULTILINE)
    text = re.sub(r"https?://\S+", blank, text)
    text = re.sub(r"«[^»]{0,120}»", blank, text)          # цитаты
    text = re.sub(r"^\s*AAECA\S+\s*$", blank, text, flags=re.MULTILINE)  # коды колод
    return text


def paragraph_index(text):
    """Границы абзацев -> номер абзаца по смещению."""
    bounds, start = [], 0
    for block in re.split(r"(\n\s*\n)", text):
        end = start + len(block)
        if block.strip():
            bounds.append((start, end))
        start = end
    return bounds


def which_paragraph(bounds, pos):
    for i, (a, b) in enumerate(bounds):
        if a <= pos < b:
            return i
    return -1


def scan(text, patterns, only=None):
    masked = mask_protected(text)
    bounds = paragraph_index(text)
    line_starts = [0] + [m.end() for m in re.finditer(r"\n", text)]

    def line_of(pos):
        lo, hi = 0, len(line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if line_starts[mid] <= pos:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    raw = []
    for p in patterns:
        if only and p["action"] not in only:
            continue
        for m in p["_re"].finditer(masked):
            if not m.group(0).strip():
                continue
            raw.append({
                "id": p["id"],
                "name": p["name"],
                "action": p["action"],
                "fix": p["fix"],
                "note": p.get("note", ""),
                "min_hits": p.get("min_hits", 1),
                "pos": m.start(),
                "line": line_of(m.start()),
                "para": which_paragraph(bounds, m.start()),
                "text": " ".join(text[m.start():m.end()].split())[:90],
            })

    # review-сигналы значимы только кластером внутри одного абзаца
    counts = {}
    for f in raw:
        counts[(f["id"], f["para"])] = counts.get((f["id"], f["para"]), 0) + 1

    findings = [f for f in raw if counts[(f["id"], f["para"])] >= f["min_hits"]]
    findings.sort(key=lambda f: (ACTION_ORDER[f["action"]], f["line"]))
    return findings


def report(findings, path):
    if not findings:
        print(f"{path}: маркеров не найдено.")
        return 0

    by_action = {}
    for f in findings:
        by_action.setdefault(f["action"], []).append(f)

    print(f"\n{path} — найдено {len(findings)} кандидатов\n")
    for action in ("remove", "rewrite", "review"):
        group = by_action.get(action)
        if not group:
            continue
        print(f"── {ACTION_LABEL[action].upper()} ({len(group)})")
        seen_fix = set()
        for f in group:
            print(f"  стр.{f['line']:>4}  {f['name']}: «{f['text']}»")
            if f["id"] not in seen_fix:
                print(f"          → {f['fix']}")
                if f["note"]:
                    print(f"          ! {f['note']}")
                seen_fix.add(f["id"])
        print()
    print("Это кандидаты, а не ошибки. Каждый — проверить в контексте;")
    print("авторский приём, цитату и термин Hearthstone оставить как есть.")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Маркеры неестественного текста (рус.)")
    ap.add_argument("file", help="путь к .md или .txt")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    ap.add_argument("--only", help="через запятую: remove,rewrite,review")
    args = ap.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"нет файла: {path}", file=sys.stderr)
        return 2

    text = path.read_text(encoding="utf-8")
    only = set(args.only.split(",")) if args.only else None
    findings = scan(text, load_patterns(), only)

    if args.format == "json":
        print(json.dumps(findings, ensure_ascii=False, indent=2))
        return 0
    return report(findings, path.name)


if __name__ == "__main__":
    sys.exit(main())
