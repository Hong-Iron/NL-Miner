"""Phase 3 — watcher: stat-poll change detection + trailing debounce (virtual clock)."""
from __future__ import annotations

from pathlib import Path

from observer.watchdog import Watcher


def test_change_fires_once_after_debounce(tmp_path):
    f = tmp_path / "f.md"
    f.write_text("# a\n", encoding="utf-8")
    w = Watcher(tmp_path, on_change=lambda p: fired.append(p), debounce=0.3)
    w.prime(now=0.0)
    fired: list = []

    # edit
    f.write_text("# a plus more text\n", encoding="utf-8")
    w.on_change = lambda p: fired.append(p)  # bind collector
    # within debounce window → must not fire yet
    assert w.tick(now=0.1) == 0 and fired == []
    # still within window
    assert w.tick(now=0.2) == 0 and fired == []
    # past window → fires exactly once
    assert w.tick(now=0.5) == 1
    assert len(fired) == 1 and fired[0] == f


def test_burst_settles_to_one_callback(tmp_path):
    f = tmp_path / "g.md"
    f.write_text("x\n", encoding="utf-8")
    fired: list = []
    w = Watcher(tmp_path, on_change=lambda p: fired.append(p), debounce=0.3)
    w.prime(now=0.0)

    # burst of rapid edits while in the debounce window
    f.write_text("y1\n", encoding="utf-8")
    w.tick(now=0.02)
    f.write_text("y2\n", encoding="utf-8")
    w.tick(now=0.04)
    f.write_text("z\n", encoding="utf-8")
    w.tick(now=0.06)
    # settled; past the debounce window → exactly one fire
    assert w.tick(now=0.4) == 1
    assert len(fired) == 1


def test_unchanged_does_not_fire(tmp_path):
    f = tmp_path / "h.md"
    f.write_text("stable\n", encoding="utf-8")
    fired: list = []
    w = Watcher(tmp_path, on_change=lambda p: fired.append(p), debounce=0.1)
    w.prime(now=0.0)
    assert w.tick(now=1.0) == 0
    assert fired == []


def test_new_file_detected(tmp_path):
    fired: list = []
    w = Watcher(tmp_path, on_change=lambda p: fired.append(p), debounce=0.2)
    w.prime(now=0.0)
    new = tmp_path / "new.md"
    new.write_text("fresh\n", encoding="utf-8")
    assert w.tick(now=0.0) == 0   # detected, pending
    assert w.tick(now=0.3) == 1
    assert fired == [new]


def test_deletion_is_soft_not_fired(tmp_path):
    f = tmp_path / "del.md"
    f.write_text("bye\n", encoding="utf-8")
    fired: list = []
    w = Watcher(tmp_path, on_change=lambda p: fired.append(p), debounce=0.1)
    w.prime(now=0.0)
    f.unlink()
    assert w.tick(now=0.2) == 0     # deletion is NOT a change callback
    assert fired == []
    assert f in w.deleted             # but it is recorded for explicit purge
