"""The reported numbers must describe what actually happened.

Two ways the old stats misled:

* ``parsed_sentences`` counts a sentence where *either* parser produced
  something. Arc-eager practically never fails, so it read 15/15 ("0 skipped")
  on octopus.md while CKY had in fact found constituents in 1 sentence of 15.
  ``cky_trees``/``cky_coverage`` report the constituency layer on its own.
* ``saved_tokens`` counts byte-identical duplicate *blocks*. Real prose has
  none, so the headline "token saving" was structurally 0.000 on every real
  document, while the canonical-token collapse that does fire went unreported.
"""
from pathlib import Path
from uuid import uuid4

from src import linker
from src.markdown_blocker import blockify
from src.postagger import RulePOSTagger

OCTOPUS = Path(__file__).resolve().parents[1] / "octopus.md"


def _link(text: str, **kw):
    uid = uuid4()
    blocks = blockify(text, uid, md=True)
    return linker.link(blocks, uid, adapter=RulePOSTagger(), **kw)


def test_cky_coverage_is_reported_separately_from_parsed_sentences():
    """A sentence the dependency parser handles but CKY cannot must not be
    counted as a constituency success."""
    # "walked" + "to the park" parse; the octopus prose largely does not.
    text = OCTOPUS.read_text(encoding="utf-8")
    _, _, stats = _link(text, parse=True)
    attempted = stats["parsed_sentences"] + stats["skipped_sentences"]
    assert attempted > 0
    # the honest counter never claims more than CKY actually produced
    assert stats["cky_trees"] <= attempted
    assert stats["cky_coverage"] == (
        stats["cky_trees"] / attempted if attempted else 0.0
    )
    # the point of the metric: on real prose CKY covers a small fraction, and
    # the number must show that rather than inheriting the dep parser's success
    assert stats["cky_trees"] < stats["parsed_sentences"]


def test_phrase_nodes_and_cky_trees_agree_on_emptiness():
    """If CKY produced no tree, there can be no phrase nodes — the two honest
    signals must not contradict each other."""
    _, _, stats = _link("Zqx frobnicated the vorpal blint.\n", parse=True)
    if stats["cky_trees"] == 0:
        assert stats["n_phrase_nodes"] == 0


def test_token_dedup_is_the_metric_that_fires():
    """Repeated words collapse onto one canonical node; that saving is real and
    must be reported, unlike the dup-block metric which needs identical blocks."""
    text = "The dog saw the dog. The dog barked at the dog.\n"
    _, _, stats = _link(text, pos=True)
    assert stats["token_saved"] > 0, "repeated words must dedup"
    assert stats["token_saved"] == stats["token_raw"] - stats["token_unique"]
    # and the dup-block metric stays 0 here: no two blocks are identical
    assert stats["saved_tokens"] == 0


def test_dup_block_saving_is_zero_on_real_prose():
    """Documents the limitation that motivated the change: the old headline
    number cannot fire on a document whose blocks all differ."""
    text = OCTOPUS.read_text(encoding="utf-8")
    _, _, stats = _link(text, pos=True)
    assert stats["saved_tokens"] == 0
    assert stats["ratio"] == 0.0
    # while the real dedup does fire
    assert stats["token_saved"] > 0
