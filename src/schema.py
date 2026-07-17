"""Phase 0 — Data contract for NL-Miner.

All later phases (blocker, linker, parsers, store) build on the models here.
Keep this file dependency-free except for pydantic.

Design refs: phase0_schema.md §1, ROADMAP "Cross-cutting 규칙".
"""
from __future__ import annotations

from enum import Enum
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


def canonical_id(label: str, pos: str = "NA") -> str:
    """Content-deterministic id for a *graph token/phrase* node.

    Same ``(label, pos)`` always yields the same id → two blocks that point
    at the same token collapse onto one shared (canonical) node, which is the
    mechanism behind the token-saving dedup demo. POS is absent until Phase 2,
    so callers pass the ``"NA"`` sentinel; when POS gets attached the id moves
    (intentional) and ``schema_version`` is bumped.
    """
    return sha256(f"{label}\x00{pos}".encode("utf-8")).hexdigest()[:16]


class FileMeta(BaseModel):
    """Identity + provenance for one source file."""

    file_path: Path
    file_name: str
    unique_id: UUID = Field(default_factory=uuid4)
    creator: str | None = None
    lang: str | None = None          # Phase 3 언어감지 결과
    mtime: float | None = None


class BlockKind(str, Enum):
    HEADING = "heading"
    PARA = "para"
    CODE = "code"
    LIST = "list"
    QUOTE = "quote"
    TABLE = "table"
    SENTENCE = "sentence"   # Phase 1 .txt 문장 분할 결과


class Block(BaseModel):
    """A structural block from a document — the unit the linker connects.

    ``span`` is an original-text char offset ``[start, end)`` so spans survive
    re-tokenisation (debugging / editor highlight). ``parent_block_id`` links
    a block under the nearest enclosing heading (blocker builds the tree).
    """

    block_id: UUID = Field(default_factory=uuid4)
    file_uuid: UUID
    kind: BlockKind
    level: int | None = None         # 헤딩 depth (#=1)
    text: str
    span: tuple[int, int]
    parent_block_id: UUID | None = None


class GraphNode(BaseModel):
    node_id: str                     # 정준 노드: canonical_id(label, pos)
    kind: str                        # token | phrase | block | canonical
    label: str
    ref_block_id: UUID | None = None
    # Dictionary form of ``label`` for token nodes (None elsewhere). Node
    # identity stays keyed on the *surface* form, so the graph still says what
    # the note says; the lemma rides alongside so concept queries can fold
    # "dogs"/"dog" — and, since the tagger lowercases it, "Camouflage"/
    # "camouflage" — onto one concept without merging the nodes themselves.
    lemma: str | None = None


class GraphEdge(BaseModel):
    src: str
    dst: str
    label: str                        # contains | modifies | ref-to | link | head | dep | sim | token | rep


class Context(BaseModel):
    """One file-processing session: the file plus the graph it produced."""

    run_id: UUID = Field(default_factory=uuid4)
    file: FileMeta
    blocks: list[Block] = Field(default_factory=list)
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    # Phase 2 (phase2_parsers.md §2.6): attaching real POS rehashes the
    # per-token canonical id ``canonical_id(label, pos)`` away from the
    # Phase-1 ``"NA"`` sentinel, so the payload must declare which id-scheme
    # its token nodes use. ``"0.1"`` = block-canonical only; ``"0.2"`` =
    # POS-attached token nodes present.
    schema_version: str = "0.1"


class Payload(BaseModel):
    """Serialization envelope: one File + its blocks/graph."""

    context: Context
    schema_version: str = "0.1"
