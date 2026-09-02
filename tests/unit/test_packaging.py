"""Упаковка в автономный скилл: пути и морфология не должны зависеть
от раскладки репозитория.

Оба дефекта были найдены запуском собранного скилла системным Python:
в .venv они не воспроизводились, потому что там пакеты установлены нормально.
"""

import subprocess
import sys
from pathlib import Path

import common as C


def test_root_found_by_marker_not_by_depth():
    """Корень ищется по признаку: в сборке раскладка на три уровня короче."""
    assert (C.ROOT / "гайды").exists() or (C.ROOT / "config").exists()


def test_corpus_resolves():
    assert C.CORPUS.exists(), f"корпус не найден: {C.CORPUS}"
    assert len(C.corpus_files()) > 40


def test_morph_gets_explicit_dictionary_path():
    """Во вложенной поставке метаданных пакета нет, и словарь надо указывать."""
    m = C.morph()
    assert m.parse("Балинды")[0].normal_form == "балинда"


def test_all_parses_returned_not_only_first():
    """Разбор возвращает все варианты, а не вероятнейший.

    «Бранном» даёт и «бранном», и «бранный»: на первом разборе имя карты
    терялось и превращалось в ложную опечатку.
    """
    assert len(C.lemmas("Бранном")) > 1
    assert "бранный" in C.lemmas("Бранном")


def test_common_word_is_not_a_card_reference():
    """«Играем» и «Играющая на воздухе» дают одну лемму «играть».

    Без фильтра частотности обычный глагол объявлялся коротким именем карты.
    """
    cards = C.sibling("cards")
    idx = cards.Index(C.card_db()["карты"], C.morph())
    common = cards.corpus_common(idx)
    short, _ = cards.scan_words("Играем под Бранном и добираем.", idx, common)
    assert short == {}, dict(short)


def test_real_short_name_still_recognised():
    cards = C.sibling("cards")
    idx = cards.Index(C.card_db()["карты"], C.morph())
    common = cards.corpus_common(idx)
    short, _ = cards.scan_words("Монетку тратьте рано.", idx, common)
    assert ("Монетку", "Фальшивая монетка") in short


def test_build_script_runs(tmp_path):
    """Сборщик должен отработать и выдать архив с манифестом."""
    root = Path(__file__).resolve().parents[2]
    r = subprocess.run(
        [sys.executable, str(root / "tools" / "build_skill.py"), "--без-корпуса"],
        capture_output=True,
        text=True,
        cwd=root,
        timeout=300,
    )
    assert r.returncode == 0, r.stderr
    archive = root / "build" / "hearthstone-editor.zip"
    assert archive.exists()
    assert (root / "build" / "hearthstone-editor" / "MANIFEST.json").exists()
    packaged_skill = root / "build" / "hearthstone-editor" / "SKILL.md"
    assert packaged_skill.exists()
    sys.path.insert(0, str(root / "tools"))
    import build_skill

    assert f'version: "{build_skill.PLUGIN_VERSION}"' in packaged_skill.read_text(encoding="utf-8")
    assert (
        root / "build" / "hearthstone-editor" / "references" / "editorial-decision-protocol.md"
    ).exists()


def test_shared_skill_blocks_are_in_sync():
    """Общие разделы двух инструкций переносятся скриптом, а не руками."""
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "tools"))
    import sync_skill

    assert sync_skill.check() == []
    assert {"rewrite", "cards-unknown"} <= set(sync_skill.expected())
    body = sync_skill.expected()["rewrite"]
    assert ".claude/skills" not in body and "python3 scripts/editor_team.py check" in body


def test_released_version_matches_sources_or_is_older():
    """Пока release/ хранит ту же версию, что исходники, он не может от них отставать."""
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "tools"))
    import build_skill

    released = build_skill.release_version()
    drift = build_skill.release_drift()
    if released == build_skill.PLUGIN_VERSION:
        assert drift == [], "\n".join(drift)
    else:
        assert released is None or released < build_skill.PLUGIN_VERSION
