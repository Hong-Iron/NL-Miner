"""Phase 3 — CLI ``view`` subcommand: the parse-graph JSON snapshot.

``view <file> --parse --json`` is the single data source for the Obsidian
plugin's graph view + decorations. It is computed in-memory from
:func:`src.context.process` (no store), so it mirrors exactly what the linker
+ parsers produce for one file. This test pins the JSON contract the TS
``types.ts`` mirrors.

Fixture: a crafted doc ("The octopus is an animal. The dog walked to the park.")
known from ``test_phase2_parse_linker`` to yield NP/VP/S/PP phrase nodes and
nsubj/det/obj dep edges under the rule grammar.
"""
from __future__ import annotations

import json
from pathlib import Path

from interface.cli import main

# verified to parse: NP/VP/S/PP phrases + det/nsubj/obj deps
CRAFTED = "# Notes\n\nThe octopus is an animal. The dog walked to the park.\n"


def _view(argv, capsys):
    rc = main(argv)
    out = capsys.readouterr()
    return rc, out.out, out.err


def _snap(capsys, path: Path, *, parse=True):
    argv = ["view", str(path)]
    if parse:
        argv.append("--parse")
    argv.append("--json")
    rc, out, err = _view(argv, capsys)
    assert rc == 0, err
    return json.loads(out)


# --------------------------------------------------------------- snapshot shape


def test_view_parse_snapshot_keys(tmp_path, capsys):
    f = tmp_path / "crafted.md"
    f.write_text(CRAFTED)
    d = _snap(capsys, f, parse=True)
    assert d["doc"] == "crafted.md"
    assert d["schema_version"] == "0.2"
    assert d["parse"] is True and d["pos"] is True
    assert d["file_uuid"]
    for k in ("stats", "blocks", "nodes", "edges"):
        assert k in d
    assert d["stats"]["n_phrase_nodes"] > 0
    assert d["stats"]["n_dep_edges"] > 0
    assert d["stats"]["parsed_sentences"] >= 2

    # nodes carry kinds incl phrase + token (parse=True)
    kinds = {n["kind"] for n in d["nodes"]}
    assert "phrase" in kinds and "token" in kinds
    assert "block" in kinds
    # edges carry dep:<rel> + head + contains
    labels = {e["label"] for e in d["edges"]}
    assert any(l.startswith("dep:") for l in labels)
    assert "head" in labels and "contains" in labels


def test_view_blocks_carry_phrase_dep_breakdown(tmp_path, capsys):
    f = tmp_path / "crafted.md"
    f.write_text(CRAFTED)
    d = _snap(capsys, f, parse=True)
    prose = [b for b in d["blocks"] if b["kind"] in ("para", "sentence")]
    assert prose, "expected at least one prose block"
    assert any(b["n_phrase"] > 0 for b in prose)
    assert any(b["n_dep"] > 0 for b in prose)
    b = next(b for b in d["blocks"] if b["n_phrase"] > 0)
    assert b["phrases"]
    p = b["phrases"][0]
    for k in ("id", "label", "head_text"):
        assert k in p
    assert b["deps"]
    de = b["deps"][0]
    for k in ("src_text", "dst_text", "label"):
        assert k in de
    assert de["label"].startswith("dep:")


def test_view_no_parse_is_phase1(tmp_path, capsys):
    f = tmp_path / "crafted.md"
    f.write_text(CRAFTED)
    d = _snap(capsys, f, parse=False)
    assert d["schema_version"] == "0.1"
    assert d["parse"] is False and d["pos"] is False
    assert d["stats"]["n_phrase_nodes"] == 0
    assert d["stats"]["n_dep_edges"] == 0
    kinds = {n["kind"] for n in d["nodes"]}
    assert "phrase" not in kinds and "token" not in kinds
    assert all(not e["label"].startswith("dep:") for e in d["edges"])
    # all blocks report zero phrase/dep breakdown
    assert all(b["n_phrase"] == 0 and b["n_dep"] == 0 for b in d["blocks"])


def test_view_is_json_native_with_or_without_flag(tmp_path, capsys):
    # `--json` flag is symmetric — view always emits one JSON object
    f = tmp_path / "crafted.md"
    f.write_text(CRAFTED)
    d1 = _snap(capsys, f, parse=True)
    rc, out, err = _view(["view", str(f), "--parse"], capsys)
    assert rc == 0
    d2 = json.loads(out)
    assert d1["schema_version"] == d2["schema_version"]
    assert d1["stats"]["n_phrase_nodes"] == d2["stats"]["n_phrase_nodes"]


def test_view_missing_file_returns_nonzero(capsys):
    rc, out, err = _view(["view", str(Path("/no/such/file.md")), "--parse", "--json"], capsys)
    assert rc == 1
    assert "no such file" in err
