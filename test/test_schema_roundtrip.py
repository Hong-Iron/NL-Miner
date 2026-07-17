"""Phase 0 — schema round-trip + canonical-id purity.

Completion gate (phase0_schema.md §4):
  * Payload.model_validate_json(Payload(...).model_dump_json()) 동일성 보장
  * 같은 (label, pos) → 항상 같은 node_id
  * 외부 의존성 = pydantic 한 줄
"""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from src.schema import Block, BlockKind, Context, FileMeta, GraphEdge, GraphNode, Payload, canonical_id


def test_context_roundtrip():
    file = FileMeta(file_path=Path("octopus.md"), file_name="octopus.md")
    ctx = Context(file=file)
    ctx.blocks.append(
        Block(file_uuid=file.unique_id, kind=BlockKind.HEADING, level=1, text="Title", span=(0, 6))
    )
    ctx.nodes.append(GraphNode(node_id=canonical_id("cat", "NN"), kind="token", label="cat"))
    ctx.edges.append(GraphEdge(src="x", dst="y", label="contains"))

    ctx2 = Context.model_validate_json(ctx.model_dump_json())
    assert ctx == ctx2
    assert ctx2.blocks[0].kind is BlockKind.HEADING
    assert ctx2.blocks[0].parent_block_id is None
    # top-level Block without parent must be representable
    assert Block(file_uuid=file.unique_id, kind=BlockKind.PARA, text="t", span=(0, 1)).parent_block_id is None


def test_payload_roundtrip_preserves_uuid_and_span():
    file = FileMeta(file_path=Path("a.md"), file_name="a.md")
    ctx = Context(file=file)
    p = Payload(context=ctx)
    p2 = Payload.model_validate_json(p.model_dump_json())
    assert p == p2
    assert p2.context.file.unique_id == file.unique_id   # UUID survives as UUID
    assert isinstance(p2.context.blocks, list)           # default_factory freshness


def test_canonical_id_is_pure_and_default_na():
    same = canonical_id("cat", "NN")
    assert same == canonical_id("cat", "NN")             # determinism
    assert same != canonical_id("cat", "VBD")           # POS-sensitive
    assert same != canonical_id("dog", "NN")             # label-sensitive
    assert canonical_id("cat") == canonical_id("cat", "NA")   # default sentinel


def test_canonical_node_dedup_collapses_two_blocks():
    # Core Phase-1 invariant: two blocks with the same token → one node id.
    a = canonical_id("the", "NA")
    b = canonical_id("the", "NA")
    assert a == b
    assert {a, b}.__len__() == 1
