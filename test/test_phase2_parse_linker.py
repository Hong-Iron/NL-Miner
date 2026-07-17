"""Phase 2 — linker consuming parser output (phrase + dependency edges).

Design refs: phase2_parsers.md §1 (linker extension), §2.6, ARCHITECTURE §2.3.
Completion gates:
  * parse=True emits phrase nodes (kind="phrase") from CKY trees
  * `contains` edges (block -> top S, parent -> child phrase), `head` edges
    (phrase -> head token node)
  * arc-eager arcs surface as `dep:<rel>` edges between token canonical nodes
  * parse implies pos: token nodes present, stats["pos"] True, schema 0.2
  * edge dedup holds (store PK-safe), phrase/token/block ids are disjoint
  * store round-trips the phrase + dep graph faithfully
  * parse=False (default) reproduces Phase 1 — no phrase/token/dep artifacts
"""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from src import linker
from src.context import process
from src.markdown_blocker import blockify
from src.postagger import RulePOSTagger
from src.schema import FileMeta, canonical_id

PROJECT = Path(__file__).resolve().parent.parent
OCTOPUS = PROJECT / "octopus.md"

CRAFTED = """# Notes

The octopus is an animal. The dog walked to the park.
"""


def _blocks(doc: str):
    fm = FileMeta(file_path=Path("d.md"), file_name="d.md")
    return blockify(doc, fm.unique_id, md=True), fm.unique_id


# ----------------------------------------------------------- phrase + dep edges


def _parsed(doc=CRAFTED):
    blocks, _ = _blocks(doc)
    return linker.link(blocks, uuid4(), parse=True, adapter=RulePOSTagger())


def test_parse_emits_phrase_nodes():
    nodes, edges, stats = _parsed()
    phrase = [n for n in nodes if n.kind == "phrase"]
    assert len(phrase) > 0
    labels = {n.label for n in phrase}
    assert {"NP", "VP", "S"} <= labels
    assert "PP" in labels                        # "to the park" -> PP
    assert stats["n_phrase_nodes"] == len(phrase)
    assert stats["parsed_sentences"] >= 2


def test_parse_emits_head_and_contains_edges():
    nodes, edges, stats = _parsed()
    labels = [e.label for e in edges]
    assert sum(1 for l in labels if l == "head") > 0
    assert sum(1 for l in labels if l == "contains") > 0


def test_parse_emits_dependency_edges():
    nodes, edges, stats = _parsed()
    dep_labels = {e.label for e in edges if e.label.startswith("dep:")}
    assert "dep:nsubj" in dep_labels             # subject -> verb
    assert "dep:det" in dep_labels                # determiner <- noun
    assert "dep:obj" in dep_labels                # verb -> object
    assert stats["n_dep_edges"] > 0


# ----------------------------------------------------------- invariants


def test_parse_no_duplicate_edges():
    nodes, edges, stats = _parsed()
    keys = [(e.src, e.dst, e.label) for e in edges]
    assert len(keys) == len(set(keys)), "linker must dedup (src,dst,label)"


def test_parse_ids_partition():
    nodes, edges, stats = _parsed()
    blocks, _ = _blocks(CRAFTED)
    block_ids = {str(b.block_id) for b in blocks}
    token_ids = {n.node_id for n in nodes if n.kind == "token"}
    phrase_ids = {n.node_id for n in nodes if n.kind == "phrase"}
    # canonical ids (16-hex) live in a disjoint space from uuid block ids (36)
    assert not (block_ids & token_ids)
    assert not (block_ids & phrase_ids)
    # phrase and token canonical ids are also distinct (different hash inputs)
    assert not (token_ids & phrase_ids)


def test_parse_implies_pos_token_layer():
    nodes, edges, stats = _parsed()
    assert stats["pos"] is True
    assert any(n.kind == "token" for n in nodes)
    assert stats["token_saved"] == stats["token_raw"] - stats["token_unique"]


# ----------------------------------------------------------- schema + corpus


def test_process_parse_bumps_schema_version():
    fm = FileMeta(file_path=OCTOPUS, file_name="octopus.md")
    ctx, stats = process(fm, parse=True)
    assert ctx.schema_version == "0.2"
    assert "n_phrase_nodes" in stats
    assert "n_dep_edges" in stats
    # dependency arcs fire on the corpus even when CKY trees are absent
    # (arc-eager only needs POS-pair rules; full S is the harder ask)
    assert stats["n_dep_edges"] > 0
    assert stats["pos"] is True


def test_parse_only_adds_structure():
    # parse edges are additive: a parse run never loses the phase-1 + token edges
    blocks_p, _ = _blocks(CRAFTED)
    _, _, p1 = linker.link(blocks_p, uuid4(), pos=True, adapter=RulePOSTagger())
    blocks_q, _ = _blocks(CRAFTED)
    _, _, p2 = linker.link(blocks_q, uuid4(), parse=True, adapter=RulePOSTagger())
    assert p2["n_edges"] >= p1["n_edges"]


# ----------------------------------------------------------- store round-trip


def test_store_roundtrips_parse_graph(tmp_path):
    from src.store import GraphStore

    fm = FileMeta(file_path=OCTOPUS, file_name="octopus.md")
    ctx, stats = process(fm, parse=True)
    assert ctx.schema_version == "0.2"
    assert stats["n_dep_edges"] > 0

    db = tmp_path / "g.db"
    with GraphStore(db) as store:
        store.upsert_file(ctx)
        n_phrases = store.conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE kind='phrase'"
        ).fetchone()[0]
        assert n_phrases == stats["n_phrase_nodes"]

        total_nodes = store.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        assert total_nodes == stats["n_nodes"]

        total_edges = store.conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        assert total_edges == stats["n_edges"]

        dep_rows = store.conn.execute(
            "SELECT COUNT(*) FROM edges WHERE label LIKE 'dep:%'"
        ).fetchone()[0]
        # stats must reflect the FINAL deduped graph exactly: the same canonical
        # dep edge recurs across sentences and is collapsed, so the stat must
        # equal the store's persisted row count (not the pre-dedup arc count).
        assert dep_rows == stats["n_dep_edges"]
        phrase_rows = store.conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE kind='phrase'"
        ).fetchone()[0]
        assert phrase_rows == stats["n_phrase_nodes"]
        assert dep_rows > 0


# ----------------------------------------------------------- regression


def test_parse_false_is_phase1_unchanged():
    blocks, _ = _blocks(CRAFTED)
    nodes, edges, stats = linker.link(blocks, uuid4(), parse=False)
    assert all(n.kind != "phrase" for n in nodes)
    assert all(n.kind != "token" for n in nodes)
    assert all(not e.label.startswith("dep:") for e in edges)
    assert all(e.label != "head" for e in edges)
    assert "n_phrase_nodes" not in stats
    assert stats["pos"] is False
    assert "token_raw" not in stats
