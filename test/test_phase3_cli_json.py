"""Phase 3 — CLI ``--json`` output contract for the Obsidian plugin bridge.

The plugin spawns ``python -m interface.cli ... --json`` and parses one JSON
object on stdout, so the shape here is the cross-language contract. Default
text output is unchanged (regression guard) — the JSON path is gated on
``--json`` and never interleaves prose with the JSON line.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from interface.cli import main

PROJECT = Path(__file__).resolve().parent.parent
OCTOPUS = PROJECT / "octopus.md"


def _run(argv, capsys):
    rc = main(argv)
    out = capsys.readouterr()
    return rc, out.out, out.err


# --------------------------------------------------------------- index --json


def test_index_json_shape(tmp_path, capsys):
    db = tmp_path / "g.db"
    # crafted doc is known to yield phrase + dep nodes under parse=True;
    # octopus.md's prose (mostly VBGN/PP-heavy sentences) won't always project
    # to full S trees, so the phrase assertion is pinned to the crafted doc.
    crafted = tmp_path / "crafted.md"
    crafted.write_text("# Notes\n\nThe octopus is an animal. The dog walked to the park.\n")
    rc, out, err = _run(["--db", str(db), "index", str(crafted),
                         "--parse", "--json"], capsys)
    assert rc == 0, err
    d = json.loads(out)        # stdout is exactly one JSON object
    assert d["ok"] is True
    assert d["db"] == str(db)
    assert d["parse"] is True and d["pos"] is True
    assert d["totals"]["files"] == len(d["files"]) == 1
    row = d["files"][0]
    assert row["name"] == "crafted.md"
    for k in ("n_blocks", "n_nodes", "n_edges", "lang", "raw_total_tokens",
              "saved_tokens", "n_phrase_nodes", "n_dep_edges"):
        assert k in row
    assert row["n_phrase_nodes"] > 0          # parse=True surfaced phrases
    assert row["n_dep_edges"] > 0


def test_index_default_text_output_unchanged(tmp_path, capsys):
    # without --json: human text, NOT a single JSON object
    db = tmp_path / "g.db"
    rc, out, err = _run(["--db", str(db), "index", str(OCTOPUS)], capsys)
    assert rc == 0
    assert "file(s) | tokens=" in out
    assert "store:" in out
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)                       # not pure JSON


# --------------------------------------------------------------- query --json


def test_query_json_blocks(tmp_path, capsys):
    db = tmp_path / "g.db"
    _run(["--db", str(db), "index", str(OCTOPUS)], capsys)
    rc, out, err = _run(["--db", str(db), "query", "octopus.md", "--json"], capsys)
    assert rc == 0
    d = json.loads(out)
    assert d["doc"] == "octopus.md"
    assert d["file_uuid"]
    assert len(d["blocks"]) > 0
    b = d["blocks"][0]
    for k in ("block_id", "kind", "level", "text", "span_start", "span_end",
              "parent_block_id"):
        assert k in b


def test_query_json_block_neighbours(tmp_path, capsys):
    db = tmp_path / "g.db"
    _run(["--db", str(db), "index", str(OCTOPUS)], capsys)
    # grab a heading block id from the block list
    rc, out, err = _run(["--db", str(db), "query", "octopus.md", "--json"], capsys)
    bid = json.loads(out)["blocks"][0]["block_id"]
    rc, out, err = _run(["--db", str(db), "query", "octopus.md",
                         "--block", bid, "--json"], capsys)
    assert rc == 0
    d = json.loads(out)
    assert d["doc"] == "octopus.md" and d["block"] == bid
    assert isinstance(d["out"], list) and isinstance(d["in"], list)
    assert all("node" in x and "label" in x for x in d["out"])


def test_query_default_text_output_unchanged(tmp_path, capsys):
    db = tmp_path / "g.db"
    _run(["--db", str(db), "index", str(OCTOPUS)], capsys)
    rc, out, err = _run(["--db", str(db), "query", "octopus.md"], capsys)
    assert rc == 0
    assert "blocks (file_uuid=" in out
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)


# --------------------------------------------------------------- graph --json


def test_graph_json_shape(tmp_path, capsys):
    db = tmp_path / "g.db"
    _run(["--db", str(db), "index", str(OCTOPUS)], capsys)
    rc, out, err = _run(["--db", str(db), "graph", "--json"], capsys)
    assert rc == 0
    d = json.loads(out)
    assert d["db"] == str(db)
    assert any(f["name"] == "octopus.md" for f in d["files"])
    assert isinstance(d["top_canonical"], list)
    # cross-note concept view, added alongside the (dup-block) top_canonical
    assert isinstance(d["top_concepts"], list)
    for c in d["top_concepts"]:
        assert set(c) >= {"node_id", "label", "n_files", "n_blocks"}


def test_graph_default_text_output_unchanged(tmp_path, capsys):
    db = tmp_path / "g.db"
    _run(["--db", str(db), "index", str(OCTOPUS)], capsys)
    rc, out, err = _run(["--db", str(db), "graph"], capsys)
    assert rc == 0
    assert "file(s)" in out and "shared concepts" in out
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)


# --------------------------------------------------------------- purge --json


def test_purge_json(tmp_path, capsys):
    db = tmp_path / "g.db"
    _run(["--db", str(db), "index", str(OCTOPUS)], capsys)
    rc, out, err = _run(["--db", str(db), "purge", "octopus.md", "--json"], capsys)
    assert rc == 0
    d = json.loads(out)
    assert d["ok"] is True and d["doc"] == "octopus.md" and d["purged"] == 1
