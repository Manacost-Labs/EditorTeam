"""Массовый импорт опубликованных TXT-материалов.

Исходники остаются внешними и неизменными. В отдельные corpus-коллекции
попадают только нормализованные candidate-копии с полной provenance-метаинформацией.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from editorteam.corpus_learning import (
    CorpusError,
    CorpusStore,
    _hash,
    _normalise,
    _split_front_matter,
)

NORMALIZATION_VERSION = 1
PROMO_MARKER = re.compile(r"^наша группа вконтакте\s*,?\s*присоединяйтесь\s*:?$", re.I)
AUTHOR_LINE = re.compile(r"^автор[\s\u00a0]+([^\n.]{2,80})\.?$", re.I | re.M)


@dataclass(frozen=True)
class ParsedGuide:
    source_path: Path
    source_format: str
    source_id: str
    title: str
    url: str
    published_at: str
    author: str
    categories: list[str]
    body: str
    source_sha256: str
    source_words: int
    normalized_words: int
    promo_removed: bool


@dataclass(frozen=True)
class ImportPolicy:
    genre: str
    id_prefix: str
    base_tags: tuple[str, ...]
    collection_kind: str
    full_inventory: bool = False
    check_legacy_references: bool = False


@dataclass(frozen=True)
class ReferenceGuide:
    guide_id: str
    title: str
    shingles: frozenset[int]


BG_POLICY = ImportPolicy(
    genre="battlegrounds-guide",
    id_prefix="bg",
    base_tags=("battlegrounds",),
    collection_kind="battlegrounds",
)
CONSTRUCTED_POLICY = ImportPolicy(
    genre="constructed-guide",
    id_prefix="archive",
    base_tags=("constructed", "historical"),
    collection_kind="ordinary-guides",
    full_inventory=True,
    check_legacy_references=True,
)

NEAR_DUPLICATE_THRESHOLD = 0.90
SHINGLE_WORDS = 5


def _metadata(lines: list[str], allowed: set[str]) -> dict[str, str]:
    result = {}
    for line in lines:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        if key in allowed:
            result[key] = value.strip()
    return result


def _date(value: str) -> str:
    for pattern in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(value.strip(), pattern).strftime("%Y-%m-%d")
        except ValueError:
            pass
    raise ValueError(f"неизвестный формат даты: {value or 'пусто'}")


def _clean_body(text: str) -> tuple[str, bool]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ").split("\n")
    lines = [line.rstrip() for line in lines]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    promo_removed = False
    tail_start = max(0, len(lines) - 30)
    for index in range(tail_start, len(lines)):
        if PROMO_MARKER.match(lines[index].strip()):
            lines = lines[:index]
            promo_removed = True
            break
    while lines and not lines[-1].strip():
        lines.pop()
    body = "\n".join(lines).strip()
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body + "\n" if body else "", promo_removed


def _subgenre(title: str) -> str:
    lower = title.lower()
    if re.search(r"\b(топ|рейтинг|лучших|худших|тир[- ]?лист)\b", lower):
        return "ranking"
    if "гайд по" in lower:
        return "hero-or-mechanic-guide"
    if re.search(r"\b(стратег|композиц|стол)\w*\b", lower):
        return "composition-guide"
    return "battlegrounds-article"


def parse_txt(path: Path) -> ParsedGuide:
    source_path = Path(path)
    try:
        raw_bytes = source_path.read_bytes()
        raw = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("файл должен быть UTF-8") from exc
    except OSError as exc:
        raise ValueError(f"не удалось прочитать файл: {exc}") from exc
    if not raw.strip():
        raise ValueError("пустой файл")

    if "--- TEXT ---" in raw:
        header, body_source = raw.split("--- TEXT ---", 1)
        meta = _metadata(header.splitlines(), {"title", "url", "date", "author", "categories"})
        source_format = "manacost-export-v1"
        source_id = "unknown"
        title = meta.get("title", "").strip()
        date_value = meta.get("date", "")
        author = meta.get("author", "unknown").strip() or "unknown"
        categories = [
            item.strip() for item in meta.get("categories", "").split(",") if item.strip()
        ]
    else:
        text_heading = re.search(r"(?m)^##\s+Текст\s*$", raw)
        if not text_heading:
            raise ValueError("не найден раздел '--- TEXT ---' или '## Текст'")
        header, body_source = raw[: text_heading.start()], raw[text_heading.end() :]
        meta = _metadata(header.splitlines(), {"id", "url", "дата", "тип", "категория"})
        source_format = "legacy-koloda-export-v1"
        source_id = meta.get("id", "unknown").strip() or "unknown"
        title_match = re.search(r"(?m)^#\s+(.+?)\s*$", header)
        title = title_match.group(1).strip() if title_match else ""
        date_value = meta.get("дата", "")
        categories = [
            value
            for value in (meta.get("тип", "").strip(), meta.get("категория", "").strip())
            if value
        ]
        author_match = AUTHOR_LINE.search(body_source.replace("\u00a0", " "))
        author = author_match.group(1).strip() if author_match else "unknown"

    if not title:
        raise ValueError("не найден заголовок")
    published_at = _date(date_value)
    url = meta.get("url", "").strip()
    parsed_url = urlparse(url)
    if not url or parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise ValueError("не найден корректный URL")
    body, promo_removed = _clean_body(body_source)
    if not body.strip():
        raise ValueError("после нормализации не осталось текста")

    return ParsedGuide(
        source_path=source_path,
        source_format=source_format,
        source_id=source_id,
        title=unicodedata.normalize("NFC", title),
        url=url,
        published_at=published_at,
        author=unicodedata.normalize("NFC", author),
        categories=[unicodedata.normalize("NFC", item) for item in dict.fromkeys(categories)],
        body=unicodedata.normalize("NFC", body),
        source_sha256=hashlib.sha256(raw_bytes).hexdigest(),
        source_words=len(raw.split()),
        normalized_words=len(body.split()),
        promo_removed=promo_removed,
    )


def _front_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return json.dumps(value, ensure_ascii=False)


def _constructed_subgenre(title: str) -> str:
    lower = title.lower()
    if re.search(r"\b(топ|\d+\s+лучших|рейтинг)\b", lower):
        return "ranking"
    if re.search(r"\b(бюджет|дешев)\w*\b", lower):
        return "budget-guide"
    if re.search(r"\b(как победить|контрколод)\b", lower):
        return "anti-guide"
    return "deck-guide"


def _policy_subgenre(policy: ImportPolicy, title: str) -> str:
    return (
        _subgenre(title) if policy.genre == "battlegrounds-guide" else _constructed_subgenre(title)
    )


def normalized_markdown(
    guide: ParsedGuide,
    relative_source: str,
    guide_id: str,
    *,
    genre: str = "battlegrounds-guide",
    subgenre: str | None = None,
) -> str:
    fields = {
        "id": guide_id,
        "title": guide.title,
        "genre": genre,
        "subgenre": subgenre or _subgenre(guide.title),
        "published_at": guide.published_at,
        "patch": "unknown",
        "author": guide.author,
        "source": "published",
        "source_url": guide.url,
        "source_path": relative_source,
        "source_sha256": guide.source_sha256,
        "source_format": guide.source_format,
        "source_id": guide.source_id,
        "normalization_version": NORMALIZATION_VERSION,
        "historical": True,
        "style_only": True,
        "knowledge_eligible": False,
    }
    front = "\n".join(f"{key}: {_front_value(value)}" for key, value in fields.items())
    body = guide.body
    if not re.match(r"(?m)^#\s+", body):
        body = f"# {guide.title}\n\n{body}"
    return f"---\n{front}\n---\n{body.rstrip()}\n"


def _shingles(text: str) -> frozenset[int]:
    """Устойчивый отпечаток для PDF/TXT-копий одного гайда.

    Строчные переносы и разница в знаках препинания исчезают, а слова и числа
    остаются. Хеши хранятся вместо строк, чтобы не раздувать память на большом архиве.
    """
    value = unicodedata.normalize("NFC", text).lower().replace("ё", "е").replace("\u00a0", " ")
    value = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", value)
    words = re.findall(r"[a-zа-я0-9]+", value)
    if len(words) < SHINGLE_WORDS:
        return frozenset()
    return frozenset(
        int.from_bytes(
            hashlib.blake2b(
                " ".join(words[index : index + SHINGLE_WORDS]).encode("utf-8"), digest_size=8
            ).digest(),
            "big",
        )
        for index in range(len(words) - SHINGLE_WORDS + 1)
    )


def _reference_guides(store: CorpusStore) -> list[ReferenceGuide]:
    references = []
    if not store.legacy_dir.exists():
        return references
    for path in sorted(store.legacy_dir.glob("*.md")):
        try:
            meta, body = _split_front_matter(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        signature = _shingles(body)
        if signature:
            references.append(
                ReferenceGuide(
                    guide_id=meta.get("id", path.stem),
                    title=meta.get("title", path.stem),
                    shingles=signature,
                )
            )
    return references


def _near_duplicate(body: str, references: list[ReferenceGuide]) -> dict | None:
    candidate = _shingles(body)
    if len(candidate) < 80:
        return None
    best: tuple[float, ReferenceGuide] | None = None
    for reference in references:
        if len(reference.shingles) < 80:
            continue
        shared = len(candidate & reference.shingles)
        containment = shared / min(len(candidate), len(reference.shingles))
        if best is None or containment > best[0]:
            best = (containment, reference)
    if best is None or best[0] < NEAR_DUPLICATE_THRESHOLD:
        return None
    return {
        "duplicate_reason": "legacy-near-content",
        "duplicate_of": best[1].guide_id,
        "duplicate_title": best[1].title,
        "similarity": round(best[0], 3),
    }


def _import_directory(source_dir: Path, store: CorpusStore, policy: ImportPolicy) -> dict:
    root = Path(source_dir).resolve()
    if not root.is_dir():
        raise CorpusError("GUIDE_IMPORT_SOURCE_ERROR", f"нет каталога: {root}")
    pattern = "*" if policy.full_inventory else "*.txt"
    files = sorted(
        (path for path in root.rglob(pattern) if path.is_file()),
        key=lambda path: unicodedata.normalize("NFC", str(path)),
    )
    article_files = [
        path
        for path in files
        if path.suffix.lower() == ".txt" and path.name.casefold() != "manifest.txt"
    ]
    if not article_files:
        raise CorpusError("GUIDE_IMPORT_EMPTY", f"в каталоге нет TXT-гайдов: {root}")

    manifest = store.ensure()
    source_hashes = {
        item.get("source_sha256"): item.get("id")
        for item in manifest.get("guides", [])
        if item.get("source_sha256")
    }
    normalized_hashes = {
        item.get("normalized_sha256"): item.get("id")
        for item in manifest.get("guides", [])
        if item.get("normalized_sha256")
    }
    references = _reference_guides(store) if policy.check_legacy_references else []
    records = []
    accounting = []
    parsed_guides: list[ParsedGuide] = []
    batch_source_hashes: set[str] = set()
    batch_normalized_hashes: set[str] = set()

    for path in files:
        relative = unicodedata.normalize("NFC", path.relative_to(root).as_posix())
        if policy.full_inventory and path.name.casefold() == "manifest.txt":
            accounting.append({"file": relative, "status": "skipped", "reason": "source-manifest"})
            continue
        if policy.full_inventory and path.suffix.lower() != ".txt":
            accounting.append({"file": relative, "status": "skipped", "reason": "unsupported-file"})
            continue
        try:
            guide = parse_txt(path)
            subgenre = _policy_subgenre(policy, guide.title)
            guide_id = (
                f"{policy.id_prefix}-{guide.published_at}-{subgenre}-{guide.source_sha256[:10]}"
            )
            content = normalized_markdown(
                guide,
                relative,
                guide_id,
                genre=policy.genre,
                subgenre=subgenre,
            )
            _, body = _split_front_matter(content)
            normalized_sha = _hash(_normalise(body))
            parsed_guides.append(guide)
            duplicate_meta = None
            if guide.source_sha256 in source_hashes:
                duplicate_meta = {
                    "duplicate_reason": "source-sha256-existing",
                    "duplicate_of": source_hashes[guide.source_sha256],
                }
            elif guide.source_sha256 in batch_source_hashes:
                duplicate_meta = {"duplicate_reason": "source-sha256-batch"}
            elif normalized_sha in normalized_hashes:
                duplicate_meta = {
                    "duplicate_reason": "normalized-sha256-existing",
                    "duplicate_of": normalized_hashes[normalized_sha],
                }
            elif normalized_sha in batch_normalized_hashes:
                duplicate_meta = {"duplicate_reason": "normalized-sha256-batch"}
            elif references:
                duplicate_meta = _near_duplicate(guide.body, references)
            if duplicate_meta:
                accounting.append({"file": relative, "status": "duplicate", **duplicate_meta})
                continue
            batch_source_hashes.add(guide.source_sha256)
            batch_normalized_hashes.add(normalized_sha)
            records.append(
                {
                    "content": content,
                    "source_file": relative,
                    "guide_id": guide_id,
                    "title": guide.title,
                    "published_at": guide.published_at,
                    "patch": "unknown",
                    "author": guide.author,
                    "tags": [*policy.base_tags, *guide.categories],
                    "source": "published",
                    "genre": policy.genre,
                    "run_quality": False,
                    "extra_meta": {
                        "subgenre": subgenre,
                        "collection_kind": policy.collection_kind,
                        "source_url": guide.url,
                        "source_path": relative,
                        "source_sha256": guide.source_sha256,
                        "source_format": guide.source_format,
                        "source_id": guide.source_id,
                        "source_domain": urlparse(guide.url).netloc,
                        "normalization_version": NORMALIZATION_VERSION,
                        "source_words": guide.source_words,
                        "normalized_words": guide.normalized_words,
                        "promo_removed": guide.promo_removed,
                        "historical": True,
                        "knowledge_eligible": False,
                    },
                }
            )
            accounting.append({"file": relative, "status": "ready", "guide_id": guide_id})
        except ValueError as exc:
            accounting.append({"file": relative, "status": "failed", "error": str(exc)})

    if records:
        add_result = store.add_candidates(records)
        imported_ids = {entry["id"] for entry in add_result["entries"]}
        for item in accounting:
            if item.get("status") == "ready" and item.get("guide_id") in imported_ids:
                item["status"] = "imported"
    else:
        add_result = {
            "corpus_version": manifest["current_version"],
            "regression": "NOT_REQUIRED",
            "guides_added": 0,
            "entries": [],
        }

    counts = Counter(item["status"] for item in accounting)
    authors = Counter(guide.author for guide in parsed_guides)
    formats = Counter(guide.source_format for guide in parsed_guides)
    duplicate_reasons = Counter(
        item.get("duplicate_reason", "unknown")
        for item in accounting
        if item["status"] == "duplicate"
    )
    dates = sorted(guide.published_at for guide in parsed_guides)
    total_accounted = sum(counts[name] for name in ("imported", "duplicate", "failed", "skipped"))
    return {
        "source_directory": str(root),
        "collection": store.relative_dir,
        "corpus_version": add_result["corpus_version"],
        "discovered": len(files),
        "imported": counts["imported"],
        "duplicates": counts["duplicate"],
        "failed": counts["failed"],
        "skipped": counts["skipped"],
        "accounted": total_accounted,
        "accounting_valid": total_accounted == len(files),
        "source_words": sum(guide.source_words for guide in parsed_guides),
        "normalized_words": sum(guide.normalized_words for guide in parsed_guides),
        "promo_blocks_removed": sum(guide.promo_removed for guide in parsed_guides),
        "authors": dict(sorted(authors.items())),
        "formats": dict(sorted(formats.items())),
        "duplicates_by_reason": dict(sorted(duplicate_reasons.items())),
        "reference_corpus_documents": len(references),
        "near_duplicate_threshold": (
            NEAR_DUPLICATE_THRESHOLD if policy.check_legacy_references else None
        ),
        "date_range": [dates[0], dates[-1]] if dates else [None, None],
        "historical": True,
        "knowledge_eligible": False,
        "manual_approval_required": True,
        "quality_deferred": True,
        "files": accounting,
    }


def import_directory(source_dir: Path, store: CorpusStore) -> dict:
    """Импорт TXT о Полях сражений (совместимость с прежним API)."""
    return _import_directory(source_dir, store, BG_POLICY)


def import_guides_directory(source_dir: Path, store: CorpusStore) -> dict:
    """Импорт обычных гайдов с PDF/TXT-dedup против исходного корпуса."""
    return _import_directory(source_dir, store, CONSTRUCTED_POLICY)
