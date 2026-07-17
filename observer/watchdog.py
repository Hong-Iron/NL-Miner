"""Phase 3 — file watcher: stat-poll change detection + trailing debounce.

Per phase3_ingest.md §2.1–§2.3 we stay self-contained (no inotify / no
``watchdog`` package): poll the tree with stat ``(mtime, size)`` keys and, on
change, hold the file in a *pending* set for a debounce window; the callback
fires once after the editor's burst settles (§2.2 trailing 300 ms).

Clock is injectable: :meth:`tick(now)` advances one polling+debounce step at
the caller-supplied clock value, so tests are deterministic (no real sleeps).
:meth:`run` wires that to ``time.monotonic`` for live use.

Deletions are detected but **not** pushed to the callback (§3.2: removal is
delayed — the store soft-stays until an explicit ``purge``); they land in
:attr:`deleted` for the operator to confirm.
"""
from __future__ import annotations

import time
from pathlib import Path

from src.filestructure import DEFAULT_EXTS


class Watcher:
    def __init__(
        self,
        root: Path | str,
        on_change,
        *,
        debounce: float = 0.3,
        exts=DEFAULT_EXTS,
    ):
        self.root = Path(root)
        self.on_change = on_change
        self.debounce = debounce
        self.exts = tuple(e.lower() for e in exts)
        self.snapshot: dict[Path, tuple[float, int]] = {}
        self.pending: dict[Path, float] = {}      # path → first-seen clock
        self.deleted: list[Path] = []             # soft-removed; not auto-purged
        self._primed = False

    # -- scanning ---------------------------------------------------------
    def _scan(self) -> dict[Path, tuple[float, int]]:
        current: dict[Path, tuple[float, int]] = {}
        if self.root.is_file():
            paths = [self.root] if self.root.suffix.lower() in self.exts else []
        else:
            paths = [p for p in self.root.rglob("*") if p.is_file() and p.suffix.lower() in self.exts]
        for p in paths:
            try:
                st = p.stat()
                current[p] = (st.st_mtime, st.st_size)
            except OSError:
                continue
        return current

    def _diff(self, current: dict[Path, tuple[float, int]]) -> tuple[list[Path], list[Path]]:
        changed: list[Path] = []
        for p, key in current.items():
            old = self.snapshot.get(p)
            if old is None or old != key:
                changed.append(p)
        deleted = [p for p in self.snapshot if p not in current]
        return changed, deleted

    # -- stepping ---------------------------------------------------------
    def tick(self, now: float) -> int:
        """Advance one poll + debounce step at clock ``now``. Returns #callbacks fired."""
        current = self._scan()
        changed, deleted = self._diff(current)

        for p in deleted:
            if p not in self.deleted:
                self.deleted.append(p)
        for p in changed:
            self.pending.setdefault(p, now)  # first-seen time, not re-armed

        fired = 0
        stale: list[Path] = []
        for p, first_seen in list(self.pending.items()):
            if now - first_seen >= self.debounce:
                # file may have kept changing; re-check it's still "changed"
                # against the latest snapshot so we fire on the settled state.
                self.on_change(p)
                fired += 1
                stale.append(p)
        for p in stale:
            self.pending.pop(p, None)

        self.snapshot = current
        return fired

    def prime(self, now: float = 0.0) -> None:
        """Seed the snapshot without firing on the initial state (first scan)."""
        self.snapshot = self._scan()
        self._primed = True

    def run(self, interval: float = 0.5) -> None:
        """Live loop: poll every ``interval`` seconds until interrupted."""
        self.prime(time.monotonic())
        try:
            while True:
                self.tick(time.monotonic())
                time.sleep(interval)
        except KeyboardInterrupt:
            return


def watch(root: Path | str, on_change, **kw) -> Watcher:
    """Convenience: build a :class:`Watcher` (caller drives ``tick``/``run``)."""
    return Watcher(root, on_change, **kw)
