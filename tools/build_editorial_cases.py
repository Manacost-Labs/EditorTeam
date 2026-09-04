"""Собирает evals/cases/editorial.json из авторского корпуса.

Кейсы `unchanged` — настоящие абзацы из `гайды/` без изменений: reference
равен source, редактор не должен их переписывать. Кейсы `edit` — те же
настоящие абзацы с детерминированно внесёнными дефектами (AI-рамки,
канцелярит, повторы, перегруженные предложения, сломанная структура);
reference — исходный авторский абзац. WoW и LoL в корпусе отсутствуют,
поэтому их кейсы помечены `synthetic: true` и построены на существующих
коротких фрагментах evals/cases/cases.json.

Запуск: python tools/build_editorial_cases.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUIDES = sorted((ROOT / "гайды").glob("*.md"))
OUT = ROOT / "evals" / "cases" / "editorial.json"
EXISTING = ROOT / "evals" / "cases" / "cases.json"

NUMBER = re.compile(r"(?<![\w/])\d+(?:[.,]\d+)?%?(?![\w])")
SENTENCE_END = re.compile(r"(?<=[.!?…])\s+(?=[А-ЯЁ«\"(])")
NAME = re.compile(r"(?<=[^.!?\n]\s)([А-ЯЁ][а-яё'’]+(?:\s+[А-ЯЁ][а-яё'’]+)*)")
CLASSES = [
    "Воин",
    "Маг",
    "Жрец",
    "Охотник",
    "Разбойник",
    "Шаман",
    "Чернокнижник",
    "Паладин",
    "Друид",
    "Рыцарь смерти",
    "Темные дары",
]

# Гайды перебираются в этом порядке; у которых нет чистого абзаца — пропускаются.
GUIDE_ORDER = [
    9,
    12,
    27,
    28,
    32,
    35,
    41,
    44,
    48,
    6,
    13,
    22,
    3,
    4,
    5,
    7,
    8,
    10,
    11,
    14,
    15,
    16,
    17,
    18,
    19,
    20,
    21,
    23,
    24,
    25,
    26,
    29,
    30,
    31,
    33,
    34,
    36,
    37,
    38,
    39,
    40,
    42,
    43,
    45,
    46,
    47,
]
UNCHANGED_COUNT = 12
EDIT_DEFECTS = (
    ["ai-frames"] * 3 + ["bureaucracy"] * 3 + ["repetition"] * 3 + ["overloaded-sentences"] * 3
)


def body(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        text = text.split("---", 2)[2]
    return text


def sentences(text: str) -> list[str]:
    return [s for s in SENTENCE_END.split(text.strip()) if s]


def pick(index: int, used: set[str], min_sentences: int = 3) -> str:
    """Первый чистый абзац гайда: начинается с заглавной кириллической буквы,
    заканчивается точкой, содержит число и не меньше min_sentences
    предложений. Отсекает строки, разорванные при извлечении из PDF."""
    for line in body(GUIDES[index]).split("\n")[60:]:
        paragraph = line.strip()
        if not 380 <= len(paragraph) <= 900 or paragraph in used:
            continue
        if not re.match(r"[А-ЯЁ]", paragraph) or not paragraph.endswith("."):
            continue
        if "http" in paragraph or not re.search(r"\d", paragraph):
            continue
        if len(sentences(paragraph)) < min_sentences:
            continue
        used.add(paragraph)
        return paragraph
    return ""


def preserve(text: str) -> list[str]:
    numbers = sorted(set(NUMBER.findall(text)))
    names = sorted({m.strip() for m in NAME.findall(text) if len(m) > 3})
    classes = [c for c in CLASSES if re.search(rf"(?<![\w]){c}(?![\w])", text)]
    seen: list[str] = []
    for item in numbers + names + classes:
        if item not in seen:
            seen.append(item)
    return seen[:12]


# --- defect injections: deterministic, reference stays the original --------


def ai_frames(text: str) -> str:
    parts = sentences(text)
    parts[0] = "Стоит отметить, что " + parts[0][0].lower() + parts[0][1:]
    if len(parts) > 2:
        parts[2] = "Важно понимать, что " + parts[2][0].lower() + parts[2][1:]
    parts.append("Подводя итог, всё вышесказанное важно учитывать в каждой партии.")
    return " ".join(parts)


BUREAUCRACY = [
    (r"\bиспользуйте\b", "осуществляйте использование"),
    (r"\bследите\b", "производите отслеживание"),
    (r"\bнужно\b", "необходимо осуществлять"),
    (r"\bможно\b", "имеется возможность"),
    (r"\bиграйте\b", "производите розыгрыш"),
    (r"\bберегите\b", "осуществляйте сбережение"),
    (r"\bпомните\b", "осуществляйте учёт того факта"),
    (r"\bвыбирайте\b", "производите выбор"),
]


def bureaucracy(text: str) -> str:
    out, hits = text, 0
    for pattern, replacement in BUREAUCRACY:
        out, n = re.subn(pattern, replacement, out, count=1)
        hits += n
    if hits < 2:
        out = "В рамках данного материала осуществляется рассмотрение следующего плана. " + out
    return out


NEGATION = re.compile(r"(?<![\w])(не|ни|нет|нельзя|никогда|без)(?![\w])", re.I)


def repetition(text: str) -> str:
    parts = sentences(text)
    # Повторяется предложение без чисел и отрицаний: reference не должен
    # «терять» число или менять последовательность отрицаний.
    for position in range(1, len(parts)):
        if not re.search(r"\d", parts[position]) and not NEGATION.search(parts[position]):
            parts.insert(position + 1, parts[position])
            break
    first = parts[0].split(" ")
    for position, word in enumerate(first):
        if len(word) >= 4 and word.isalpha() and not NEGATION.fullmatch(word):
            first.insert(position, word)
            break
    parts[0] = " ".join(first)
    return " ".join(parts)


def overloaded(text: str) -> str:
    parts = sentences(text)
    joined = (
        parts[0].rstrip(".!?")
        + ", при этом "
        + parts[1][0].lower()
        + parts[1][1:].rstrip(".!?")
        + ", а также "
        + parts[2][0].lower()
        + parts[2][1:]
    )
    return " ".join([joined] + parts[3:])


def structure(text: str) -> str:
    """Ломает структуру: абзацы склеиваются в одно полотно без смысловых
    швов. Порядок и заголовок сохраняются, чтобы reference не нарушал
    затворы на порядок отрицаний и разметку."""
    parts = [p for p in text.split("\n\n") if p.strip()]
    heading = [p for p in parts if p.startswith("#")]
    rest = [p for p in parts if not p.startswith("#")]
    assert len(rest) >= 2, "structure defect needs at least two paragraphs"
    wall = " ".join(rest)
    return heading[0] + "\n\n" + wall if heading else wall


DEFECTS = {
    "ai-frames": (ai_frames, ["remove_ai_frames", "keep_facts"]),
    "bureaucracy": (bureaucracy, ["replace_bureaucracy_with_verbs", "keep_facts"]),
    "repetition": (repetition, ["remove_repetition", "keep_facts"]),
    "overloaded-sentences": (overloaded, ["split_overloaded_sentence", "keep_facts"]),
    "broken-structure": (structure, ["restore_paragraphs", "keep_facts"]),
}


def load_existing(case_id: str) -> dict:
    for item in json.loads(EXISTING.read_text(encoding="utf-8")):
        if item["vars"]["id"] == case_id:
            return item["vars"]
    raise KeyError(case_id)


def case(
    case_id,
    game,
    profile,
    source,
    reference,
    action,
    defects,
    allowed,
    must=None,
    synthetic=False,
    origin=None,
):
    item = {
        "id": case_id,
        "game": game,
        "profile": profile,
        "source": source,
        "reference": reference,
        "expected_action": action,
        "defects": defects,
        "must_preserve": [
            entity
            for entity in (must if must is not None else preserve(reference))
            if entity in source and entity in reference
        ],
        "allowed_changes": allowed,
    }
    if synthetic:
        item["synthetic"] = True
    if origin:
        item["origin"] = origin
    return item


def structural_cases() -> list[dict]:
    out = []
    for case_id, origin in (
        ("hs-structure-01", "corpus-05"),
        ("hs-structure-02", "corpus-09"),
        ("hs-structure-03", "corpus-08"),
    ):
        vars_ = load_existing(origin)
        reference = vars_["text"]
        out.append(
            case(
                case_id,
                "hearthstone",
                vars_["profile"],
                structure(reference),
                reference,
                "edit",
                ["broken-structure"],
                DEFECTS["broken-structure"][1],
                must=vars_["protected_entities"],
                origin="гайды с внесённым дефектом",
            )
        )
    vars_ = load_existing("corpus-07")
    out.append(
        case(
            "hs-markdown-unchanged-01",
            "hearthstone",
            vars_["profile"],
            vars_["text"],
            vars_["text"],
            "unchanged",
            [],
            [],
            must=vars_["protected_entities"],
            origin="гайды",
        )
    )
    return out


LOL_GOOD = (
    "После покупки Бесконечного края дуэль на боковой линии становится проще, но урон надо "
    "наносить вовремя. Не пушьте линию без вижена: ганки становятся слишком простыми. "
    "На 10-й минуте забираем дракона, если лесной соперник показался на верхней линии."
)
TABLE = (
    "## Винрейт за неделю\n\n| Колода | Винрейт | Доля |\n| --- | --- | --- |\n"
    "| Контроль Маг | 54,2% | 8% |\n| Агро Друид | 51,0% | 12% |\n\n"
    "Цифры — по 20 000 партий с [сайта статистики](https://example.com/stats). "
    "Не переносите их на все ранги."
)

SYNTHETIC = [
    (
        "wow-edit-01",
        "wow",
        "wow-guide",
        "Стоит отметить, что на 7-м уровне предмет дает 12% скорости. Важно понимать, что в "
        "рейде это полезнее, чем лишние 20 единиц брони. Подводя итог, выбор очевиден.",
        "На 7-м уровне предмет дает 12% скорости. В рейде это полезнее, чем лишние 20 единиц брони.",
        "edit",
        ["ai-frames"],
        ["remove_ai_frames", "keep_facts"],
    ),
    (
        "wow-edit-02",
        "wow",
        "wow-guide",
        "Не следует осуществлять расходование кулдауна перед заходом в подземелье: следующий "
        "пак сложнее. Танк осуществляет удержание аггро, а хил производит отслеживание группы.",
        "Не тратьте кулдаун перед заходом в подземелье: следующий пак сложнее. Танк держит "
        "аггро, а хил следит за группой.",
        "edit",
        ["bureaucracy"],
        ["replace_bureaucracy_with_verbs", "keep_facts"],
    ),
    (
        "wow-unchanged-01",
        "wow",
        "wow-guide",
        load_existing("corpus-03")["text"],
        load_existing("corpus-03")["text"],
        "unchanged",
        [],
        [],
    ),
    (
        "lol-edit-01",
        "league",
        "guide",
        "На 10-й минуте забираем дракона, забираем дракона, если лесной соперник показался на "
        "верхней линии. Керри нужен безопасный фарм, а саппорт должен первым приходить к "
        "объекту, саппорт должен первым приходить к объекту.",
        "На 10-й минуте забираем дракона, если лесной соперник показался на верхней линии. "
        "Керри нужен безопасный фарм, а саппорт должен первым приходить к объекту.",
        "edit",
        ["repetition"],
        ["remove_repetition", "keep_facts"],
    ),
    (
        "lol-edit-02",
        "league",
        "guide",
        "Не пушьте линию без вижена, при этом ганки становятся слишком простыми, а также после "
        "покупки Бесконечного края дуэль на боковой линии становится проще, но урон надо "
        "наносить вовремя, и это важно помнить в каждой игре постоянно.",
        "Не пушьте линию без вижена: ганки становятся слишком простыми. После покупки "
        "Бесконечного края дуэль на боковой линии становится проще, но урон надо наносить вовремя.",
        "edit",
        ["overloaded-sentences"],
        ["split_overloaded_sentence", "keep_facts"],
    ),
    ("lol-unchanged-01", "league", "guide", LOL_GOOD, LOL_GOOD, "unchanged", [], []),
    ("hs-table-unchanged-01", "hearthstone", "meta-report", TABLE, TABLE, "unchanged", [], []),
    (
        "hs-list-edit-01",
        "hearthstone",
        "constructed-guide",
        "## Муллиган\n\n- Стоит отметить, что Огненный шар держите до 6 хода.\n"
        "- Важно понимать, что Полосу везения не берите против агро.\n"
        "- Подводя итог, монету осуществляйте сбережение для 4 маны.",
        "## Муллиган\n\n- Огненный шар держите до 6 хода.\n- Полосу везения не берите против агро.\n"
        "- Монету берегите для 4 маны.",
        "edit",
        ["ai-frames", "bureaucracy"],
        ["remove_ai_frames", "replace_bureaucracy_with_verbs", "keep_markdown"],
    ),
]


def main() -> None:
    cases = []
    used: set[str] = set()
    guides = iter(GUIDE_ORDER)

    def next_paragraph(min_sentences: int) -> str:
        for index in guides:
            text = pick(index, used, min_sentences)
            if text:
                return text
        raise ValueError("corpus ran out of clean paragraphs")

    for number in range(1, UNCHANGED_COUNT + 1):
        text = next_paragraph(3)
        cases.append(
            case(
                f"hs-unchanged-{number:02d}",
                "hearthstone",
                "constructed-guide",
                text,
                text,
                "unchanged",
                [],
                [],
                origin="гайды",
            )
        )
    counters: dict[str, int] = {}
    for defect in EDIT_DEFECTS:
        reference = next_paragraph(4)
        inject, allowed = DEFECTS[defect]
        counters[defect] = counters.get(defect, 0) + 1
        cases.append(
            case(
                f"hs-{defect}-{counters[defect]:02d}",
                "hearthstone",
                "constructed-guide",
                inject(reference),
                reference,
                "edit",
                [defect],
                allowed,
                origin="гайды с внесённым дефектом",
            )
        )
    cases.extend(structural_cases())
    for case_id, game, profile, source, reference, action, defects, allowed in SYNTHETIC:
        cases.append(
            case(
                case_id, game, profile, source, reference, action, defects, allowed, synthetic=True
            )
        )
    for item in cases:
        assert item["source"] and item["reference"], item["id"]
        if item["expected_action"] == "unchanged":
            assert item["source"] == item["reference"], item["id"]
        else:
            assert item["source"] != item["reference"], item["id"]
        for entity in item["must_preserve"]:
            assert entity in item["source"] and entity in item["reference"], (item["id"], entity)
    OUT.write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(len(cases), "cases →", OUT.relative_to(ROOT))


if __name__ == "__main__":
    main()
