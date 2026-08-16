"""Бутстрап .venv должен работать и на Windows, где интерпретатор в Scripts."""

import common as C


def test_venv_python_found_on_posix_layout():
    py = C.venv_python()
    assert py is None or py.exists()


def test_windows_layout_is_recognised(tmp_path, monkeypatch):
    """До исправления путь был зашит как bin/python и Windows не находился."""
    fake_root = tmp_path
    scripts = fake_root / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "python.exe").write_text("")
    monkeypatch.setattr(C, "ROOT", fake_root)
    found = C.venv_python()
    assert found is not None
    assert found.name == "python.exe"


def test_missing_venv_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "ROOT", tmp_path)
    assert C.venv_python() is None
