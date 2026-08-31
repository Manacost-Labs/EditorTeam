#!/usr/bin/env python3
"""Переносимый вход в EditorTeam CLI для собранного скилла."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
for candidate in (SKILL / "vendor", SKILL / "python", SKILL.parents[2] / "src"):
    if candidate.exists() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from editorteam.cli import main  # noqa: E402, I001


if __name__ == "__main__":
    raise SystemExit(main())
