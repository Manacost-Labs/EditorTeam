"""Отдельный импорт исторических материалов о Полях сражений."""

import json
from pathlib import Path

from editorteam.bg_import import import_directory, import_guides_directory, parse_txt
from editorteam.corpus_learning import CorpusStore

MODERN = """Title: 7 лучших Аксессуаров
URL: https://hs-manacost.ru/example/
Date: 2025-05-01
Author: Alexey
Categories: Поля сражений, Гайды

--- TEXT ---

Тир-1 существо дает +3/+3. Это число и название нельзя потерять.

Удачи!

Наша группа Вконтакте, присоединяйтесь:

vk.com/manacost
"""

LEGACY = """# (Обновлено) Большой гайд по элементалям

ID: 2244
URL: https://old.kolodahearthstone.ru/example/
Дата: 03.04.2024
Тип: Поля сражений
Категория: Поля сражений

## Описание

Дубликат краткого описания не должен попасть в основной текст.

## Кратко

Еще один служебный дубликат.

## Текст

На 5-м уровне таверны ищите ключевого элементаля 10/10.

Автор Redsnapper.
"""


def _store(tmp_path: Path) -> CorpusStore:
    return CorpusStore(
        tmp_path,
        corpus_dir_name="corpus-bg",
        include_legacy=False,
        quality_runner=lambda _text, _profile: [],
    )


def test_parse_two_supported_txt_formats(tmp_path: Path) -> None:
    modern = tmp_path / "modern.txt"
    legacy = tmp_path / "legacy.txt"
    modern.write_text(MODERN, encoding="utf-8")
    legacy.write_text(LEGACY, encoding="utf-8")

    parsed_modern = parse_txt(modern)
    parsed_legacy = parse_txt(legacy)

    assert parsed_modern.source_format == "manacost-export-v1"
    assert parsed_modern.author == "Alexey"
    assert parsed_modern.promo_removed is True
    assert "vk.com" not in parsed_modern.body
    assert "+3/+3" in parsed_modern.body
    assert parsed_legacy.source_format == "legacy-koloda-export-v1"
    assert parsed_legacy.published_at == "2024-04-03"
    assert parsed_legacy.author == "Redsnapper"
    assert "10/10" in parsed_legacy.body
    assert "служебный дубликат" not in parsed_legacy.body


def test_directory_import_is_one_version_and_preserves_sources(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    modern = source / "modern.txt"
    legacy = source / "legacy.txt"
    modern.write_text(MODERN, encoding="utf-8")
    legacy.write_text(LEGACY, encoding="utf-8")
    originals = {path.name: path.read_bytes() for path in source.glob("*.txt")}
    store = _store(tmp_path)

    report = import_directory(source, store)

    assert report["discovered"] == 2
    assert report["imported"] == 2
    assert report["accounting_valid"] is True
    assert report["corpus_version"] == "v2"
    assert report["knowledge_eligible"] is False
    assert {path.name: path.read_bytes() for path in source.glob("*.txt")} == originals

    manifest = json.loads((tmp_path / "corpus-bg" / "manifest.json").read_text("utf-8"))
    assert manifest["collection"] == "corpus-bg"
    assert len(manifest["versions"]) == 2
    assert len(manifest["guides"]) == 2
    assert {item["status"] for item in manifest["guides"]} == {"candidate"}
    assert {item["quality_status"] for item in manifest["guides"]} == {"pending"}
    assert all(item["historical"] is True for item in manifest["guides"])
    assert all(item["knowledge_eligible"] is False for item in manifest["guides"])
    assert all(not Path(item["source_path"]).is_absolute() for item in manifest["guides"])
    assert store.inspect()["approved_guides"] == 0


def test_repeat_import_accounts_duplicates_without_new_version(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "modern.txt").write_text(MODERN, encoding="utf-8")
    store = _store(tmp_path)
    first = import_directory(source, store)

    second = import_directory(source, store)

    assert first["corpus_version"] == "v2"
    assert second["corpus_version"] == "v2"
    assert second["duplicates"] == 1
    assert second["imported"] == 0
    assert second["source_words"] > 0
    assert second["authors"] == {"Alexey": 1}
    assert second["accounting_valid"] is True


def test_failed_file_is_accounted_while_valid_files_import(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "modern.txt").write_text(MODERN, encoding="utf-8")
    (source / "empty.txt").write_text("", encoding="utf-8")

    report = import_directory(source, _store(tmp_path))

    assert report["discovered"] == 2
    assert report["imported"] == 1
    assert report["failed"] == 1
    assert report["accounted"] == 2
    assert report["accounting_valid"] is True


def test_deferred_quality_runs_when_candidate_is_approved(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "modern.txt").write_text(MODERN, encoding="utf-8")
    checked = []
    store = CorpusStore(
        tmp_path,
        corpus_dir_name="corpus-bg",
        include_legacy=False,
        quality_runner=lambda text, profile: checked.append((text, profile)) or [],
    )
    report = import_directory(source, store)
    guide_id = next(item["guide_id"] for item in report["files"] if item["status"] == "imported")

    store.approve(guide_id)

    assert len(checked) == 1
    assert checked[0][1] == "battlegrounds-guide"
    manifest = json.loads((tmp_path / "corpus-bg" / "manifest.json").read_text("utf-8"))
    entry = next(item for item in manifest["guides"] if item["id"] == guide_id)
    assert entry["status"] == "approved"
    assert entry["quality_status"] == "complete"


def _legacy_export(title: str, body: str, source_id: int) -> str:
    return f"""# {title}

ID: {source_id}
URL: https://old.kolodahearthstone.ru/guide/{source_id}
Дата: 03.04.2024
Тип: Гайды
Категория: Стандарт

## Текст

{body}
"""


def test_constructed_archive_accounts_auxiliary_files_and_pdf_duplicates(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    articles = source / "articles"
    articles.mkdir(parents=True)
    source.joinpath("manifest.txt").write_text("Количество: 2\n", encoding="utf-8")
    source.joinpath(".DS_Store").write_bytes(b"metadata")

    repeated = " ".join(
        f"Совет {index} сохраняет карту и число {index} для матчапа." for index in range(40)
    )
    unique = " ".join(
        f"Новая тактика {index} объясняет другой архетип и темп игры." for index in range(40)
    )
    articles.joinpath("duplicate.txt").write_text(
        _legacy_export("Старый гайд", repeated, 10), encoding="utf-8"
    )
    articles.joinpath("unique.txt").write_text(
        _legacy_export("Новый гайд", unique, 11), encoding="utf-8"
    )
    legacy = tmp_path / "гайды"
    legacy.mkdir()
    # PDF-версия отличается переносами и front matter, но текст тот же.
    legacy.joinpath("old.md").write_text(
        '---\nid: guide-old\ntitle: "Старый гайд"\n---\n'
        + repeated.replace(" сохраняет", " сохра-\nняет"),
        encoding="utf-8",
    )
    originals = {
        path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()
    }
    store = CorpusStore(
        tmp_path,
        corpus_dir_name="corpus-archive",
        include_legacy=False,
        quality_runner=lambda _text, _profile: [],
    )

    report = import_guides_directory(source, store)

    assert report["discovered"] == 4
    assert report["imported"] == 1
    assert report["duplicates"] == 1
    assert report["skipped"] == 2
    assert report["failed"] == 0
    assert report["accounted"] == 4
    assert report["accounting_valid"] is True
    assert report["reference_corpus_documents"] == 1
    assert report["duplicates_by_reason"] == {"legacy-near-content": 1}
    duplicate = next(item for item in report["files"] if item["status"] == "duplicate")
    assert duplicate["duplicate_of"] == "guide-old"
    assert duplicate["similarity"] >= 0.9
    assert {
        path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()
    } == originals

    manifest = json.loads((tmp_path / "corpus-archive" / "manifest.json").read_text("utf-8"))
    assert len(manifest["guides"]) == 1
    entry = manifest["guides"][0]
    assert entry["genre"] == "constructed-guide"
    assert entry["source_id"] == "11"
    assert entry["status"] == "candidate"
    assert entry["quality_status"] == "pending"
    assert entry["knowledge_eligible"] is False


def test_constructed_archive_repeat_import_is_idempotent(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    body = " ".join(f"Уникальный совет номер {index} для колоды." for index in range(30))
    source.joinpath("guide.txt").write_text(
        _legacy_export("Большой гайд", body, 42), encoding="utf-8"
    )
    store = CorpusStore(
        tmp_path,
        corpus_dir_name="corpus-archive",
        include_legacy=False,
        quality_runner=lambda _text, _profile: [],
    )

    first = import_guides_directory(source, store)
    second = import_guides_directory(source, store)

    assert first["corpus_version"] == "v2"
    assert second["corpus_version"] == "v2"
    assert second["imported"] == 0
    assert second["duplicates"] == 1
    assert second["duplicates_by_reason"] == {"source-sha256-existing": 1}
