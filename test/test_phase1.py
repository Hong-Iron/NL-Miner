"""Phase 1 — blocker + linker + process + payload integration tests.

Completion gates (phase1_blocks_links.md §4, ROADMAP 완료 기준):
  * same paragraph twice → one canonical block node + saved > 0
  * query-ish neighbour edges exist (contains / ref-to / sim)
  * CJK doesn't crash (fallback)
  * external deps still just pydantic
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src import linker
from src.context import process
from src.filestructure import discover
from src.markdown_blocker import blockify
from src.schema import BlockKind, FileMeta

PROJECT = Path(__file__).resolve().parent.parent
OCTOPUS = PROJECT / "octopus.md"


# -------------------------------------------------------------- fixtures


DUP_DOC = """# Notes

The quick brown fox jumps over the lazy dog. The dog slept quietly under the warm summer sun.

Markdown is a lightweight markup language for creating formatted text using plain text syntax.

The quick brown fox jumps over the lazy dog. The dog slept quietly under the warm summer sun.
"""


WIKI_DOC = """# Guide

See [[Target]] for details.

# Target

This is the target section.
"""


CODE_DOC = """# Code

```python
def helper():
    return 1
```

```python
helper()
```
"""


CJK_DOC = """# 한국어

고양이가 잠을 잔다. 강아지가 뛰어간다.
"""


def _find(blocks, kind, text_contains=None):
    for b in blocks:
        if b.kind is kind and (text_contains is None or text_contains in b.text):
            return b
    return None


# ----------------------------------------------------------------- blocker


def test_blockify_octopus_structure():
    text = OCTOPUS.read_text(encoding="utf-8")
    fm = FileMeta(file_path=OCTOPUS, file_name="octopus.md")
    blocks = blockify(text, fm.unique_id, md=True)

    headings = [b for b in blocks if b.kind is BlockKind.HEADING]
    code = [b for b in blocks if b.kind is BlockKind.CODE]
    assert len(headings) >= 4
    assert len(code) == 1

    # spans all within the original text and half-open
    for b in blocks:
        assert 0 <= b.span[0] <= b.span[1] <= len(text)

    # heading stack: "Intelligence" (###) sits under "Key Characteristics" (##)
    intel = _find(headings, BlockKind.HEADING, "Intelligence")
    key = _find(headings, BlockKind.HEADING, "Key Characteristics")
    assert intel.parent_block_id == key.block_id

    # the fenced snippet's parent is the "A Small Code Snippet" heading (##)
    snippet_h = _find(headings, BlockKind.HEADING, "Small Code Snippet")
    assert code[0].parent_block_id == snippet_h.block_id
    # the # inside the code block was NOT mistaken for a heading
    assert not any("print_octopus" in h.text for h in headings)


def test_txt_sentence_splitting():
    text = "Hello world. This is a test!\n\nSecond paragraph here? Yes."
    fm = FileMeta(file_path=Path("a.txt"), file_name="a.txt")
    blocks = blockify(text, fm.unique_id, md=False)
    assert all(b.kind is BlockKind.SENTENCE for b in blocks)
    assert len(blocks) >= 3
    assert blocks[0].text == "Hello world."
    for b in blocks:
        assert b.parent_block_id is None


# ----------------------------------------------------------------- linker


def test_near_dup_unifies_and_saves():
    fm = FileMeta(file_path=Path("d.md"), file_name="d.md")
    blocks = blockify(DUP_DOC, fm.unique_id, md=True)
    nodes, edges, stats = linker.link(blocks, fm.unique_id)

    canon = [n for n in nodes if n.kind == "canonical"]
    assert len(canon) == 1, "two identical paragraphs must collapse to one canonical node"

    # rep edges: both duplicate members point at the canonical node
    rep_edges = [e for e in edges if e.label == "rep"]
    assert len(rep_edges) == 2

    # blocks = [HEADING "Notes", PARA P1, PARA P2, PARA P1] — the dups are the PARAs
    paras = [b for b in blocks if b.kind is BlockKind.PARA]
    assert len(paras) == 3
    assert paras[0].text == paras[2].text
    n_dup = len(linker.tokenize(paras[0].text))
    raw = sum(len(linker.tokenize(b.text)) for b in blocks)
    assert stats["dup_groups"] == 1
    assert stats["saved_tokens"] == n_dup
    assert stats["raw_total_tokens"] == raw
    assert stats["ratio"] == pytest.approx(stats["saved_tokens"] / stats["raw_total_tokens"])
    assert 0.3 < stats["ratio"] < 0.5


def test_wikilink_ref_to():
    fm = FileMeta(file_path=Path("w.md"), file_name="w.md")
    blocks = blockify(WIKI_DOC, fm.unique_id, md=True)
    _, edges, _ = linker.link(blocks, fm.unique_id)
    ref = [e for e in edges if e.label == "ref-to"]
    assert len(ref) == 1
    src_block = next(b for b in blocks if str(b.block_id) == ref[0].src)
    assert "[[Target]]" in src_block.text
    tgt_block = next(b for b in blocks if str(b.block_id) == ref[0].dst)
    assert tgt_block.text == "Target"


def test_code_ref_between_code_blocks():
    fm = FileMeta(file_path=Path("c.md"), file_name="c.md")
    blocks = blockify(CODE_DOC, fm.unique_id, md=True)
    _, edges, _ = linker.link(blocks, fm.unique_id)
    code_refs = [e for e in edges if e.label == "code-ref"]
    assert len(code_refs) >= 1
    assert code_refs[0].src != code_refs[0].dst


def test_sim_vocab_edge():
    doc = (
        "# Top\n\n"
        "the system parses natural language text into linked blocks.\n\n"
        "the system parses natural language text into linked blocks again today.\n"
    )
    fm = FileMeta(file_path=Path("s.md"), file_name="s.md")
    blocks = blockify(doc, fm.unique_id, md=True)
    _, edges, _ = linker.link(blocks, fm.unique_id)
    sim = [e for e in edges if e.label == "sim-vocab"]
    assert len(sim) >= 1


def test_duplicate_edges_are_deduped():
    """Regression: a code block referencing two names defined in the SAME
    target block would emit two identical code-ref edges; the store's edge
    PK then IntegrityErrors. Linker must dedup (src,dst,label)."""
    doc = (
        "# C\n\n"
        "```python\n"
        "def helper():\n"
        "    return 1\n"
        "```\n\n"
        "```python\n"
        "a = helper()\n"
        "b = helper()\n"
        "```\n"  # helper referenced twice from the same block → same edge twice
    )
    fm = FileMeta(file_path=Path("d.md"), file_name="d.md")
    blocks = blockify(doc, fm.unique_id, md=True)
    _, edges, _ = linker.link(blocks, fm.unique_id)
    keys = [(e.src, e.dst, e.label) for e in edges]
    assert len(keys) == len(set(keys)), "no duplicate (src,dst,label) edges"


def test_duplicate_wikilinks_deduped():
    doc = "# Target\n\nbody.\n\n[[Target]] and again [[Target]]\n"
    fm = FileMeta(file_path=Path("w.md"), file_name="w.md")
    blocks = blockify(doc, fm.unique_id, md=True)
    _, edges, _ = linker.link(blocks, fm.unique_id)
    ref = [e for e in edges if e.label == "ref-to"]
    assert len(ref) == 1, "two wikilinks to same heading → one ref-to edge"


def test_cjk_does_not_crash_and_char_splits():
    fm = FileMeta(file_path=Path("ko.md"), file_name="ko.md")
    blocks = blockify(CJK_DOC, fm.unique_id, md=True)
    nodes, edges, stats = linker.link(blocks, fm.unique_id)

    # a single hangul word must NOT be one lump token → chars
    assert linker.tokenize("고양이가") == ["고", "양", "이", "가"]

    ko = _find(blocks, BlockKind.HEADING, "한국어")
    assert ko is not None
    assert stats["n_blocks"] >= 2
    assert stats["raw_total_tokens"] >= 1


# ----------------------------------------------------------------- process


def test_process_octopus_produces_graph():
    fm = FileMeta(file_path=OCTOPUS, file_name="octopus.md")
    ctx, stats = process(fm)
    assert len(ctx.blocks) >= 5
    assert stats["n_nodes"] == len(ctx.nodes)
    assert stats["n_edges"] == len(ctx.edges)
    assert "contains" in {e.label for e in ctx.edges}
    assert stats["raw_total_tokens"] > 0


def test_payload_jsonl_roundtrip(tmp_path):
    import sys

    sys.path.insert(0, str(PROJECT))  # ensure `import payload` works
    import payload as payload_mod

    import importlib

    payload_mod = importlib.import_module("payload")

    fm = FileMeta(file_path=OCTOPUS, file_name="octopus.md")
    ctx, _ = process(fm)
    path = payload_mod.save(ctx, tmp_path)
    assert path.suffix == ".jsonl"
    loaded = payload_mod.load(path)
    assert loaded == ctx  # full Context round-trips through one JSONL line


def test_discover_finds_octopus():
    found = discover(PROJECT)
    names = {f.file_name for f in found}
    assert "octopus.md" in names
