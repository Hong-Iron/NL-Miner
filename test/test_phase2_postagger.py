"""Phase 2 — rule-based POS tagger tests (no external deps).

Completion gates (phase2_parsers.md §2.4–§2.5):
  * closed-class lexicon (deterministic) overrides morphology
  * suffix morphology fills the open class (-ing→VBG, -ly→RB, -tion→NN ...)
  * capitalised unknown → NNP, digits → CD
  * canonical id is stable for a fixed (text, pos)
  * NLM_MORPH env selects an external adapter; bad/absent → rule fallback
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.postagger import (
    RulePOSTagger,
    Token,
    get_adapter,
    tag,
)

PROJECT = Path(__file__).resolve().parent.parent
OCTOPUS = PROJECT / "octopus.md"


# ----------------------------------------------------------------- lexicon


def test_closedclass_lexicon_overrides_morphology():
    t = RulePOSTagger()
    # "be" is in the lexicon as VB even though bare verb; "is" → VBZ
    assert t._pos("the") == "DT"
    assert t._pos("The") == "DT"          # case-insensitive lexicon hit
    assert t._pos("is") == "VBZ"
    assert t._pos("and") == "CC"
    assert t._pos("in") == "IN"
    assert t._pos("not") == "RB"


def test_aux_modal_inflections_deterministic():
    t = RulePOSTagger()
    assert t._pos("was") == "VBD"
    assert t._pos("were") == "VBD"
    assert t._pos("has") == "VBZ"
    assert t._pos("will") == "MD"
    assert t._pos("could") == "MD"


# ---------------------------------------------------------------- morphology


def test_suffix_morphology():
    t = RulePOSTagger()
    assert t._pos("running") == "VBG"
    assert t._pos("walked") == "VBD"
    assert t._pos("quickly") == "RB"
    assert t._pos("creation") == "NN"
    assert t._pos("happiness") == "NN"
    assert t._pos("useful") == "JJ"
    assert t._pos("hopeless") == "JJ"


def test_capitalised_unknown_is_proper():
    t = RulePOSTagger()
    assert t._pos("Octopus") == "NNP"
    assert t._pos("Berlin") == "NNP"
    # but capitalised lexicon words still take their lexicon tag
    assert t._pos("The") == "DT"


def test_numbers_are_cd():
    t = RulePOSTagger()
    assert t._pos("3") == "CD"
    assert t._pos("3.14") == "CD"
    assert t._pos("12:00") == "CD"
    assert t._pos("-7") == "CD"
    assert t._pos("HTTP2") == "CD"   # has digits, not pure alpha


def test_plural_s_heuristic():
    t = RulePOSTagger()
    assert t._pos("dogs") == "NNS"
    assert t._pos("octopuses") == "NNS"
    # -ss should NOT trigger plural
    assert t._pos("class") == "NN"
    assert t._pos("boss") == "NN"


def test_fallback_common_noun():
    t = RulePOSTagger()
    # alpha lowercase, no known suffix, no -s
    assert t._pos("cat") == "NN"
    assert t._pos("dog") == "NN"


# ------------------------------------------------------------------- tagging


def test_tag_returns_tokens_with_pos():
    toks = tag("The quick fox runs.")
    texts = [t.text for t in toks]
    assert "The" in texts and "fox" in texts
    assert all(isinstance(t, Token) for t in toks)
    assert all(t.pos != "NA" for t in toks), "rule tagger never emits NA"


def test_tag_octopus_has_noun_and_verb_distribution():
    text = OCTOPUS.read_text(encoding="utf-8")
    toks = tag(text)
    assert len(toks) > 50
    pos_set = {t.pos for t in toks}
    # an EN document should produce at least nouns, a proper noun, and some verb
    assert "NN" in pos_set or "NNS" in pos_set
    assert "NNP" in pos_set            # "Octopus", "Mimic Octopus" etc.
    assert pos_set & {"VBZ", "VBD", "VBP", "VB", "VBG"}


# ----------------------------------------------------------------- canonical


def test_canonical_id_stable_for_fixed_text_pos():
    a = tag("running")[0]
    b = tag("running")[0]
    assert a.pos == b.pos == "VBG"
    assert a.canonical == b.canonical
    # different POS → different canonical id (the whole point of Phase 2)
    from src.schema import canonical_id
    assert canonical_id("running", "VBG") != canonical_id("running", "NN")


@pytest.mark.parametrize("word,pos,lemma", [
    ("walked", "VBD", "walk"),
    ("running", "VBG", "runn"),   # naive -ing strip; acceptable for a no-LM tagger
    ("dogs", "NNS", "dog"),
])
def test_lemma_strips_inflection(word, pos, lemma):
    assert RulePOSTagger.lemma(word, pos) == lemma


# ------------------------------------------------------------- adapter switch


def test_get_adapter_default_is_builtin_auto():
    # The default routes by script (AutoAdapter) instead of assuming English.
    # What matters is the invariant it upholds: no external analyzer, and latin
    # text tagged exactly as the rule tagger would.
    a = get_adapter(env="")
    assert a.name == "auto"
    assert a.tag("the dog barked") == RulePOSTagger().tag("the dog barked")


def test_get_adapter_env_can_still_pin_the_rule_tagger():
    assert get_adapter(env="rule").name == "rule"


def test_get_adapter_unknown_env_falls_back_to_builtin(monkeypatch):
    monkeypatch.setenv("NLM_MORPH", "nonexistent-analyzer")
    a = get_adapter()
    assert a.name == "auto"


def test_get_adapter_spacy_missing_falls_back(monkeypatch):
    # spaCy is not installed in the default env → must silently fall back to a
    # built-in tagger rather than raising
    monkeypatch.setenv("NLM_MORPH", "spacy")
    a = get_adapter()
    assert a.name == "auto"


def test_tag_accepts_explicit_adapter():
    toks = tag("the dog", adapter=RulePOSTagger())
    assert [t.pos for t in toks] == ["DT", "NN"]
