"""The closed loop: suggest → the agent writes → reindex → verify.

This is what "the agent controls the vault" has to mean, and it is testable end
to end. The failure it guards against was found by walking the loop by hand: the
tool named the note to link ("bluegreen.md"), and the obvious wikilink for it is
its *title* — `[[블루그린]]`. Obsidian resolves `[[X]]` against the **file name**,
so that link connects nothing and silently creates a phantom note. Worse than
doing nothing, and nothing in the output warned about it.

So the suggestion carries the literal string to write. No inference, no failure.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from interface.cli import main
from src.context import process
from src.schema import FileMeta
from src.store import GraphStore

VAULT = {
    "canary.md": "# 카나리 배포\n트래픽 일부만 새 버전으로 보낸다. 카나리로 위험을 줄인다.\n",
    "rollout.md": "# 롤아웃\n점진적 롤아웃 전략. 카나리와 블루그린이 있다.\n",
    "bluegreen.md": "# 블루그린\n두 환경을 번갈아 쓴다. 롤아웃 시 무중단.\n",
    "shop.md": "# 쇼핑\n우유와 계란.\n",
}


def _run(argv, capsys):
    rc = main(argv)
    out = capsys.readouterr()
    return rc, out.out, out.err


@pytest.fixture
def vault(tmp_path):
    for name, text in VAULT.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    return tmp_path


def _index(root, db, capsys):
    rc, *_ = _run(["--db", str(db), "index", str(root), "--pos"], capsys)
    assert rc == 0


def test_suggestion_carries_the_exact_link_to_write(vault, capsys):
    db = vault / "g.db"
    _index(vault, db, capsys)
    rc, out, _ = _run(["--db", str(db), "bridge", "rollout.md", "--json",
                       "--min-score", "0", "--min-shared", "1"], capsys)
    d = json.loads(out)
    by = {b["name"]: b for b in d["bridges"]}
    assert by["bluegreen.md"]["link"] == "[[bluegreen]]", (
        "must be the file name — [[블루그린]] (the heading) resolves to nothing"
    )


@pytest.mark.parametrize("name,expected", [
    ("bluegreen.md", "[[bluegreen]]"),
    ("두족류 개요.md", "[[두족류 개요]]"),   # spaces are fine in a wikilink
    ("a.b.md", "[[a.b]]"),                  # only the last extension goes
])
def test_wikilink_for(name, expected):
    assert GraphStore.wikilink_for(name) == expected


def test_the_loop_closes(vault, capsys):
    """Apply what the tool suggested, reindex, and the suggestion is gone."""
    db = vault / "g.db"
    _index(vault, db, capsys)

    rc, out, _ = _run(["--db", str(db), "bridge", "rollout.md", "--json",
                       "--min-score", "0", "--min-shared", "1"], capsys)
    links = [b["link"] for b in json.loads(out)["bridges"]]
    assert links, "nothing to apply — fixture is wrong"

    # the agent edits the note with its own tools; the CLI never writes notes
    p = vault / "rollout.md"
    p.write_text(p.read_text() + f"\n관련: {' '.join(links)}\n", encoding="utf-8")

    rc, out, _ = _run(["--db", str(db), "index", str(vault), "--pos", "--json"], capsys)
    d = json.loads(out)
    assert [f["name"] for f in d["files"]] == ["rollout.md"], "incremental: only the edit"

    rc, out, _ = _run(["--db", str(db), "bridge", "rollout.md", "--json",
                       "--min-score", "0", "--min-shared", "1"], capsys)
    assert json.loads(out)["bridges"] == [], "already linked — must not re-suggest"


def test_orphans_drop_after_the_agent_links(vault, capsys):
    db = vault / "g.db"
    _index(vault, db, capsys)
    rc, out, _ = _run(["--db", str(db), "audit", "--json"], capsys)
    assert len(json.loads(out)["orphans"]) == 4

    rc, out, _ = _run(["--db", str(db), "bridge", "rollout.md", "--json",
                       "--min-score", "0", "--min-shared", "1"], capsys)
    links = " ".join(b["link"] for b in json.loads(out)["bridges"])
    p = vault / "rollout.md"
    p.write_text(p.read_text() + f"\n관련: {links}\n", encoding="utf-8")
    _run(["--db", str(db), "index", str(vault), "--pos"], capsys)

    rc, out, _ = _run(["--db", str(db), "audit", "--json"], capsys)
    orphans = {o["name"] for o in json.loads(out)["orphans"]}
    assert orphans == {"shop.md"}, (
        "linking three notes must leave only the unrelated one orphaned"
    )


def test_the_cli_never_writes_notes(vault, capsys):
    """Control belongs to the agent: this tool reports, the agent acts. If a
    subcommand ever edits a note, that is a new and much larger promise."""
    before = {p.name: p.read_text() for p in vault.glob("*.md")}
    db = vault / "g.db"
    _index(vault, db, capsys)
    for argv in (["--db", str(db), "audit"],
                 ["--db", str(db), "bridge", "rollout.md"],
                 ["--db", str(db), "cards"],
                 ["outline", str(vault / "rollout.md")]):
        _run(argv, capsys)
    after = {p.name: p.read_text() for p in vault.glob("*.md")}
    assert before == after
