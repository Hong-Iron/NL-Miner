"""Phase 3 — preprocess: frontmatter / normalisation / language detection."""
from __future__ import annotations

from pathlib import Path

from src.preprocess import detect_lang, split_frontmatter, strip_bom, preprocess
from src.context import process
from src.schema import BlockKind, FileMeta

PROJECT = Path(__file__).resolve().parent.parent
OCTOPUS = PROJECT / "octopus.md"


def test_bom_strip_and_crlf_normalisation():
    assert strip_bom("﻿hello") == "hello"
    fm = FileMeta(file_path=Path("/nonexistent"), file_name="x.md")
    tmp = Path("/tmp/_nl_pre_bom.md")
    tmp.write_text("﻿a\r\n\r\nb\r\n", encoding="utf-8")
    fm2 = fm.model_copy(update={"file_path": tmp})
    body, _ = preprocess(fm2)
    assert body.startswith("a\n") and "\r" not in body
    tmp.unlink()


def test_frontmatter_parsed_and_body_offset_consistent():
    src = "---\ntitle: Beta\nauthors: Red Iron\n---\n# Heading\n\nBody.\n"
    meta, body = split_frontmatter(src)
    assert meta["title"] == "Beta" and meta["authors"] == "Red Iron"
    assert body.startswith("# Heading\n")
    # headings found later have spans indexing INTO this body, not the raw src
    assert body.index("# Heading") == 0


def test_no_frontmatter_returns_full_text():
    src = "# Just a doc\n\nNo frontmatter here.\n"
    meta, body = split_frontmatter(src)
    assert meta == {}
    assert body == src


def test_creator_and_lang_enriched(tmp_path):
    p = tmp_path / "f.md"
    p.write_text("---\nauthors: Jane Doe\n---\nA plain English document about octopuses.\n", encoding="utf-8")
    fm = FileMeta(file_path=p, file_name="f.md")
    body, fm2 = preprocess(fm)
    assert fm2.creator == "Jane Doe"
    assert fm2.lang == "en"
    assert fm2.mtime is not None
    assert "octopuses" in body


def test_detect_lang_scripts():
    assert detect_lang("고양이가 잠을 잔다") == "ko"
    assert detect_lang("猫は寝ている") == "ja"   # kana present → ja even with Han
    assert detect_lang("这是一个中文句子。") == "zh"
    assert detect_lang("The quick brown fox.") == "en"


def test_octopus_preprocess_no_frontmatter():
    fm = FileMeta(file_path=OCTOPUS, file_name="octopus.md")
    body, fm2 = preprocess(fm)
    # octopus.md has no frontmatter → body is the doc, lang=zh/en determined by content
    assert "Octopuses" in body
    assert fm2.lang in {"en", "zh"}
    assert fm2.creator is None


def test_non_utf8_falls_back_not_crash(tmp_path):
    p = tmp_path / "bad.md"
    p.write_bytes(b"# Hcaf\xe9 \xff\xfe\nsome body\n")
    fm = FileMeta(file_path=p, file_name="bad.md")
    body, _ = preprocess(fm)          # must NOT raise
    assert "body" in body
    assert "�" in body            # bad byte → replacement char


def test_full_pipeline_on_non_utf8(tmp_path):
    """process() end-to-end must survive a file with an invalid byte."""
    p = tmp_path / "bad2.md"
    p.write_bytes(b"# Title\n\nA paragraph with a bad \xe9 byte.\n")
    fm = FileMeta(file_path=p, file_name="bad2.md")
    ctx, stats = process(fm)
    assert any(b.kind is BlockKind.HEADING for b in ctx.blocks)
    assert stats["n_blocks"] >= 1
