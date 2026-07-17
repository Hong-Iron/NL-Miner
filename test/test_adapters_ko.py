"""Korean 조사 splitting — the thing cross-note concept linking depends on.

Without it a whitespace tokenizer emits 문어는/문어가/문어를 as three unrelated
nodes, so the canonical dedup (the mechanism behind linking a concept across
notes) can never fire on a Korean vault.

The splitter is deliberately conservative: a false split silently merges
unrelated concepts onto one node and corrupts the graph, whereas a missed split
only reproduces today's behaviour. These tests pin both directions — what it
must catch, and what it must refuse to touch.
"""
from uuid import uuid4

import pytest

from src import linker
from src.adapters_ko import KoreanRuleAdapter, split_josa
from src.markdown_blocker import blockify
from src.postagger import AutoAdapter, RulePOSTagger, get_adapter
from src.schema import canonical_id


# --------------------------------------------------------------- the unlock


def test_same_noun_different_particles_collapse_to_one_node():
    forms = ["문어는", "문어가", "문어를", "문어의", "문어에게", "문어"]
    ids = {canonical_id(split_josa(f)[0], "NN") for f in forms}
    assert len(ids) == 1, f"expected one canonical node, got {len(ids)}"


def test_particles_are_split_off():
    assert split_josa("문어는") == ("문어", "는")
    assert split_josa("바다에서") == ("바다", "에서")
    assert split_josa("아가미로") == ("아가미", "로")
    assert split_josa("헤모시아닌이라는") == ("헤모시아닌", "이라는")


def test_longest_particle_wins():
    # 에서 must beat 에, otherwise the stem keeps a stray 서
    assert split_josa("바다에서")[0] == "바다"
    assert split_josa("으로서를"[:3])[0] != ""


# ------------------------------------------------- what it must NOT touch


def test_noun_ending_in_a_particle_syllable_is_left_alone():
    """사과 ends in 과 (a real particle). Splitting it would merge an apple
    with 사-, so the min-stem rule must keep it whole."""
    assert split_josa("사과") == ("사과", None)


def test_verb_endings_are_not_particles():
    for w in ["높고", "뛰어나다", "생물이다", "가장"]:
        stem, josa = split_josa(w)
        assert josa is None, f"{w} is not a noun+particle; got {stem}+{josa}"


def test_stem_is_never_emptied():
    for w in ["는", "이", "에서", "의"]:
        stem, josa = split_josa(w)
        assert stem == w and josa is None, "a bare particle has no stem to keep"


# ------------------------------------------------------------- the adapter


def test_adapter_tags_stem_as_noun_and_particle_separately():
    toks = KoreanRuleAdapter().tag("문어는 바다에서 산다")
    pairs = [(t.text, t.pos) for t in toks]
    assert ("문어", "NN") in pairs
    assert ("는", "JO") in pairs
    assert ("바다", "NN") in pairs


def test_adapter_delegates_latin_words_to_the_en_tagger():
    toks = KoreanRuleAdapter().tag("문어는 octopus 이다")
    pairs = {(t.text, t.pos) for t in toks}
    assert ("문어", "NN") in pairs
    # the latin word keeps whatever the EN rules say, not a blanket NN
    assert any(t.text == "octopus" for t in toks)


def test_trailing_punctuation_does_not_block_the_split():
    toks = KoreanRuleAdapter().tag("문어는.")
    assert ("문어", "NN") in [(t.text, t.pos) for t in toks]


# --------------------------------------------------------------- dispatch


def test_auto_adapter_routes_korean_and_leaves_english_identical():
    auto = AutoAdapter()
    en = "The dog walked to the park."
    assert auto.tag(en) == RulePOSTagger().tag(en), "EN must be byte-identical"
    ko = [t.text for t in auto.tag("문어는 바다에서 산다")]
    assert "문어" in ko and "는" in ko


def test_default_adapter_handles_korean_without_env_config():
    """A Korean vault must work on a default install — no NLM_MORPH needed."""
    texts = [t.text for t in get_adapter(env="").tag("문어는 바다에서 산다")]
    assert "문어" in texts


def test_korean_note_dedups_across_blocks():
    """End-to-end: the same noun in different roles across blocks collapses."""
    text = "# 문어\n\n문어는 똑똑하다.\n\n문어가 헤엄친다.\n\n문어를 보았다.\n"
    uid = uuid4()
    blocks = blockify(text, uid, md=True)
    nodes, _, stats = linker.link(blocks, uid, pos=True)
    munueo = [n for n in nodes if n.kind == "token" and n.label == "문어"]
    assert len(munueo) == 1, "문어 must be a single shared canonical node"
    assert stats["token_saved"] > 0


# ------------------------------- found on a real Korean-English vault


@pytest.mark.parametrize("word,stem,josa", [
    ("agent가", "agent", "가"),
    ("args의", "args", "의"),
    ("CSV로", "CSV", "로"),
    ("API를", "API", "를"),
    ("JSON은", "JSON", "은"),
    ("k-fold가", "k-fold", "가"),
])
def test_josa_splits_off_latin_words(word, stem, josa):
    """Korean technical writing attaches 조사 straight onto English words.
    Requiring a Hangul stem meant none of these split, so agent / agent가 /
    agent를 stayed three unrelated concepts — on a real vault this was the
    single biggest source of duplicate concepts."""
    assert split_josa(word) == (stem, josa)


def test_latin_stem_keeps_the_pos_it_would_have_alone():
    """"JSON은" → "JSON" must land on the same canonical node as a bare "JSON",
    or the concept still fails to link. That means the EN tagger has to tag it —
    (JSON, NNP) — rather than the adapter's blanket NN."""
    with_josa = {(t.text, t.pos) for t in KoreanRuleAdapter().tag("JSON은 좋다")}
    alone = {(t.text, t.pos) for t in KoreanRuleAdapter().tag("JSON 형식")}
    assert ("JSON", "NNP") in with_josa
    assert ("JSON", "NNP") in alone


@pytest.mark.parametrize("word", [
    "유도하거나", "하거나", "되거나", "가거나",   # -거나
    "포함하여", "수행하여",                      # -여
    "하여야",                                   # -야
])
def test_verb_endings_are_not_stripped_as_particles(word):
    """나/야/여 are particles in principle but overwhelmingly verb-ending tails
    in prose. Stripping them invented words ("유도하거", "포함하") that then
    showed up in the UI as concepts."""
    assert split_josa(word) == (word, None)


@pytest.mark.parametrize("word", ["여기", "위해", "함께", "것이", "자세히", "그리고"])
def test_korean_closed_class_words_are_not_concepts(word):
    """English function words were dropped via postagger._LEXICON while Korean
    ones sailed through — an asymmetry that let 여기/위해/함께 link notes that
    share nothing."""
    from src.store import GraphStore
    assert GraphStore._is_content_word(word) is False


@pytest.mark.parametrize("word", ["문어", "지능", "파이프라인", "두족류"])
def test_real_korean_nouns_survive(word):
    from src.store import GraphStore
    assert GraphStore._is_content_word(word) is True
