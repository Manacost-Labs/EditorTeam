"""Подключает вложенные библиотеки: в песочнице нет ни pip, ни сети."""
import sys
from pathlib import Path

_vendor = Path(__file__).resolve().parents[1] / 'vendor'
if _vendor.exists() and str(_vendor) not in sys.path:
    sys.path.insert(0, str(_vendor))
