"""Phase 1 payload — JSONL save/load of the :class:`Payload` envelope.

Phase 1 stores one Payload per file as a single JSONL line under ``payload/``
(phase1_blocks_links.md §1 `payload.py`, phase0 §2.4 "왜 JSONL이고 SQLite가
아닌가": zero deps, line-diff friendly, incremental append). SQLite lands in
Phase 3; ``Payload`` will abstract both stores.
"""
from __future__ import annotations

from pathlib import Path

from src.schema import Context, Payload

DEFAULT_OUTDIR = Path("payload")


def save(ctx: Context, outdir: Path | str = DEFAULT_OUTDIR) -> Path:
    """Write ``ctx`` as one Payload JSONL line → ``<outdir>/<file_name>.jsonl``.

    The Payload's ``schema_version`` mirrors ``ctx.schema_version`` so a
    POS-attached graph (``"0.2"``) is distinguishable from a Phase-1 graph
    (``"0.1"``) on disk (phase2_parsers.md §2.6).
    """
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    payload = Payload(context=ctx, schema_version=ctx.schema_version)
    path = outdir / f"{ctx.file.file_name}.jsonl"
    path.write_text(payload.model_dump_json() + "\n", encoding="utf-8")
    return path


def load(path: Path | str) -> Context:
    """Read the first Payload line from ``path`` → its ``Context``."""
    line = Path(path).read_text(encoding="utf-8").splitlines()[0]
    return Payload.model_validate_json(line).context


def load_all(path: Path | str) -> list[Context]:
    """Read every Payload line from a multi-line JSONL file."""
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(Payload.model_validate_json(line).context)
    return out
