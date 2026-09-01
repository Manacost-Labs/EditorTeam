"""Метрики аккуратности откалиброваны по корпусу и не путают приём с тиком."""

from pathlib import Path

import common as C

elegance = C.sibling("elegance")
CLEAN = Path("tests/fixtures/negative/clean-guide.md").read_text(encoding="utf-8")

KANCELARIT = (
    "Осуществление добора карт производится в целях повышения эффективности "
    "использования ресурсов. Реализация стратегии требует обеспечения контроля "
    "стола и накопления брони. Выполнение размена является основанием достижения "
    "преимущества. Формирование позиции происходит путём замешивания бомб."
)


def test_kancelarit_has_high_nominalization():
    m = elegance.measure(KANCELARIT)
    assert m["nominalization_per_100w"] > elegance.NORMS["nominalization_per_100w"]["fail"]
    assert "elegance.nominalization" in {f["id"] for f in elegance.findings(KANCELARIT, m)}


def test_clean_fixture_is_within_norms():
    m = elegance.measure(CLEAN)
    assert m["nominalization_per_100w"] <= elegance.NORMS["nominalization_per_100w"]["warn"]
    assert m["same_start_runs"] == 0
    assert elegance.findings(CLEAN, m) == []


def test_same_start_run_detected_but_short_anaphora_is_not():
    run = (
        "Колода выигрывает за счёт темпа и брони на ранних ходах. "
        "Колода не боится агрессии и добирает карты стабильно. "
        "Колода закрывает партию бомбами к десятому ходу."
    )
    m = elegance.measure(run)
    assert m["same_start_runs"] == 1
    assert m["same_start_words"][0][0] == "колода"
    anaphora = "Он сильный. Он быстрый. Он дешёвый."
    assert elegance.measure(anaphora)["same_start_runs"] == 0


def test_concreteness_floor_only_on_long_text():
    abstract = " ".join(["Стратегия требует понимания темпа и позиции."] * 40)
    m = elegance.measure(abstract)
    assert m["concreteness_per_100w"] < elegance.NORMS["concreteness_per_100w"]["fail"]
    assert "elegance.abstract" in {f["id"] for f in elegance.findings(abstract, m)}
    short = "Стратегия требует понимания темпа."
    assert "elegance.abstract" not in {f["id"] for f in elegance.findings(short)}


def test_empty_text_is_safe():
    assert elegance.measure("") is None
    assert elegance.findings("") == []
