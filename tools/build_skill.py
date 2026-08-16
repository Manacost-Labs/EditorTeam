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

VENDOR_PACKAGES = ["pymorphy3", "pymorphy3_dicts_ru", "dawg_python", "dawg2_python"]


def site_packages() -> Path | None:
    for p in (ROOT / ".venv" / "lib").glob("python*/site-packages"):
        return p
    return None


def copy_core(dst: Path) -> None:
    for sub in ("scripts", "assets", "references"):
        src = SKILL / sub
        if src.exists():
            shutil.copytree(src, dst / sub, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "config", dst / "config")
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
            shutil.copytree(src, vendor / pkg,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "tests"))
            copied += 1
    return copied


def copy_corpus(dst: Path) -> int:
    src = ROOT / "гайды"
    if not src.exists():
        return 0
    shutil.copytree(src, dst / "гайды")
    return len(list((dst / "гайды").glob("*.md")))


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
    print(f"  распакованный:   {sum(f.stat().st_size for f in dst.rglob('*') if f.is_file()) / 1024 / 1024:.1f} МБ")
    print(f"  морфология:      {'вложена' if n_vendor else 'НЕТ'}")
    print(f"  корпус:          {n_corpus} документов" if n_corpus else "  корпус:          НЕТ")
    return archive


def smoke(dst: Path) -> bool:
    """Проверить, что собранный скилл работает системным Python без установки."""
    sample = dst / "_проба.md"
    sample.write_text(
        "Матч-апы\nОставляйте Мастера брони против агро колод. "
        "Но не спешите играть КелТузад рано.\n",
        encoding="utf-8",
    )
    # системный Python, а не .venv: иначе проба берёт установленные пакеты
    # и не проверяет главное — работает ли вложенная поставка
    py = shutil.which("python3") or sys.executable
    ok = True
    for script in ("markers.py", "soul.py", "rhythm.py", "structure.py",
                   "cards.py", "consistency.py", "author.py"):
        r = subprocess.run(
            [py, str(dst / "scripts" / script), str(sample)],
            capture_output=True, text=True,
            env={"PYTHONPATH": str(dst / "scripts"), "PATH": "/usr/bin:/bin"},
        )
        status = "ок" if r.returncode == 0 else f"ОШИБКА: {r.stderr.strip().splitlines()[-1:]}"
        print(f"    {script:<16} {status}")
        ok = ok and r.returncode == 0
    sample.unlink()
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description="Сборка автономного скилла")
    ap.add_argument("--без-корпуса", dest="no_corpus", action="store_true")
    ap.add_argument("--без-морфологии", dest="no_vendor", action="store_true")
    ap.add_argument("--проба", dest="smoke", action="store_true",
                    help="запустить анализаторы в собранном виде")
    args = ap.parse_args()

    build(with_corpus=not args.no_corpus, with_vendor=not args.no_vendor)
    if args.smoke:
        print("\n  проба системным Python, без установки:")
        return 0 if smoke(OUT / NAME) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
