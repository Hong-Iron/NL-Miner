"""Context module — model re-exports + the one-file orchestrator :func:`process`.

Phase 0 made this a light re-export over :mod:`src.schema`. Phase 1 added the
block→link pipeline; Phase 3 wraps a preprocess step (frontmatter/normalise/lang)
around it (phase3_ingest.md §1). ``process`` stays the single entry point that
indexing, watching, and the CLI all go through.
"""
from __future__ import annotations

from . import linker
from .markdown_blocker import blockify
from .preprocess import preprocess
from .schema import (
    Block,
    BlockKind,
    Context,
    FileMeta,
    GraphEdge,
    GraphNode,
    Payload,
    canonical_id,
)

__all__ = [
    "Block",
    "BlockKind",
    "Context",
    "FileMeta",
    "GraphEdge",
    "GraphNode",
    "Payload",
    "canonical_id",
    "process",
]


def process(
    file: FileMeta, *, pos: bool = False, parse: bool = False
) -> tuple[Context, dict]:
    """Run one file through preprocess → blockify → link.

    Result: a populated ``Context`` (with enriched ``FileMeta`` — creator,
    mtime, lang are filled in by preprocess when absent) plus the linker's
    savings ``stats``.

    ``pos=False`` (default) keeps Phase 1 semantics and ``schema_version``
    ``"0.1"``. ``pos=True`` attaches POS via the configured
    :func:`src.postagger.get_adapter` (``NLM_MORPH`` env), adds per-token
    canonical nodes, and bumps ``ctx.schema_version`` to ``"0.2"``.

    ``parse=True`` additionally runs the Phase 2 parsers (CKY constituency +
    arc-eager dependency) over each prose block via :func:`src.linker.link`'s
    ``parse`` switch, attaching phrase/dep nodes. Parsing implies POS (it
    needs tagged tokens), so it also sets ``schema_version`` to ``"0.2"``
    (phase2_parsers.md §2, §2.6).
    """
    body, file = preprocess(file)
    md = file.file_path.suffix.lower() == ".md"
    blocks = blockify(body, file.unique_id, md=md)
    ctx = Context(file=file, blocks=blocks)
    ctx.nodes, ctx.edges, stats = linker.link(
        blocks, file.unique_id, pos=pos, parse=parse
    )
    if pos or parse:
        ctx.schema_version = "0.2"
    return ctx, stats
