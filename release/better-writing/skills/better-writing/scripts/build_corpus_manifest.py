#!/usr/bin/env python3
"""Inventory a large writing corpus and optionally chunk readable text files.

The script uses only the Python standard library. It records PDFs but leaves
extraction and OCR to specialised PDF tooling.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable, Sequence


SUPPORTED_SUFFIXES = frozenset({".csv", ".docx", ".html", ".json", ".md", ".pdf", ".rtf", ".txt"})
TEXT_SUFFIXES = frozenset({".csv", ".html", ".json", ".md", ".txt"})


def sha256_file(path: Path, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(block_size):
            digest.update(block)
    return digest.hexdigest()


def display_path(path: Path, base_dir: Path) -> str:
    try:
        return path.resolve().relative_to(base_dir.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def is_ignored(path: Path, ignored: tuple[Path, ...]) -> bool:
    resolved = path.resolve()
    return any(resolved == item or item in resolved.parents for item in ignored)


def discover(inputs: Sequence[Path], recursive: bool, ignored: tuple[Path, ...]) -> tuple[list[Path], list[dict[str, str]]]:
    files: set[Path] = set()
    issues: list[dict[str, str]] = []
    for raw_path in inputs:
        path = raw_path.resolve()
        if not path.exists():
            issues.append({"path": str(path), "status": "missing", "error": "input does not exist"})
            continue
        if path.is_symlink():
            issues.append({"path": str(path), "status": "skipped", "error": "symbolic links are not followed"})
            continue
        if path.is_file():
            if not is_ignored(path, ignored):
                files.add(path)
            continue
        if not path.is_dir():
            issues.append({"path": str(path), "status": "skipped", "error": "input is not a regular file or directory"})
            continue
        iterator: Iterable[Path] = path.rglob("*") if recursive else path.glob("*")
        for candidate in iterator:
            if candidate.is_symlink() or not candidate.is_file() or is_ignored(candidate, ignored):
                continue
            files.add(candidate.resolve())
    return sorted(files, key=lambda item: str(item).casefold()), issues


def pdf_page_count(path: Path) -> tuple[int | None, str | None]:
    command = shutil.which("pdfinfo")
    if command is None:
        return None, "pdfinfo is unavailable; page count not measured"
    completed = subprocess.run(
        [command, str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip().splitlines()
        return None, message[0] if message else "pdfinfo could not read this PDF"
    for line in completed.stdout.splitlines():
        if line.lower().startswith("pages:"):
            value = line.partition(":")[2].strip()
            if value.isdigit():
                return int(value), None
    return None, "pdfinfo returned no page count"


def text_metrics(path: Path) -> tuple[int, int, int]:
    characters = 0
    replacement_characters = 0
    lines = 0
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        for line in handle:
            characters += len(line)
            replacement_characters += line.count("\ufffd")
            lines += 1
    return characters, lines, replacement_characters


def split_long_unit(unit: str, limit: int) -> list[str]:
    if len(unit) <= limit:
        return [unit]
    pieces: list[str] = []
    remaining = unit
    while len(remaining) > limit:
        cut = remaining.rfind(" ", 0, limit + 1)
        if cut < limit // 2:
            cut = limit
        pieces.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        pieces.append(remaining)
    return pieces


def semantic_chunks(text: str, limit: int) -> list[str]:
    units: list[str] = []
    current_lines: list[str] = []
    for line in text.splitlines():
        if line.strip():
            current_lines.append(line.rstrip())
        elif current_lines:
            units.append("\n".join(current_lines).strip())
            current_lines = []
    if current_lines:
        units.append("\n".join(current_lines).strip())
    chunks: list[str] = []
    current = ""
    for raw_unit in units:
        for unit in split_long_unit(raw_unit, limit):
            candidate = f"{current}\n\n{unit}" if current else unit
            if current and len(candidate) > limit:
                chunks.append(current)
                current = unit
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def write_chunks(path: Path, source_id: str, digest: str, chunk_dir: Path, limit: int) -> list[dict[str, object]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    chunks = semantic_chunks(text, limit)
    records: list[dict[str, object]] = []
    for index, chunk in enumerate(chunks, start=1):
        chunk_id = f"{digest[:12]}-{index:05d}"
        filename = f"{chunk_id}.txt"
        header = f"[source: {source_id} | chunk: {index:05d}]\n\n"
        atomic_write(chunk_dir / filename, header + chunk.strip() + "\n")
        records.append({"id": chunk_id, "file": filename, "characters": len(chunk)})
    return records


def build_manifest(
    inputs: Sequence[Path],
    base_dir: Path,
    recursive: bool = True,
    chunk_dir: Path | None = None,
    chunk_chars: int = 12000,
    output_path: Path | None = None,
) -> dict[str, object]:
    ignored_items = [item.resolve() for item in (chunk_dir, output_path) if item is not None]
    files, issues = discover(inputs, recursive, tuple(ignored_items))
    entries: list[dict[str, object]] = []
    digests: dict[str, str] = {}
    for path in files:
        source_id = display_path(path, base_dir)
        suffix = path.suffix.lower()
        entry: dict[str, object] = {
            "source_id": source_id,
            "extension": suffix or None,
            "bytes": path.stat().st_size,
            "status": "ready" if suffix in SUPPORTED_SUFFIXES else "unsupported",
        }
        try:
            digest = sha256_file(path)
            entry["sha256"] = digest
            duplicate_of = digests.get(digest)
            if duplicate_of is not None:
                entry["status"] = "duplicate"
                entry["duplicate_of"] = duplicate_of
            else:
                digests[digest] = source_id
            if suffix == ".pdf":
                pages, warning = pdf_page_count(path)
                entry["pages"] = pages
                entry["extraction"] = "not_extracted"
                if warning:
                    entry["warning"] = warning
            elif suffix in TEXT_SUFFIXES:
                characters, lines, replacements = text_metrics(path)
                entry.update({"characters": characters, "lines": lines, "replacement_characters": replacements})
                entry["extraction"] = "text_indexed"
                if replacements:
                    entry["warning"] = "UTF-8 decoding inserted replacement characters"
                if chunk_dir is not None and duplicate_of is None:
                    entry["chunks"] = write_chunks(path, source_id, digest, chunk_dir, chunk_chars)
            elif suffix in SUPPORTED_SUFFIXES:
                entry["extraction"] = "binary_not_extracted"
        except (OSError, subprocess.SubprocessError) as exc:
            entry["status"] = "failed"
            entry["error"] = str(exc)
        entries.append(entry)
    return {
        "schema_version": 1,
        "base_dir": str(base_dir.resolve()),
        "summary": {
            "discovered": len(entries) + len(issues),
            "unique": sum(1 for item in entries if item.get("status") == "ready"),
            "duplicates": sum(1 for item in entries if item.get("status") == "duplicate"),
            "unsupported": sum(1 for item in entries if item.get("status") == "unsupported"),
            "failed_or_missing": sum(1 for item in entries if item.get("status") == "failed") + len(issues),
        },
        "sources": entries,
        "input_issues": issues,
    }


def run_self_tests() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="better-writing-corpus-") as temporary:
        root = Path(temporary)
        (root / "a.txt").write_text("Первый абзац.\n\nВторой абзац с фактами.\n", encoding="utf-8")
        (root / "duplicate.txt").write_text((root / "a.txt").read_text(encoding="utf-8"), encoding="utf-8")
        (root / "notes.md").write_text("# Notes\n\nA sufficiently long paragraph for chunking.\n", encoding="utf-8")
        (root / "ignored.bin").write_bytes(b"\x00\x01")
        chunks = root / "chunks"
        manifest = build_manifest((root,), root, chunk_dir=chunks, chunk_chars=32)
        sources = manifest["sources"]
        assert isinstance(sources, list)
        summary = manifest["summary"]
        assert isinstance(summary, dict)
        checks = {
            "discovers_all_regular_files": summary.get("discovered") == 4,
            "deduplicates_by_digest": summary.get("duplicates") == 1,
            "marks_unsupported_files": summary.get("unsupported") == 1,
            "uses_relative_source_ids": all(not str(item.get("source_id", "")).startswith("/") for item in sources),
            "writes_bounded_text_chunks": bool(list(chunks.glob("*.txt"))),
            "keeps_chunk_headers": all(path.read_text(encoding="utf-8").startswith("[source: ") for path in chunks.glob("*.txt")),
        }
        return {"passed": all(checks.values()), "checks": checks}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inventory source files for a large writing job.")
    parser.add_argument("inputs", nargs="+", type=Path, help="Files or directories to inventory")
    parser.add_argument("--output", required=True, type=Path, help="Manifest JSON path")
    parser.add_argument("--base-dir", type=Path, default=Path.cwd(), help="Base directory for stable source IDs")
    parser.add_argument("--no-recursive", action="store_true", help="Do not recurse into input directories")
    parser.add_argument("--chunks-dir", type=Path, help="Optional output directory for text chunks")
    parser.add_argument("--chunk-chars", type=int, default=12000, help="Approximate maximum characters per chunk")
    parser.add_argument("--self-test", action="store_true", help="Run built-in tests instead of inventorying inputs")
    args = parser.parse_args(argv)
    if args.self_test:
        result = run_self_tests()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result["passed"] else 1
    if args.chunk_chars < 1000:
        parser.error("--chunk-chars must be at least 1000")
    manifest = build_manifest(
        tuple(args.inputs),
        args.base_dir,
        recursive=not args.no_recursive,
        chunk_dir=args.chunks_dir,
        chunk_chars=args.chunk_chars,
        output_path=args.output,
    )
    atomic_write(args.output, json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    print(json.dumps(manifest["summary"], indent=2, ensure_ascii=False, sort_keys=True))
    return 1 if manifest["summary"]["failed_or_missing"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
