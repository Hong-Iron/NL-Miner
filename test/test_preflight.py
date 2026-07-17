"""Environment failures must be reported as environment failures.

Without the preflight the user sees a TypeError from inside pydantic (Python too
old) or an AttributeError in serialisation (pydantic 1) — neither names the real
problem. These pin that the check fires, names it, and stays quiet when the
environment is fine.
"""
import sys

import pytest

from src import preflight


def test_current_environment_passes():
    # the suite runs on a supported interpreter, so there must be no problems
    assert preflight.check(exit_on_error=False) == []


def test_old_python_is_named(monkeypatch):
    monkeypatch.setattr(sys, "version_info", (3, 9, 0))
    problems = preflight.check(exit_on_error=False)
    assert any("3.10" in p and "3.9" in p for p in problems)


def test_missing_pydantic_is_named(monkeypatch):
    import builtins
    real = builtins.__import__

    def fake(name, *a, **k):
        if name == "pydantic":
            raise ModuleNotFoundError("No module named 'pydantic'")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)
    problems = preflight.check(exit_on_error=False)
    assert any("pydantic is not installed" in p for p in problems)
    assert any("pip install" in p for p in problems)


def test_pydantic_v1_is_named(monkeypatch):
    import pydantic
    monkeypatch.setattr(pydantic, "VERSION", "1.10.13", raising=False)
    problems = preflight.check(exit_on_error=False)
    assert any("pydantic 2 required" in p for p in problems)


def test_exit_on_error_raises_systemexit(monkeypatch):
    monkeypatch.setattr(sys, "version_info", (3, 9, 0))
    with pytest.raises(SystemExit) as ei:
        preflight.check(exit_on_error=True)
    assert ei.value.code == 2


def test_new_store_is_owner_only(tmp_path):
    """The store holds every block's text — the user's notes. A fresh db must
    not be world-readable (sqlite defaults to 0644)."""
    import os
    import stat
    from src.store import GraphStore

    db = tmp_path / "g.db"
    GraphStore(db).close()
    if os.name == "posix":  # POSIX bits are meaningful; Windows uses ACLs
        mode = stat.S_IMODE(db.stat().st_mode)
        assert mode & 0o077 == 0, f"world/group access on {oct(mode)}"
