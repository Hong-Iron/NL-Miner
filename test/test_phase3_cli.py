"""Phase 3 — CLI surface: index → query → graph → purge, via the public main()."""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest

import interface.cli as cli
from src.schema import BlockKind
from pathlib import Path as _Path

PROJECT = Path(__file__).resolve().parent.parent
OCTOPUS = PROJECT / "octopus.md"


def _run(args, db):
    return cli.main(["--db", str(db)] + args)


def test_index_then_query_and_graph(tmp_path, capsys):
    db = tmp_path / "g.db"
    rc = _run(["index", str(OCTOPUS)], db)
    assert rc == 0
    out = capsys.readouterr().out
    # summary line leads with the dedup that actually fires (token collapse);
    # the dup-block ratio it used to lead with reads 0.0% on any real corpus.
    assert "indexed octopus.md" in out and "deduped=" in out
    assert (tmp_path.parent / "payload").exists() or Path("payload").exists()

    # payload file written
    payload_path = Path("payload") / "octopus.md.jsonl"
    assert payload_path.exists()
    payload_path.unlink()

    # query: no block → lists blocks
    rc = _run(["query", "octopus.md"], db)
    assert rc == 0
    out = capsys.readouterr().out
    assert "octopus.md:" in out and "heading" in out

    # query a specific block id → neighbours; look the stored id up from the DB
    # (block ids are random uuid4 per process(), so we read what was indexed)
    from src.context import process
    from src.schema import FileMeta
    from src.store import GraphStore
    with GraphStore(db) as s:
        fu = s.file_uuid_for("octopus.md")
        row = s.conn.execute(
            "SELECT block_id FROM blocks WHERE file_uuid=? AND kind='heading' "
            "AND text LIKE '%Intelligence%'", (fu,)
        ).fetchone()
    intel_id = row[0]
    rc = _run(["query", "octopus.md", "--block", intel_id], db)
    assert rc == 0
    out = capsys.readouterr().out
    assert "neighbours" in out and "contains" in out

    # graph summary + shared concepts. The text view now leads with concepts
    # (shared *token* nodes) rather than "top canonical" dup-block groups,
    # which only exist for byte-identical blocks and so are always empty here.
    rc = _run(["graph", "--top-canonical", "5"], db)
    assert rc == 0
    out = capsys.readouterr().out
    assert "store" in out and "shared concepts" in out

    # purge
    rc = _run(["purge", "octopus.md"], db)
    assert rc == 0
    out = capsys.readouterr().out
    assert "purged 1" in out

    # now query should fail to find it
    rc = _run(["query", "octopus.md"], db)
    assert rc == 1


def test_index_directory_many(tmp_path, capsys):
    db = tmp_path / "many.db"
    a = tmp_path / "a.md"; a.write_text("# A\n\nunique alpha text here.\n", encoding="utf-8")
    b = tmp_path / "b.md"; b.write_text("# B\n\nunique beta text there.\n", encoding="utf-8")
    rc = _run(["index", str(tmp_path)], db)
    assert rc == 0
    out = capsys.readouterr().out
    assert "2 file(s)" in out
    # payloads cleaned
    for p in ("a.md", "b.md"):
        pth = Path("payload") / f"{p}.jsonl"
        assert pth.exists(); pth.unlink()
