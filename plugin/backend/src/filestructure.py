"""Phase 1 — directory discovery → FileMeta.

Replaces the former two-line stub (the old ``translator.py`` just held
``root_dir``/``test_dir``). Walks a root path and emits a :class:`FileMeta`
per ``.md``/``.txt`` file (phase1_blocks_links.md §1 `FileStructure`).
"""
from __future__ import annotations

from pathlib import Path

from .schema import FileMeta

DEFAULT_EXTS = (".md", ".txt")


def discover(root: Path | str, exts: tuple[str, ...] = DEFAULT_EXTS) -> list[FileMeta]:
    """Return ``FileMeta`` for every supported file under ``root``.

    If ``root`` itself is a matching file, return just that one (lets the
    Phase 1 CLI target a single file). ``mtime`` is captured so Phase 3's
    incremental scan can skip unchanged files.
    """
    root = Path(root)
    exts = tuple(e.lower() for e in exts)
    if root.is_file():
        files = [root] if root.suffix.lower() in exts else []
    else:
        files = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in exts]
    out: list[FileMeta] = []
    for p in sorted(files):
        try:
            mtime = p.stat().st_mtime
        except OSError:
            mtime = None
        out.append(FileMeta(file_path=p.resolve(), file_name=p.name, mtime=mtime))
    return out
