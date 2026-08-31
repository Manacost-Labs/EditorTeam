"""Версионируемое обучение на одобренных автором гайдах.

Это только STYLE MEMORY. Патч, дата и метаданные хранятся для аудита,
но тексты корпуса никогда не становятся актуальным GAME KNOWLEDGE.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import statistics
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

SCHEMA_VERSION = 1
MIN_GENRE_SAMPLE = 3
DRIFT_LIMITS = {
    "sentence_length.mean": 2.0,
    "imperative_rate_per_1k.mean": 1.5,
    "reader_address_rate_per_1k.mean": 2.0,
    "short_sentence_rate_per_1k.mean": 4.0,
}
TERMS = ("винрейт", "процент побед", "муллиган", "матч-ап", "ладдер")
AI_MARKERS = (
    "стоит отметить",
    "важно понимать",
    "давайте разберемся",
    "подведем итог",
)


class CorpusError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class Document:
    id: str
    path: Path
    meta: dict
    text: str


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _normalise(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").lower().split())


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _split_front_matter(text: str) -> tuple[dict, str]:
    match = re.match(r"\A---\r?\n(.*?)\r?\n---\r?\n", text, re.S)
    if not match:
        return {}, text
    meta = {}
    for raw in match.group(1).splitlines():
        if ":" not in raw or raw.lstrip().startswith("#"):
            continue
        key, value = raw.split(":", 1)
        meta[key.strip()] = value.strip().strip("\"'")
    return meta, text[match.end() :]


def _slug(value: str) -> str:
    value = re.sub(r"[^a-z0-9а-яё]+", "-", value.lower(), flags=re.I).strip("-")
    return value[:72] or "guide"


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?…])\s+", text) if len(s.split()) > 1]


def _paragraphs(text: str) -> list[str]:
    return [p for p in re.split(r"\n\s*\n|\n(?=[A-ZА-ЯЁ])", text) if p.strip()]


def _robust(values: list[float]) -> dict:
    if not values:
        return {
            "n": 0,
            "mean": 0.0,
            "median": 0.0,
            "q1": 0.0,
            "q3": 0.0,
            "mad": 0.0,
            "trimmed_mean": 0.0,
        }
    ordered = sorted(values)
    n = len(ordered)
    cut = math.floor(n * 0.1)
    trimmed = ordered[cut : n - cut] if cut and n - cut > cut else ordered
    med = statistics.median(ordered)
    q = statistics.quantiles(ordered, n=4, method="inclusive") if n > 1 else [med, med, med]
    return {
        "n": n,
        "mean": round(statistics.fmean(ordered), 3),
        "median": round(med, 3),
        "q1": round(q[0], 3),
        "q3": round(q[2], 3),
        "mad": round(statistics.median(abs(v - med) for v in ordered), 3),
        "trimmed_mean": round(statistics.fmean(trimmed), 3),
    }


def _doc_metrics(text: str) -> dict:
    sentences = _sentences(text)
    paragraphs = _paragraphs(text)
    word_count = len(text.split())
    per_1k = 1000 / max(1, word_count)
    sentence_lengths = [len(s.split()) for s in sentences]
    lower = text.lower()
    markdown_headings = re.findall(r"^#{1,6}\s+(.+)$", text, re.M)
    plain_headings = [
        line.strip(" #:.")
        for line in text.splitlines()
        if 1 <= len(line.split()) <= 8
        and not re.search(r"[.!?]$", line.strip())
        and re.search(r"(муллиган|стратег|матч-ап|колод|карт|игры|вступлен|итог)", line, re.I)
    ]
    return {
        "words": word_count,
        "sentences": len(sentences),
        "paragraphs": len(paragraphs),
        "sentence_length": statistics.fmean(sentence_lengths) if sentence_lengths else 0.0,
        "sentence_length_variance": statistics.pvariance(sentence_lengths)
        if len(sentence_lengths) > 1
        else 0.0,
        "paragraph_length": statistics.fmean(len(p.split()) for p in paragraphs)
        if paragraphs
        else 0.0,
        "reader_address_rate_per_1k": len(re.findall(r"\b(вы|вам|ваш\w*)\b", lower)) * per_1k,
        "imperative_rate_per_1k": len(re.findall(r"\b\w+(?:айте|яйте|ите|ьте|йте)\b", lower))
        * per_1k,
        "contrast_rate_per_1k": len(re.findall(r"\b(но|однако|зато|хотя)\b", lower)) * per_1k,
        "short_sentence_rate_per_1k": sum(1 for n in sentence_lengths if n <= 6) * per_1k,
        "parenthetical_rate_per_1k": len(re.findall(r"\([^\n()]+\)", text)) * per_1k,
        "ai_marker_rate_per_1k": sum(lower.count(marker) for marker in AI_MARKERS) * per_1k,
        "terminology": {term: lower.count(term) for term in TERMS},
        "headings": [h.strip().lower() for h in [*markdown_headings, *plain_headings]],
    }


def compute_baseline(documents: list[Document], version: str) -> dict:
    metrics = {doc.id: _doc_metrics(doc.text) for doc in documents}

    def aggregate(ids: list[str]) -> dict:
        rows = [metrics[doc_id] for doc_id in ids]
        names = (
            "sentence_length",
            "sentence_length_variance",
            "paragraph_length",
            "reader_address_rate_per_1k",
            "imperative_rate_per_1k",
            "contrast_rate_per_1k",
            "short_sentence_rate_per_1k",
            "parenthetical_rate_per_1k",
            "ai_marker_rate_per_1k",
        )
        terms = Counter()
        headings = Counter()
        for row in rows:
            terms.update(row["terminology"])
            headings.update(row["headings"])
        return {
            "guides": len(rows),
            "words": sum(row["words"] for row in rows),
            "sentences": sum(row["sentences"] for row in rows),
            "metrics": {name: _robust([row[name] for row in rows]) for name in names},
            "terminology_frequency": dict(terms),
            "section_frequency": dict(headings.most_common(40)),
        }

    global_ids = [doc.id for doc in documents]
    genres: dict[str, list[str]] = {}
    for doc in documents:
        genres.setdefault(doc.meta.get("genre", "unknown"), []).append(doc.id)
    genre_baselines = {}
    for genre, ids in sorted(genres.items()):
        if len(ids) >= MIN_GENRE_SAMPLE:
            genre_baselines[genre] = {"fallback": False, **aggregate(ids)}
        else:
            genre_baselines[genre] = {
                "fallback": True,
                "reason": f"sample {len(ids)} < {MIN_GENRE_SAMPLE}",
                "uses": "global",
                "guides": len(ids),
            }
    return {
        "schema_version": SCHEMA_VERSION,
        "corpus_version": version,
        "generated_at": _now(),
        "global": aggregate(global_ids),
        "genres": genre_baselines,
    }


def compare_baselines(before: dict, after: dict) -> dict:
    bg, ag = before.get("global", {}), after.get("global", {})
    changes = {}
    for name, current in ag.get("metrics", {}).items():
        previous = bg.get("metrics", {}).get(name, {})
        changes[name] = {
            "before": previous.get("trimmed_mean", 0.0),
            "after": current.get("trimmed_mean", 0.0),
            "delta": round(current.get("trimmed_mean", 0.0) - previous.get("trimmed_mean", 0.0), 3),
        }
    term_changes = {}
    terms = set(bg.get("terminology_frequency", {})) | set(ag.get("terminology_frequency", {}))
    for term in sorted(terms):
        old = bg.get("terminology_frequency", {}).get(term, 0)
        new = ag.get("terminology_frequency", {}).get(term, 0)
        if old != new:
            term_changes[term] = {"before": old, "after": new, "delta": new - old}
    section_changes = {}
    sections = set(bg.get("section_frequency", {})) | set(ag.get("section_frequency", {}))
    for section in sorted(sections):
        old = bg.get("section_frequency", {}).get(section, 0)
        new = ag.get("section_frequency", {}).get(section, 0)
        if old != new:
            section_changes[section] = {"before": old, "after": new, "delta": new - old}
    drift = []
    for key, limit in DRIFT_LIMITS.items():
        metric, field = key.split(".", 1)
        delta = abs(
            ag.get("metrics", {}).get(metric, {}).get(field, 0.0)
            - bg.get("metrics", {}).get(metric, {}).get(field, 0.0)
        )
        if delta > limit:
            drift.append(
                {
                    "code": "CORPUS_DRIFT_WARNING",
                    "metric": metric,
                    "measure": field,
                    "delta": round(delta, 3),
                    "limit": limit,
                }
            )
    return {
        "guides": {"before": bg.get("guides", 0), "after": ag.get("guides", 0)},
        "words": {"before": bg.get("words", 0), "after": ag.get("words", 0)},
        "sentences": {"before": bg.get("sentences", 0), "after": ag.get("sentences", 0)},
        "style_changes": changes,
        "terminology_changes": term_changes,
        "structure_changes": {"section_frequency": section_changes},
        # Corpus updates recalculate statistical norms but never mutate the
        # configured editorial rules. Keeping this explicit makes compare
        # reports auditable instead of silently omitting the question.
        "rule_changes": [],
        "potential_drift": drift,
    }


class CorpusStore:
    def __init__(
        self,
        root: Path,
        regression_runner: Callable[[], bool] | None = None,
        quality_runner: Callable[[str, str], list[dict]] | None = None,
    ):
        self.root = Path(root).resolve()
        self.corpus_dir = self.root / "corpus"
        self.guides_dir = self.corpus_dir / "guides"
        self.snapshots_dir = self.corpus_dir / "snapshots"
        self.baselines_dir = self.corpus_dir / "baselines"
        self.manifest_path = self.corpus_dir / "manifest.json"
        self.baseline_path = self.corpus_dir / "baseline.json"
        self.legacy_dir = self.root / "гайды"
        self.regression_runner = regression_runner or self._default_regression
        self.quality_runner = quality_runner or self._default_quality

    def _read_json(self, path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_json_atomic(self, path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)

    def ensure(self) -> dict:
        self.guides_dir.mkdir(parents=True, exist_ok=True)
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.baselines_dir.mkdir(parents=True, exist_ok=True)
        if self.manifest_path.exists():
            manifest = self._read_json(self.manifest_path)
            if "active_baseline" not in manifest:
                version = manifest.get("current_version", "v1")
                baseline = (
                    self._read_json(self.baseline_path)
                    if self.baseline_path.exists()
                    else compute_baseline(self._documents(manifest), version)
                )
                manifest["active_baseline"] = f"corpus/baselines/{version}.json"
                for item in manifest.get("versions", []):
                    item.setdefault("baseline", f"corpus/baselines/{item['version']}.json")
                self._write_json_atomic(self.baselines_dir / f"{version}.json", baseline)
                self._write_json_atomic(self.manifest_path, manifest)
            return manifest
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "current_version": "v1",
            "active_baseline": "corpus/baselines/v1.json",
            "updated_at": _now(),
            "style_only": True,
            "knowledge_policy": "current patch/current meta evidence only",
            "excluded_legacy_ids": [],
            "guides": [],
            "versions": [],
        }
        docs = self._documents(manifest)
        baseline = compute_baseline(docs, "v1")
        snapshot = self._snapshot("v1", manifest, baseline, "bootstrap", [])
        manifest["versions"].append(self._version_entry(snapshot))
        self._write_json_atomic(self.baseline_path, baseline)
        self._write_json_atomic(self.baselines_dir / "v1.json", baseline)
        self._write_json_atomic(self.snapshots_dir / "v1.json", snapshot)
        self._write_json_atomic(self.manifest_path, manifest)
        return manifest

    def _version_number(self, manifest: dict) -> int:
        found = re.search(r"(\d+)$", manifest.get("current_version", "v0"))
        return int(found.group(1)) if found else 0

    def _next_version(self, manifest: dict) -> str:
        return f"v{self._version_number(manifest) + 1}"

    def _documents(self, manifest: dict) -> list[Document]:
        excluded = set(manifest.get("excluded_legacy_ids", []))
        out = []
        for path in sorted(self.legacy_dir.glob("*.md")) if self.legacy_dir.exists() else []:
            raw = path.read_text(encoding="utf-8")
            meta, text = _split_front_matter(raw)
            doc_id = meta.get("id", path.stem)
            if doc_id not in excluded:
                out.append(Document(doc_id, path, {**meta, "source": "legacy"}, text))
        for item in manifest.get("guides", []):
            if item.get("status") != "approved":
                continue
            path = self.root / item["path"]
            if not path.exists():
                continue
            meta, text = _split_front_matter(path.read_text(encoding="utf-8"))
            out.append(Document(item["id"], path, {**meta, **item}, text))
        return out

    def _snapshot(
        self, version: str, manifest: dict, baseline: dict, action: str, changed: list[str]
    ) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "version": version,
            "created_at": _now(),
            "action": action,
            "changed_guides": changed,
            "state": {
                "excluded_legacy_ids": list(manifest.get("excluded_legacy_ids", [])),
                "guides": manifest.get("guides", []),
            },
            "baseline": baseline,
        }

    def _version_entry(self, snapshot: dict) -> dict:
        return {
            "version": snapshot["version"],
            "created_at": snapshot["created_at"],
            "action": snapshot["action"],
            "changed_guides": snapshot["changed_guides"],
            "guides": snapshot["baseline"]["global"]["guides"],
            "snapshot": f"corpus/snapshots/{snapshot['version']}.json",
            "baseline": f"corpus/baselines/{snapshot['version']}.json",
        }

    def _baseline(self, manifest: dict) -> dict:
        active = manifest.get("active_baseline")
        if active and (self.root / active).exists():
            return self._read_json(self.root / active)
        if self.baseline_path.exists():
            return self._read_json(self.baseline_path)
        return compute_baseline(self._documents(manifest), manifest["current_version"])

    def _default_regression(self) -> bool:
        script = self.root / ".claude" / "skills" / "hs-edit" / "scripts" / "selftest.py"
        if not script.exists():
            return True
        py = self.root / ".venv" / "bin" / "python"
        command = [str(py if py.exists() else Path(sys.executable)), str(script)]
        candidate_path = self.corpus_dir / f".candidate-{os.getpid()}.json"
        env = os.environ.copy()
        candidate = getattr(self, "_candidate_manifest", None)
        if candidate is not None:
            self._write_json_atomic(candidate_path, candidate)
            env["EDITOR_CORPUS_MANIFEST"] = str(candidate_path)
        try:
            return (
                subprocess.run(command, cwd=self.root, timeout=300, check=False, env=env).returncode
                == 0
            )
        finally:
            candidate_path.unlink(missing_ok=True)

    def _default_quality(self, text: str, profile: str) -> list[dict]:
        try:
            from editorteam.server import _scripts, analyze

            findings = analyze(text, "hearthstone", profile, mode="GUIDE").get("findings", [])
            author = _scripts().sibling("author")
            score, _ = author.evaluate(text, author.load_tools())
            findings.append(
                {
                    "id": "author.score",
                    "severity": "info",
                    "message": f"соответствие авторской норме: {score}/10",
                }
            )
            return findings
        except Exception as exc:  # quality warnings must not silently reject a published guide
            return [{"id": "quality.unavailable", "severity": "review", "message": str(exc)}]

    def _activate(
        self, candidate: dict, action: str, changed: list[str], run_regression: bool = True
    ) -> dict:
        before_manifest = self.ensure()
        before = self._baseline(before_manifest)
        version = self._next_version(before_manifest)
        after = compute_baseline(self._documents(candidate), version)
        report = compare_baselines(before, after)
        self._candidate_manifest = candidate
        try:
            regression_ok = not run_regression or self.regression_runner()
        finally:
            self._candidate_manifest = None
        if not regression_ok:
            raise CorpusError(
                "CORPUS_REGRESSION_FAILED",
                "regression не прошла; активный corpus не изменен",
            )
        snapshot = self._snapshot(version, candidate, after, action, changed)
        candidate = json.loads(json.dumps(candidate))
        candidate["current_version"] = version
        candidate["active_baseline"] = f"corpus/baselines/{version}.json"
        candidate["updated_at"] = snapshot["created_at"]
        candidate.setdefault("versions", []).append(self._version_entry(snapshot))
        self._write_json_atomic(self.snapshots_dir / f"{version}.json", snapshot)
        self._write_json_atomic(self.baselines_dir / f"{version}.json", after)
        # Manifest is the activation pointer and is replaced last. A crash before
        # this line leaves the previous active state fully readable.
        self._write_json_atomic(self.manifest_path, candidate)
        self._write_json_atomic(self.baseline_path, after)
        return {"corpus_version": version, "regression": "PASS", **report}

    def _all_hashes(self, manifest: dict) -> tuple[dict[str, str], dict[str, str]]:
        raw_hashes, normalised_hashes = {}, {}
        for doc in self._documents({**manifest, "guides": [*manifest.get("guides", [])]}):
            raw = doc.path.read_text(encoding="utf-8")
            raw_hashes[_hash(raw)] = doc.id
            normalised_hashes[_hash(_normalise(doc.text))] = doc.id
        for item in manifest.get("guides", []):
            if item.get("status") == "approved":
                continue
            raw_hashes[item.get("sha256", "")] = item["id"]
            normalised_hashes[item.get("normalized_sha256", "")] = item["id"]
        return raw_hashes, normalised_hashes

    def add(
        self,
        source_file: Path,
        *,
        published_at: str,
        patch: str,
        author: str,
        tags: list[str],
        source: str,
        genre: str,
        approve: bool = False,
        guide_id: str | None = None,
    ) -> dict:
        manifest = self.ensure()
        path = Path(source_file)
        try:
            datetime.strptime(published_at, "%Y-%m-%d")
        except ValueError as exc:
            raise CorpusError(
                "CORPUS_METADATA_ERROR", "published_at должен быть YYYY-MM-DD"
            ) from exc
        if not patch.strip():
            raise CorpusError("CORPUS_METADATA_ERROR", "patch не может быть пустым")
        if approve and source not in {"published", "final"}:
            raise CorpusError(
                "CORPUS_NOT_PUBLISHED",
                "approved style corpus принимает только published/final guide",
            )
        try:
            raw = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise CorpusError("CORPUS_ENCODING_ERROR", "guide должен быть UTF-8") from exc
        except OSError as exc:
            raise CorpusError("CORPUS_SOURCE_ERROR", f"не удалось прочитать guide: {exc}") from exc
        if not raw.strip():
            raise CorpusError("CORPUS_EMPTY", "пустой guide нельзя добавить")
        meta, body = _split_front_matter(raw)
        raw_sha, normalised_sha = _hash(raw), _hash(_normalise(body))
        raw_hashes, normalised_hashes = self._all_hashes(manifest)
        duplicate = raw_hashes.get(raw_sha) or normalised_hashes.get(normalised_sha)
        if duplicate:
            raise CorpusError("CORPUS_DUPLICATE", f"guide уже есть в corpus: {duplicate}")
        title = meta.get("title") or next(
            (line.lstrip("# ") for line in body.splitlines() if line.strip()), path.stem
        )
        doc_id = guide_id or meta.get("id") or f"{_slug(title)}-{published_at[:7]}"
        if any(item["id"] == doc_id for item in manifest.get("guides", [])):
            raise CorpusError("CORPUS_DUPLICATE", f"guide id уже занят: {doc_id}")
        warnings = self.quality_runner(body, genre)
        entry = {
            "id": doc_id,
            "title": title,
            "path": f"corpus/guides/{_slug(doc_id)}.md",
            "published_at": published_at,
            "patch": patch,
            "author": author,
            "status": "approved" if approve else "candidate",
            "source": source,
            "sha256": raw_sha,
            "normalized_sha256": normalised_sha,
            "added_at": _now(),
            "approved_at": _now() if approve else None,
            "genre": genre,
            "tags": tags,
            "quality_warnings": warnings,
            "style_only": True,
        }
        candidate = json.loads(json.dumps(manifest))
        candidate.setdefault("guides", []).append(entry)
        destination = self.root / entry["path"]
        if destination.exists():
            raise CorpusError(
                "CORPUS_PATH_CONFLICT",
                f"путь managed guide уже занят: {destination.name}",
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        tmp = destination.with_suffix(destination.suffix + f".{os.getpid()}.tmp")
        tmp.write_text(raw, encoding="utf-8")
        try:
            if approve:
                os.replace(tmp, destination)
                try:
                    result = self._activate(candidate, "add-approved", [doc_id])
                except Exception:
                    destination.unlink(missing_ok=True)
                    raise
            else:
                os.replace(tmp, destination)
                version = self._next_version(manifest)
                baseline = json.loads(json.dumps(self._baseline(manifest)))
                baseline["corpus_version"] = version
                baseline["generated_at"] = _now()
                snapshot = self._snapshot(version, candidate, baseline, "add-candidate", [doc_id])
                candidate["current_version"] = version
                candidate["active_baseline"] = f"corpus/baselines/{version}.json"
                candidate["updated_at"] = snapshot["created_at"]
                candidate.setdefault("versions", []).append(self._version_entry(snapshot))
                self._write_json_atomic(self.snapshots_dir / f"{version}.json", snapshot)
                self._write_json_atomic(self.baselines_dir / f"{version}.json", baseline)
                self._write_json_atomic(self.manifest_path, candidate)
                self._write_json_atomic(self.baseline_path, baseline)
                result = {
                    "corpus_version": version,
                    "regression": "NOT_REQUIRED",
                    "guides": {
                        "before": baseline["global"]["guides"],
                        "after": baseline["global"]["guides"],
                    },
                }
        finally:
            tmp.unlink(missing_ok=True)
        return {"guide": entry, "quality_warnings": warnings, **result}

    def approve(self, guide_id: str) -> dict:
        manifest = self.ensure()
        candidate = json.loads(json.dumps(manifest))
        for item in candidate.get("guides", []):
            if item["id"] == guide_id:
                if item["status"] == "approved":
                    raise CorpusError("CORPUS_ALREADY_APPROVED", f"guide уже approved: {guide_id}")
                if item["status"] != "candidate":
                    raise CorpusError("CORPUS_NOT_CANDIDATE", f"guide не candidate: {guide_id}")
                if item.get("source") not in {"published", "final"}:
                    raise CorpusError(
                        "CORPUS_NOT_PUBLISHED",
                        "candidate не помечен как published/final",
                    )
                item["status"] = "approved"
                item["approved_at"] = _now()
                return self._activate(candidate, "approve", [guide_id])
        raise CorpusError("CORPUS_NOT_FOUND", f"guide не найден: {guide_id}")

    def reject(self, guide_id: str) -> dict:
        manifest = self.ensure()
        candidate = json.loads(json.dumps(manifest))
        for item in candidate.get("guides", []):
            if item["id"] == guide_id:
                if item["status"] != "candidate":
                    raise CorpusError("CORPUS_NOT_CANDIDATE", f"guide не candidate: {guide_id}")
                item["status"] = "rejected"
                item["rejected_at"] = _now()
                return self._activate(candidate, "reject", [guide_id])
        raise CorpusError("CORPUS_NOT_FOUND", f"guide не найден: {guide_id}")

    def remove(self, guide_id: str) -> dict:
        manifest = self.ensure()
        candidate = json.loads(json.dumps(manifest))
        for item in candidate.get("guides", []):
            if item["id"] == guide_id and item["status"] != "archived":
                item["status"] = "archived"
                item["archived_at"] = _now()
                return self._activate(candidate, "remove", [guide_id])
        legacy_ids = {doc.id for doc in self._documents({**candidate, "guides": []})}
        if guide_id in legacy_ids:
            candidate.setdefault("excluded_legacy_ids", []).append(guide_id)
            return self._activate(candidate, "remove-legacy", [guide_id])
        raise CorpusError("CORPUS_NOT_FOUND", f"guide не найден: {guide_id}")

    def versions(self) -> list[dict]:
        return self.ensure().get("versions", [])

    def rollback(self, version: str) -> dict:
        manifest = self.ensure()
        path = self.snapshots_dir / f"{version}.json"
        if not path.exists():
            raise CorpusError("CORPUS_VERSION_NOT_FOUND", f"нет corpus version {version}")
        state = self._read_json(path)["state"]
        candidate = json.loads(json.dumps(manifest))
        candidate["guides"] = state["guides"]
        candidate["excluded_legacy_ids"] = state["excluded_legacy_ids"]
        return self._activate(candidate, f"rollback-to-{version}", [])

    def inspect(self) -> dict:
        manifest = self.ensure()
        baseline = self._baseline(manifest)
        docs = self._documents(manifest)
        dates = sorted(
            doc.meta.get("published_at")
            for doc in docs
            if doc.meta.get("published_at") not in (None, "unknown")
        )
        genres = Counter(doc.meta.get("genre", "unknown") for doc in docs)
        statuses = Counter(item.get("status", "unknown") for item in manifest.get("guides", []))
        return {
            "current_version": manifest["current_version"],
            "approved_guides": len(docs),
            "managed_statuses": dict(statuses),
            "genres": dict(genres),
            "date_range": [dates[0], dates[-1]] if dates else [None, None],
            "words": baseline["global"]["words"],
            "sentences": baseline["global"]["sentences"],
            "last_update": manifest["updated_at"],
            "style_only": True,
            "knowledge_policy": "current patch/current meta evidence required",
        }

    def compare(self, before_version: str, after_version: str) -> dict:
        def load(version: str) -> dict:
            path = self.snapshots_dir / f"{version}.json"
            if not path.exists():
                raise CorpusError("CORPUS_VERSION_NOT_FOUND", f"нет corpus version {version}")
            return self._read_json(path)

        before, after = load(before_version), load(after_version)
        return {
            "before": before_version,
            "after": after_version,
            "action": after["action"],
            "changed_guides": after["changed_guides"],
            **compare_baselines(before["baseline"], after["baseline"]),
        }
