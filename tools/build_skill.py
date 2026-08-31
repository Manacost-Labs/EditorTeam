#!/usr/bin/env python3
"""Сборка автономного скилла для claude.ai и Claude Code.

    python3 tools/build_skill.py                 — полная сборка
    python3 tools/build_skill.py --без-корпуса   — без памяти архива
    python3 tools/build_skill.py --без-морфологии

Скилл должен работать там, где нет ни установки пакетов, ни сети:
на Claude API сети нет никогда, на claude.ai она зависит от настроек.
Поэтому морфология вкладывается внутрь (она чистый Python), справочник
карт — снимком, а обновление необязательно.

Ступени отката, если предел загрузки не пустит полную сборку:
    полная         ~20 МБ   всё работает
    без корпуса    ~17 МБ   нет памяти архива и регрессии
    без морфологии ~1 МБ    нет коротких имён карт и нечётких проверок
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".claude" / "skills" / "hs-edit"
OUT = ROOT / "build"
NAME = "hearthstone-editor"
PLUGIN_NAME = "editor-team"
PLUGIN_VERSION = "1.0.0"

VENDOR_PACKAGES = ["pymorphy3", "pymorphy3_dicts_ru", "dawg_python", "dawg2_python", "yaml"]


def site_packages() -> Path | None:
    for p in (ROOT / ".venv" / "lib").glob("python*/site-packages"):
        return p
    return None


def copy_core(dst: Path) -> None:
    for sub in ("scripts", "assets", "references", "agents"):
        src = SKILL / sub
        if src.exists():
            shutil.copytree(src, dst / sub, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "config", dst / "config")
    shutil.copytree(
        ROOT / "src" / "editorteam",
        dst / "python" / "editorteam",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    corpus_docs = ROOT / "docs" / "corpus-learning.md"
    if corpus_docs.exists():
        shutil.copy(corpus_docs, dst / "references" / "corpus-learning.md")
    for doc in ("ГОЛОС.md", "СТИЛЬ.md"):
        if (ROOT / doc).exists():
            shutil.copy(ROOT / doc, dst / doc)


def copy_vendor(dst: Path) -> int:
    """Вложить морфологию. Она чистый Python — установка не нужна."""
    sp = site_packages()
    if not sp:
        print("! нет .venv — морфология не вложена", file=sys.stderr)
        return 0
    vendor = dst / "vendor"
    vendor.mkdir(parents=True, exist_ok=True)
    copied = 0
    for pkg in VENDOR_PACKAGES:
        src = sp / pkg
        if src.exists():
            shutil.copytree(
                src,
                vendor / pkg,
                ignore=shutil.ignore_patterns(
                    "__pycache__", "*.pyc", "*.so", "*.dylib", "*.pyd", "tests"
                ),
            )
            # Third-party wheels occasionally contain trailing whitespace or
            # several final blank lines. Normalise only the packaged copies so
            # release sources stay diff-clean without touching the venv.
            for py_file in (vendor / pkg).rglob("*.py"):
                source = py_file.read_text(encoding="utf-8")
                lines = [line.rstrip() for line in source.splitlines()]
                while lines and not lines[-1]:
                    lines.pop()
                normalised = "\n".join(lines) + "\n"
                py_file.write_text(normalised, encoding="utf-8")
            copied += 1
    return copied


def copy_corpus(dst: Path) -> int:
    src = ROOT / "гайды"
    if src.exists():
        shutil.copytree(src, dst / "гайды")
    managed = ROOT / "corpus"
    if managed.exists():
        shutil.copytree(managed, dst / "corpus")
    legacy = len(list((dst / "гайды").glob("*.md"))) if (dst / "гайды").exists() else 0
    if not (dst / "corpus" / "manifest.json").exists():
        return legacy
    state = json.loads((dst / "corpus" / "manifest.json").read_text(encoding="utf-8"))
    excluded = len(state.get("excluded_legacy_ids", []))
    approved = sum(1 for item in state.get("guides", []) if item.get("status") == "approved")
    return legacy - excluded + approved


def write_bootstrap(dst: Path, has_vendor: bool) -> None:
    """Подключение вложенной морфологии до импорта анализаторов."""
    (dst / "scripts" / "sitecustomize.py").write_text(
        '"""Подключает вложенные библиотеки: в песочнице нет ни pip, ни сети."""\n'
        "import sys\n"
        "from pathlib import Path\n\n"
        "_vendor = Path(__file__).resolve().parents[1] / 'vendor'\n"
        "if _vendor.exists() and str(_vendor) not in sys.path:\n"
        "    sys.path.insert(0, str(_vendor))\n",
        encoding="utf-8",
    )
    marker = dst / "scripts" / "VENDORED"
    marker.write_text("yes\n" if has_vendor else "no\n", encoding="utf-8")


def manifest(dst: Path, parts: dict) -> None:
    cards = json.loads((dst / "assets" / "cards-ru.json").read_text(encoding="utf-8"))
    (dst / "MANIFEST.json").write_text(
        json.dumps(
            {
                "name": NAME,
                "built_from": "EditorTeam",
                "cards_snapshot": cards.get("_снято"),
                "cards_count": len(cards["карты"]),
                "corpus_documents": parts["corpus"],
                "corpus_version": (
                    json.loads((dst / "corpus" / "manifest.json").read_text(encoding="utf-8")).get(
                        "current_version"
                    )
                    if (dst / "corpus" / "manifest.json").exists()
                    else "legacy-v1"
                ),
                "morphology_bundled": parts["vendor"] > 0,
                "note": "снимок карт обновляется скриптом scripts/update_cards.py, "
                "если в песочнице есть сеть",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def build(with_corpus: bool, with_vendor: bool) -> Path:
    dst = OUT / NAME
    if OUT.exists():
        shutil.rmtree(OUT)
    dst.mkdir(parents=True)

    copy_core(dst)
    n_vendor = copy_vendor(dst) if with_vendor else 0
    n_corpus = copy_corpus(dst) if with_corpus else 0
    write_bootstrap(dst, n_vendor > 0)
    manifest(dst, {"corpus": n_corpus, "vendor": n_vendor})

    src_skill = ROOT / "tools" / "SKILL.md"
    if src_skill.exists():
        shutil.copy(src_skill, dst / "SKILL.md")

    archive = OUT / f"{NAME}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(dst.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(OUT))

    size = archive.stat().st_size / 1024 / 1024
    print(f"\n{archive}")
    print(f"  размер архива:   {size:.1f} МБ")
    print(
        f"  распакованный:   {sum(f.stat().st_size for f in dst.rglob('*') if f.is_file()) / 1024 / 1024:.1f} МБ"
    )
    print(f"  морфология:      {'вложена' if n_vendor else 'НЕТ'}")
    print(f"  корпус:          {n_corpus} документов" if n_corpus else "  корпус:          НЕТ")
    return archive


def smoke(dst: Path) -> bool:
    """Проверить, что собранный скилл работает системным Python без установки."""
    sample = dst / "_проба.md"
    sample.write_text(
        "Матч-апы\nОставляйте ключевую карту против агрессивных колод. "
        "Но не спешите разыгрывать главный ресурс слишком рано.\n",
        encoding="utf-8",
    )
    # системный Python, а не .venv: иначе проба берёт установленные пакеты
    # и не проверяет главное — работает ли вложенная поставка
    py = shutil.which("python3") or sys.executable
    ok = True
    for script in (
        "markers.py",
        "soul.py",
        "rhythm.py",
        "structure.py",
        "cards.py",
        "consistency.py",
        "author.py",
        "guide_voice.py",
    ):
        r = subprocess.run(
            [py, str(dst / "scripts" / script), str(sample)],
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin"},
        )
        status = "ок" if r.returncode == 0 else f"ОШИБКА: {r.stderr.strip().splitlines()[-1:]}"
        print(f"    {script:<16} {status}")
        ok = ok and r.returncode == 0
    for script in ("certainty_guard.py", "semantic_diff.py"):
        r = subprocess.run(
            [py, str(dst / "scripts" / script), str(sample), str(sample)],
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin"},
        )
        status = "ок" if r.returncode == 0 else f"ОШИБКА: {r.stderr.strip().splitlines()[-1:]}"
        print(f"    {script:<16} {status}")
        ok = ok and r.returncode == 0
    cli_checks = (
        ("profiles", ["profiles"]),
        (
            "audit",
            ["audit", str(sample), "--profile", "constructed-guide", "--format", "json"],
        ),
        (
            "validate-edit",
            ["validate-edit", str(sample), str(sample), "--format", "json"],
        ),
        ("corpus inspect", ["corpus", "inspect", "--format", "json"]),
    )
    for label, arguments in cli_checks:
        cli = subprocess.run(
            [py, str(dst / "scripts" / "editor_team.py"), *arguments],
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin"},
        )
        print(
            f"    {'editor_team.py ' + label:<28} "
            f"{'ок' if cli.returncode == 0 else 'ОШИБКА: ' + cli.stderr.strip()}"
        )
        ok = ok and cli.returncode == 0
    sample.unlink()
    return ok


def build_plugin(skill_dir: Path) -> Path:
    """Формат ChatGPT Work/Codex plugin: plugin manifest + skills directory."""
    release = ROOT / "release"
    plugin = release / PLUGIN_NAME
    if plugin.exists():
        shutil.rmtree(plugin)
    packaged_skill = plugin / "skills" / NAME
    shutil.copytree(skill_dir, packaged_skill)
    manifest_dir = plugin / ".codex-plugin"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": PLUGIN_NAME,
                "version": PLUGIN_VERSION,
                "description": "Evidence-hidden Hearthstone guide editing with semantic guards and approved corpus learning.",
                "author": {"name": "Manacost Labs"},
                "skills": "./skills/",
                "interface": {
                    "displayName": "EditorTeam",
                    "shortDescription": "Hearthstone guide editing without evidence narration",
                    "longDescription": "Edits Russian Hearthstone guides while preserving claims, confidence, numbers and author voice; separates current game evidence from versioned style memory.",
                    "developerName": "Manacost Labs",
                    "category": "Productivity",
                    "capabilities": [
                        "Guide editing",
                        "Semantic validation",
                        "Card names",
                        "Style corpus",
                    ],
                    "defaultPrompt": "Edit this Hearthstone guide in GUIDE mode, preserve the claim contract and keep research evidence backstage.",
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    archive = release / f"{PLUGIN_NAME}-chatgpt-work-plugin-{PLUGIN_VERSION}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for path in sorted(plugin.rglob("*")):
            if path.is_file():
                bundle.write(path, path.relative_to(release))
    sums = []
    for path in sorted(release.glob("*.zip")):
        sums.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
    (release / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")
    print(f"  ChatGPT Work plugin: {archive}")
    return archive


def main() -> int:
    ap = argparse.ArgumentParser(description="Сборка автономного скилла")
    ap.add_argument("--без-корпуса", dest="no_corpus", action="store_true")
    ap.add_argument("--без-морфологии", dest="no_vendor", action="store_true")
    ap.add_argument(
        "--проба", dest="smoke", action="store_true", help="запустить анализаторы в собранном виде"
    )
    ap.add_argument("--release", action="store_true", help="собрать ChatGPT Work plugin в release/")
    args = ap.parse_args()

    build(with_corpus=not args.no_corpus, with_vendor=not args.no_vendor)
    if args.release:
        build_plugin(OUT / NAME)
    if args.smoke:
        print("\n  проба системным Python, без установки:")
        return 0 if smoke(OUT / NAME) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
