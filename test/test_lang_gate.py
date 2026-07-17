"""The EN-only parsers must not run on non-English text.

Regression for a silent-fabrication bug: the rule tagger falls back to ``NN``
for any word it doesn't recognise, so on Korean every token came back a noun,
``("N","N") -> compound`` fired between every adjacent pair, and a Korean note
produced a chain of confident-looking ``dep:compound`` edges — one of them even
attaching to a list bullet. The graph was noise presented as a parse.

The gate is per *sentence* (not per file) so mixed notes behave: an English
sentence inside a Korean note still parses, and vice versa.
"""
from uuid import uuid4

from src import linker
from src.markdown_blocker import blockify
from src.postagger import RulePOSTagger

KO = "문어는 바다에서 가장 흥미로운 생물이다. 문어는 지능이 높고 위장 능력이 뛰어나다.\n"
EN = "The octopus is an animal. The dog walked to the park.\n"
MIXED = "문어는 바다에서 가장 흥미로운 생물이다.\n\nThe dog walked to the park.\n"


def _link(text: str):
    uid = uuid4()
    blocks = blockify(text, uid, md=True)
    return linker.link(blocks, uid, parse=True, adapter=RulePOSTagger())


def test_korean_produces_no_fabricated_deps():
    nodes, edges, stats = _link(KO)
    deps = [e for e in edges if e.label.startswith("dep:")]
    assert deps == [], f"EN parser fabricated {len(deps)} relations on Korean"
    assert stats["n_dep_edges"] == 0
    assert stats["n_phrase_nodes"] == 0
    # the sentences are accounted for, not silently dropped
    assert stats["skipped_lang"] >= 2
    assert stats["parsed_sentences"] == 0


def test_korean_still_yields_blocks_and_tokens():
    """Gating the parser must not gut the layers that *do* work: block
    structure and canonical tokens are language-independent."""
    nodes, edges, stats = _link(KO)
    assert stats["n_blocks"] >= 1
    assert stats["token_raw"] > 0
    assert any(n.kind == "token" for n in nodes)
    assert any(n.kind == "block" for n in nodes)


def test_english_is_unaffected():
    nodes, edges, stats = _link(EN)
    assert stats["n_dep_edges"] > 0
    assert stats["n_phrase_nodes"] > 0
    assert stats["skipped_lang"] == 0
    assert stats["parsed_sentences"] >= 2


def test_gate_is_per_sentence_not_per_file():
    """An English sentence in a Korean note still parses; the Korean one
    doesn't. A file-level gate would drop both."""
    nodes, edges, stats = _link(MIXED)
    assert stats["skipped_lang"] >= 1, "Korean sentence should be skipped"
    assert stats["parsed_sentences"] >= 1, "English sentence should still parse"
    assert stats["n_dep_edges"] > 0
