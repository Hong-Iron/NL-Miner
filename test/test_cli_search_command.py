"""Phase 3+ — CLI ``search`` subcommand: scoped block-text search.

``nlm search <pattern> --json`` is the token-saving lever the Claude Code
skill uses so an agent can "find blocks mentioning X" and load only those,
not every block's text. Returns matching rows (joined to file_name) with
case-insensitive substring matching over the store's ``blocks`` table.

Default text output is unchanged in spirit (non-JSON), pinned with the same
``json.JSONDecodeError`` regression guard the other CLI --json tests use.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from interface.cli import main

# verified to parse: NP/VP/S/PP phrases + det/nsubj/obj deps
CRAFTED = "# Notes\n\nThe octopus is an animal. The dog walked to the park.\n"


def _run(argv, capsys):
    rc = main(argv)
    out = capsys.readouterr()
    return rc, out.out, out.err


def _crafted(d: Path) -> Path:
    f = d / "crafted.md"
    f.write_text(CRAFTED)
    return f


def _index(db: Path, tmp_path: Path, capsys) -> None:
    rc, _, err = _run(["--db", str(db), "index", str(_crafted(tmp_path))], capsys)
    assert rc == 0, err


def test_search_json_finds_matching_blocks(tmp_path, capsys):
    db = tmp_path / "g.db"
    _index(db, tmp_path, capsys)
    rc, out, err = _run(["--db", str(db), "search", "octopus", "--json"], capsys)
    assert rc == 0, err
    d = json.loads(out)
    assert d["db"] == str(db)
    assert d["pattern"] == "octopus"
    assert d["count"] >= 1
    m = d["matches"][0]
    for k in ("doc", "block_id", "kind", "level", "text", "span_start", "span_end"):
        assert k in m
    assert "octopus" in m["text"].lower()
    assert m["doc"] == "crafted.md"


def test_search_case_insensitive(tmp_path, capsys):
    db = tmp_path / "g.db"
    _index(db, tmp_path, capsys)
    rc, out, err = _run(["--db", str(db), "search", "OCTOPUS", "--json"], capsys)
    assert rc == 0, err
    assert json.loads(out)["count"] >= 1


def test_search_no_match_returns_empty(tmp_path, capsys):
    db = tmp_path / "g.db"
    _index(db, tmp_path, capsys)
    rc, out, err = _run(["--db", str(db), "search", "nonexistent", "--json"], capsys)
    assert rc == 0
    d = json.loads(out)
    assert d["count"] == 0 and d["matches"] == []


def test_search_kind_filter_headings_only(tmp_path, capsys):
    db = tmp_path / "g.db"
    _index(db, tmp_path, capsys)
    # "notes" matches the heading text only (the para says "octopus"/"animal")
    rc, out, err = _run(
        ["--db", str(db), "search", "notes", "--kind", "heading", "--json"], capsys
    )
    assert rc == 0, err
    d = json.loads(out)
    assert d["count"] >= 1
    assert all(m["kind"] == "heading" for m in d["matches"])
    # without the filter, the same pattern still matches at least the heading
    rc2, out2, _ = _run(["--db", str(db), "search", "notes", "--json"], capsys)
    assert json.loads(out2)["count"] == d["count"]


def test_search_limit_caps_results(tmp_path, capsys):
    db = tmp_path / "g.db"
    _index(db, tmp_path, capsys)
    rc, out, err = _run(["--db", str(db), "search", "the", "--limit", "1", "--json"], capsys)
    assert rc == 0, err
    d = json.loads(out)
    assert d["limit"] == 1
    assert d["count"] <= 1


def test_search_default_text_output_not_json(tmp_path, capsys):
    db = tmp_path / "g.db"
    _index(db, tmp_path, capsys)
    rc, out, err = _run(["--db", str(db), "search", "octopus"], capsys)
    assert rc == 0
    assert "block(s) match" in out
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)


def test_search_empty_match_text_output(tmp_path, capsys):
    db = tmp_path / "g.db"
    _index(db, tmp_path, capsys)
    rc, out, err = _run(["--db", str(db), "search", "zzznomatch"], capsys)
    assert rc == 0
    assert "no blocks matching" in out


def test_search_treats_like_wildcards_as_literal_text(tmp_path, capsys):
    """Callers type search terms, not SQL patterns.

    `%` and `_` are LIKE wildcards, so unescaped they made `search "%"` return
    every block in the vault, and `search "50%"`/`search "snake_case"` match text
    that doesn't contain them. Nothing in the interface hints that the argument
    is a pattern.
    """
    (tmp_path / "a.md").write_text(
        "# A\nCoverage is 50% here. We use snake_case names.\n", encoding="utf-8")
    (tmp_path / "b.md").write_text(
        "# B\nCompletely unrelated prose.\n", encoding="utf-8")
    db = tmp_path / "g.db"
    _run(["--db", str(db), "index", str(tmp_path)], capsys)

    def n(pattern):
        rc, out, _ = _run(["--db", str(db), "search", pattern, "--json"], capsys)
        assert rc == 0
        return json.loads(out)["count"]

    # only the block that literally contains them
    assert n("%") == 1
    assert n("_") == 1
    assert n("50%") == 1
    assert n("snake_case") == 1
    # and a term in neither block still finds nothing
    assert n("zzz") == 0
