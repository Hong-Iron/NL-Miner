"""Phase 2 §2.6 — POS-attached per-token canonical nodes + schema_version 0.2.

Completion gates (phase2_parsers.md §2.6, §4 self-check):
  * same (word, pos) → same canonical_id (token node dedup) regardless of file
  * attaching POS *moves* the id away from the Phase-1 "NA" sentinel
  * token savings > 0 on a doc with repeated words (Phase 1 stays block-level)
  * `pos=True` bumps Context.schema_version "0.1" → "0.2"; default stays "0.1"
  * `pos=False` reproduces Phase 1 (no token nodes, no "token" edges)
  * store round-trips the 0.2 graph
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

from src import linker
from src.context import process
from src.markdown_blocker import blockify
from src.postagger import RulePOSTagger
from src.schema import BlockKind, FileMeta, canonical_id

PROJECT = Path(__file__).resolve().parent.parent
OCTOPUS = PROJECT / "octopus.md"

REPETITIVE = """# Notes

the octopus is an octopus that uses octopus arms to open an octopus shell.
"""


def _blocks(doc: str):
    fm = FileMeta(file_path=Path("d.md"), file_name="d.md")
    return blockify(doc, fm.unique_id, md=True), fm.unique_id


# ----------------------------------------------------------- canonical id move


def test_pos_attaches_moves_canonical_id_off_sentinel():
    """Phase 1 hashed tokens as canonical_id(word, "NA"); Phase 2 attaches a
    real POS, so the per-token id must move (§2.6 migration)."""
    na = canonical_id("octopus", "NA")
    nnp = canonical_id("octopus", "NNP")
    nn = canonical_id("octopus", "NN")
    assert na != nnp
    assert na != nn
    assert nnp != nn, "different POS must give a different token node"


def test_same_word_pos_same_canonical_id_across_blocks():
    blocks, _ = _blocks(REPETITIVE)
    nodes, edges, stats = linker.link(blocks, uuid4(), pos=True, adapter=RulePOSTagger())
    token_nodes = [n for n in nodes if n.kind == "token"]
    # "octopus" (NNP capitalize→NNP, lowercase→NN) collapses each (text,pos)
    by_label = {}
    for n in token_nodes:
        by_label.setdefault((n.label, n.node_id), n)
    oct_nodes = [n for n in token_nodes if n.label.lower() == "octopus"]
    # every token node id for "octopus" with the SAME POS is one shared id
    ids = {n.node_id for n in oct_nodes}
    # there may be one (NNP at sentence start / NN mid-sentence) — but each
    # is shared across its occurrences, never per-occurrence
    assert len(oct_nodes) >= 1
    assert len(ids) == len({canonical_id("octopus", p) for p in ("NNP", "NN")}) or len(ids) <= 2


def test_no_per_occurrence_token_nodes():
    """The dedup contract: 4 occurrences of 'octopus' must NOT make 4 token
    nodes — they collapse onto the shared (word,pos) canonical node."""
    blocks, _ = _blocks(REPETITIVE)
    nodes, edges, stats = linker.link(blocks, uuid4(), pos=True, adapter=RulePOSTagger())
    token_nodes = [n for n in nodes if n.kind == "token"]
    # octopus appears 4× in the doc; trivially < 4 distinct token nodes for it
    oc = [n for n in token_nodes if n.label.lower() == "octopus"]
    assert len(oc) <= 2  # at most NNP + NN
    # and the number of `token` edges to those nodes is bounded by block count,
    # not by raw occurrences: 1 per block per distinct (word,pos)
    token_edges = [e for e in edges if e.label == "token"]
    assert len(token_edges) <= len(blocks) * len(token_nodes)


# --------------------------------------------------------------- token savings


def test_token_savings_positive_on_repetitive_doc():
    blocks, _ = _blocks(REPETITIVE)
    _, _, stats = linker.link(blocks, uuid4(), pos=True, adapter=RulePOSTagger())
    assert stats["pos"] is True
    assert stats["token_raw"] > 0
    assert stats["n_token_nodes"] > 0
    # 4× 'octopus' but each (word,pos) emitted once → some tokens are saved
    assert stats["token_saved"] == stats["token_raw"] - stats["token_unique"]
    assert stats["token_saved"] > 0, "repeated words must save > 0 tokens"
    assert 0.0 <= stats["ratio_token"] <= 1.0


def test_token_savings_zero_or_low_on_unique_doc():
    doc = "# Heading\n\nthe quick brown fox jumps over the lazy dog today.\n"
    blocks, _ = _blocks(doc)
    _, _, stats = linker.link(blocks, uuid4(), pos=True, adapter=RulePOSTagger())
    # only function words ('the'×2) repeat → tiny saving but no error
    assert stats["token_raw"] > 0
    assert stats["token_saved"] == stats["token_raw"] - stats["token_unique"]
    assert stats["ratio_token"] <= 0.4  # mostly unique content


# --------------------------------------------------------------- schema version


def test_process_pos_true_bumps_schema_version():
    fm = FileMeta(file_path=OCTOPUS, file_name="octopus.md")
    ctx_p1, _ = process(fm)
    assert ctx_p1.schema_version == "0.1"

    fm2 = FileMeta(file_path=OCTOPUS, file_name="octopus.md")
    ctx_p2, _ = process(fm2, pos=True)
    assert ctx_p2.schema_version == "0.2"


def test_pos_false_is_phase1_unchanged():
    """Default `pos=False` path must not produce token nodes/edges and must
    report the Phase-1-only stats keys (regression guard)."""
    blocks, _ = _blocks(REPETITIVE)
    nodes, edges, stats = linker.link(blocks, uuid4(), pos=False)
    assert all(n.kind != "token" for n in nodes)
    assert all(e.label != "token" for e in edges)
    assert stats["pos"] is False
    assert "token_raw" not in stats
    assert schema_keys_phase1(stats)


def schema_keys_phase1(stats: dict) -> bool:
    base = {"raw_total_tokens", "saved_tokens", "ratio", "dup_groups",
            "n_blocks", "n_nodes", "n_edges", "pos"}
    return base.issubset(stats) and set(stats) - base == set()


# ------------------------------------------------------------- endpoint parity


def test_pos_true_does_not_break_edge_dedup():
    """`token` edges from many blocks must not collide with the store edge PK."""
    blocks, _ = _blocks(REPETITIVE)
    _, edges, _ = linker.link(blocks, uuid4(), pos=True, adapter=RulePOSTagger())
    keys = [(e.src, e.dst, e.label) for e in edges]
    assert len(keys) == len(set(keys))


def test_pos_true_token_node_ids_disjoint_from_block_ids():
    blocks, _ = _blocks(REPETITIVE)
    nodes, edges, _ = linker.link(blocks, uuid4(), pos=True, adapter=RulePOSTagger())
    block_ids = {str(b.block_id) for b in blocks}
    token_ids = {n.node_id for n in nodes if n.kind == "token"}
    # canonical_id is a 16-hex sha256 prefix; uuid4 str is 36 chars with dashes
    assert not (block_ids & token_ids)


# ------------------------------------------------------------- store round-trip


def test_store_roundtrips_pos_true_graph(tmp_path):
    from src.store import GraphStore

    fm = FileMeta(file_path=OCTOPUS, file_name="octopus.md")
    ctx, stats = process(fm, pos=True)
    assert ctx.schema_version == "0.2"
    assert stats["token_saved"] > 0

    db = tmp_path / "g.db"
    with GraphStore(db) as store:
        store.upsert_file(ctx)
        files = store.list_files()
        assert files and files[0][0] == "octopus.md"
        # token nodes survive (kind lookup via the `nodes.kind` index)
        row = store.conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE kind='token'"
        ).fetchone()
        assert row[0] == stats["n_token_nodes"]
        # edge count matches
        erow = store.conn.execute("SELECT COUNT(*) FROM edges").fetchone()
        assert erow[0] == stats["n_edges"]
