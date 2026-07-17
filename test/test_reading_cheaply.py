"""Reading a document without loading it.

The gap these close: every earlier way to "read" a document through this tool
cost *more* than the file. On a 20.5k-token note, `query <doc> --json` is 34.8k
and `view --json` is 56.9k — both carry every block's text. And `search` needs a
word you only have once you've read the thing.

`outline` is the shape without the body; `cards` is that for a whole vault.
"""
import json

import pytest

from interface.cli import main
from src.context import process
from src.schema import FileMeta
from src.store import GraphStore

DOC = """# Title

Opening prose here.

## Section A

Body of A.

```python
code()
```

## Section B

Body of B.

> a quote
"""


def _run(argv, capsys):
    rc = main(argv)
    out = capsys.readouterr()
    return rc, out.out, out.err


@pytest.fixture
def doc(tmp_path):
    p = tmp_path / "note.md"
    p.write_text(DOC, encoding="utf-8")
    return p


# ------------------------------------------------------------- outline


def test_outline_is_far_smaller_than_a_real_document(tmp_path, capsys):
    """The whole point: the map has to be cheaper than the territory."""
    big = tmp_path / "big.md"
    big.write_text(
        "".join(f"## Section {i}\n\n" + ("Body sentence here. " * 60) + "\n\n"
                for i in range(20)),
        encoding="utf-8",
    )
    rc, out, _ = _run(["outline", str(big), "--json"], capsys)
    assert rc == 0
    ratio = len(out) / len(big.read_text())
    assert ratio < 0.15, f"outline should be a fraction of the file, got {ratio:.0%}"


def test_outline_costs_more_than_a_tiny_file(doc, capsys):
    """The honest limit, pinned so nobody 'optimises' the wrong direction.

    Structure has a fixed cost — ids, line numbers, kind counts — so under a few
    KB the map exceeds the territory. Callers must check the size first and just
    read small notes. Measured on a real vault: worth it at 20k tokens (18x),
    a loss at 400.
    """
    rc, out, _ = _run(["outline", str(doc), "--json"], capsys)
    assert len(out) > len(DOC), (
        "if this ever passes, outline got cheap enough that the size check in "
        "SKILL.md can be dropped"
    )


def test_outline_carries_no_body_text(doc, capsys):
    rc, out, _ = _run(["outline", str(doc), "--json"], capsys)
    d = json.loads(out)
    assert "Body of A." not in out
    assert "code()" not in out
    assert [s["title"] for s in d["sections"]] == ["Title", "Section A", "Section B"]


def test_outline_gives_line_ranges_not_char_spans(doc, capsys):
    """The caller's next move is Read(file, offset, limit), which speaks lines.
    Handing over char offsets would make every caller redo the conversion."""
    rc, out, _ = _run(["outline", str(doc), "--json"], capsys)
    d = json.loads(out)
    lines = DOC.splitlines()
    for s in d["sections"]:
        assert 1 <= s["from_line"] <= s["to_line"] <= len(lines) + 1
    a = next(s for s in d["sections"] if s["title"] == "Section A")
    # the range must actually contain the section
    assert "Section A" in "\n".join(lines[a["from_line"] - 1 : a["to_line"]])


def test_outline_reports_what_each_section_is_made_of(doc, capsys):
    rc, out, _ = _run(["outline", str(doc), "--json"], capsys)
    d = json.loads(out)
    a = next(s for s in d["sections"] if s["title"] == "Section A")
    b = next(s for s in d["sections"] if s["title"] == "Section B")
    assert a["kinds"].get("code") == 1, "the code block is why you'd pick this section"
    assert b["kinds"].get("quote") == 1


def test_outline_needs_no_store(doc, capsys):
    """'Let me look at this file cheaply' must not require indexing a database."""
    rc, out, _ = _run(["outline", str(doc), "--json"], capsys)
    assert rc == 0 and json.loads(out)["n_sections"] == 3


def test_outline_keeps_prose_before_the_first_heading(tmp_path, capsys):
    p = tmp_path / "n.md"
    p.write_text("Just prose, no heading at all.\n", encoding="utf-8")
    rc, out, _ = _run(["outline", str(p), "--json"], capsys)
    d = json.loads(out)
    assert d["n_sections"] == 1 and d["sections"][0]["title"] == ""


# --------------------------------------------------------------- cards


@pytest.fixture
def vault(tmp_path):
    (tmp_path / "octopus.md").write_text(
        "# Octopus\nThe octopus uses camouflage in the ocean.\n", encoding="utf-8")
    (tmp_path / "pasta.md").write_text(
        "# Pasta\nBoil water and add salt to the pasta.\n", encoding="utf-8")
    (tmp_path / "squid.md").write_text(
        "# Squid\nThe squid is a cephalopod in the ocean.\n", encoding="utf-8")
    store = GraphStore(tmp_path / "g.db")
    for n in ("octopus.md", "pasta.md", "squid.md"):
        p = tmp_path / n
        ctx, _ = process(FileMeta(file_path=p, file_name=n), pos=True)
        store.upsert_file(ctx)
    yield store
    store.close()


def test_cards_carry_headings_and_keywords_but_no_body(vault):
    cards = {c["name"]: c for c in vault.doc_cards()}
    assert set(cards) == {"octopus.md", "pasta.md", "squid.md"}
    o = cards["octopus.md"]
    assert [h["title"] for h in o["headings"]] == ["Octopus"]
    assert o["n_chars"] > 0
    assert "camouflage" in [k.lower() for k in o["keywords"]]


def test_keywords_are_distinctive_not_frequent(vault):
    """Ranking by frequency would give every card the same words. The keyword
    has to be what this note has and the others don't."""
    cards = {c["name"]: c for c in vault.doc_cards()}
    pasta = [k.lower() for k in cards["pasta.md"]["keywords"]]
    assert "salt" in pasta or "pasta" in pasta
    # "ocean" is in two of three notes, so it must not out-rank a unique term
    octo = [k.lower() for k in cards["octopus.md"]["keywords"]]
    assert octo.index("camouflage") < (octo.index("ocean") if "ocean" in octo else 99)


def test_cards_have_no_summary_field(vault):
    """A summary needs an LLM this project doesn't have. Better to omit it than
    to ship something that looks like comprehension and isn't."""
    for c in vault.doc_cards():
        assert "summary" not in c
