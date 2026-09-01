#!/usr/bin/env python3
"""Отбор образцовых абзацев автора для промпта переплавки.

    .venv/bin/python tools/pick_exemplars.py            # пишет assets/exemplars.json
    .venv/bin/python tools/pick_exemplars.py --show     # только показать

Отбор детерминированный: сортировка по числу разных живых сигналов,
разбросу длин предложений и id гайда. Абзац годится, если в нём 35–90 слов,
нет ни одного маркера шаблона, не больше двух чисел и хотя бы два разных
живых сигнала. Роль абзаца — по разделу, в котором он стоит.

Образцы — стиль, не факт: в промпте они подписаны STYLE ONLY.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".claude" / "skills" / "hs-edit" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import common as C  # noqa: E402

OUT = C.ASSETS / "exemplars.json"
ROLE_BY_SECTION = {
    "builds": "сборки",
    "deckbuilding": "декбилдинг",
    "mulligan": "муллиган",
    "strategy": "стратегия",
    "matchups": "матч-ап",
    "conclusion": "заключение",
}
ROLES = ["зачин", "обещание", "сборки", "декбилдинг", "муллиган", "стратегия", "матч-ап",
         "заключение"]
PER_ROLE = 1
NUMBERS = re.compile(r"(?<!\w)\d+(?:[.,]\d+)?%?(?!\w)")
PROMISE = re.compile(r"гайд\s+расскаж|вы\s+найд|мы\s+наглядно|мы\s+разбер", re.I)
OPENING = re.compile(r"геро[йи]\s+гайда", re.I)
BAD_LINE = re.compile(r"наверх|нажмите на изображение|код колоды|^\s*-\s", re.I)


def paragraphs_with_lines(text):
    """Абзацы вместе с номером первой строки: PDF-корпус без пустых строк."""
    out, buf, start = [], [], 0
    for i, line in enumerate(text.split("\n")):
        s = line.strip()
        new_para = (not s) or (s[:1].isupper() and buf and buf[-1].rstrip().endswith((".", "!", "?", ":")))
        if new_para and buf:
            out.append((start, " ".join(buf)))
            buf = []
        if s:
            if not buf:
                start = i
            buf.append(s)
    if buf:
        out.append((start, " ".join(buf)))
    return out


DECKBUILDING = re.compile(r"основ\w+\s+колоды|замен\w+|бюджетн\w+|технически\w+\s+карт", re.I)
BUILDS = re.compile(r"сборк\w+", re.I)
CLASS_RE = None  # заполняется в main из structure.CLASS_PATTERNS


def _classes(text_para):
    return sum(1 for pat in CLASS_RE.values() if re.search(rf"\b{pat}\b", text_para, re.I))


def role_of(line_no, text_para, ranges, seen_opening, preamble, tail):
    """Роль абзаца: сначала раздел, в котором он стоит; зачин и обещание —
    только в преамбуле до оглавления, заключение — только в хвосте гайда.

    В PDF-корпусе заголовки «Сборки», «Декбилдинг» и «Матч-апы» часто стоят
    только в оглавлении, поэтому для абзацев вне опознанных разделов роль
    угадывается по содержанию: два и больше классов — матч-ап, «основа
    колоды» и «замены» — декбилдинг, «сборка» дважды — сборки.
    """
    for sid, (a, b) in ranges.items():
        if a <= line_no <= b:
            role = ROLE_BY_SECTION.get(sid)
            if role == "заключение" and not tail:
                return None
            return role
    if preamble and OPENING.search(text_para) and not seen_opening:
        return "зачин"
    if preamble and PROMISE.search(text_para):
        return "обещание"
    if preamble:
        return None
    if tail and re.search(r"спасибо\s+за\s+внимание", text_para, re.I):
        return "заключение"
    if _classes(text_para) >= 2:
        return "матч-ап"
    if DECKBUILDING.search(text_para):
        return "декбилдинг"
    if len(BUILDS.findall(text_para)) >= 2:
        return "сборки"
    return None


def quality(para, markers, soul, rhythm):
    words = len(para.split())
    if not 35 <= words <= 90:
        return None
    if BAD_LINE.search(para) or para.rstrip().endswith(":"):
        return None
    if len(NUMBERS.findall(para)) > 2:
        return None
    if markers.scan(para, markers.load_patterns()):
        return None
    s, _ = soul.measure(para)
    kinds = sum(1 for v in s.values() if v["n"] > 0) if s else 0
    if kinds < 2:
        return None
    r = rhythm.measure(para)
    ratio = r["ratio"] if r else 0
    return kinds, round(ratio, 3), words


def golos_samples():
    """Четыре «Образца» из ГОЛОС.md — глобальный набор для профилей без корпуса."""
    p = C.ROOT / "ГОЛОС.md"
    if not p.exists():
        return []
    text = p.read_text(encoding="utf-8")
    m = re.search(r"^## Образцы\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not m:
        return []
    out = []
    for q in re.findall(r"^>\s*(.+)$", m.group(1), re.M):
        out.append({"role": "образец", "name": "ГОЛОС.md", "text": q.strip(),
                    "words": len(q.split())})
    return out


def pick(records, markers, soul, rhythm, structure):
    sections = structure.load_profile_sections("constructed-guide")
    candidates = {role: [] for role in ROLES}
    for path, meta, body in records:
        name = C.guide_name(path)[:60]
        gid = meta.get("id", path.stem)
        bodies = structure.resolve_sections(body, sections)
        ranges = {sid: (b.start_line - 1, b.end_line - 1) for sid, b in bodies.items()
                  if not b.toc_only}
        toc = structure.toc_span(body, sections)
        toc_start = toc[0] if toc else 10 ** 9
        paras = paragraphs_with_lines(body)
        seen_opening = False
        for k, (line_no, para) in enumerate(paras):
            preamble = line_no < toc_start and k < 8
            tail = k >= len(paras) - 4
            role = role_of(line_no, para, ranges, seen_opening, preamble, tail)
            if role == "зачин":
                seen_opening = True
                # заголовок статьи в PDF склеен с первым абзацем — режем от формулы
                m = OPENING.search(para)
                para = para[m.start():]
            if not role:
                continue
            q = quality(para, markers, soul, rhythm)
            if not q:
                continue
            kinds, ratio, words = q
            candidates[role].append(((-kinds, -ratio, gid), {
                "role": role, "name": name, "guide_id": gid, "text": para, "words": words,
                "soul_kinds": kinds, "rhythm_ratio": ratio,
            }))
    chosen, used = [], set()
    for role in ROLES:
        taken = 0
        for _, item in sorted(candidates[role], key=lambda x: x[0]):
            if item["guide_id"] in used:
                continue
            chosen.append(item)
            used.add(item["guide_id"])
            taken += 1
            if taken >= PER_ROLE:
                break
    return chosen, {role: len(v) for role, v in candidates.items()}


def main() -> int:
    ap = argparse.ArgumentParser(description="Отбор образцов авторской манеры")
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()
    C.ensure_venv("pymorphy3")
    markers, soul, rhythm, structure = (C.sibling(n) for n in ("markers", "soul", "rhythm", "structure"))
    global CLASS_RE
    CLASS_RE = structure.CLASS_PATTERNS
    records = C.corpus_records()
    if not records:
        print("нет approved style corpus", file=sys.stderr)
        return 2
    chosen, pool = pick(records, markers, soul, rhythm, structure)
    data = {
        "_комментарий": "Образцы манеры для промпта переплавки. Только форма: факты и советы устарели. "
                        "Пересобрать: .venv/bin/python tools/pick_exemplars.py",
        "corpus_version": C.corpus_manifest().get("current_version", "legacy-v1"),
        "candidates_per_role": pool,
        "profiles": {
            "constructed-guide": chosen,
            "global": golos_samples(),
        },
    }
    if args.show:
        for item in chosen:
            print(f"[{item['role']}] {item['name']} ({item['words']} сл., живых {item['soul_kinds']}, ритм {item['rhythm_ratio']})")
            print(f"  {item['text']}\n")
        print("global:", len(data["profiles"]["global"]))
        return 0
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ratios = [i["rhythm_ratio"] for i in chosen]
    print(f"записано {len(chosen)} образцов в {OUT.relative_to(C.ROOT)}; "
          f"ролей {len({i['role'] for i in chosen})}, медиана ритма {statistics.median(ratios) if ratios else 0:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
