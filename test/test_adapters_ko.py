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
    for w in ["높고", "뛰어나다", "가장"]:
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


# --------------------------------- copula, plural, agreement, markup leftovers


@pytest.mark.parametrize("word,stem,suffix", [
    ("생물이다", "생물", "이다"),
    ("파장이다", "파장", "이다"),
    ("노드이므로", "노드", "이므로"),
    ("조건이지만", "조건", "이지만"),
])
def test_copula_splits_off_the_noun(word, stem, suffix):
    """파장이다 left whole was a concept distinct from 파장 — in a vault written
    in the plain ~이다 style, half the noun's occurrences."""
    assert split_josa(word) == (stem, suffix)


def test_copula_noun_is_tagged_as_noun_not_verb():
    """노드이므로 ends in 므로, a verb-only connective; the copula must win."""
    pairs = [(t.text, t.pos) for t in KoreanRuleAdapter().tag("노드이므로")]
    assert ("노드", "NN") in pairs


@pytest.mark.parametrize("word,stem,suffix", [
    ("우체국들", "우체국", "들"),
    ("호스트들이", "호스트", "들이"),
    ("agent들은", "agent", "들은"),
])
def test_plural_deul_folds_onto_the_noun(word, stem, suffix):
    assert split_josa(word) == (stem, suffix)


@pytest.mark.parametrize("word", ["그들", "만들"])
def test_deul_is_not_cut_from_short_words(word):
    assert split_josa(word) == (word, None)


@pytest.mark.parametrize("word", [
    "디스플레이", "어레이", "게이트웨이",   # 이 after a vowel is not 이 the particle
    "역효과",                              # 과 after a vowel is not 과
    "재평가", "전문가",                     # 가 after a consonant is not 가
])
def test_particle_must_agree_with_the_final_sound(word):
    """이/은/을/과/으로 follow a 받침, 가/는/를/와 a vowel. A particle in the
    wrong shape is part of the noun."""
    assert split_josa(word) == (word, None)


def test_agreeing_particles_still_split_after_loanwords():
    assert split_josa("디스플레이가") == ("디스플레이", "가")
    assert split_josa("전문가는") == ("전문가", "는")


@pytest.mark.parametrize("word", ["푸리에", "해상도", "정확도", "허프만", "마이크로"])
def test_listed_nouns_are_never_split(word):
    """These pass the stem and agreement rules (푸리 + 에, 해상 + 도) but are
    single nouns."""
    assert split_josa(word) == (word, None)


def test_listed_noun_still_sheds_a_real_particle():
    assert split_josa("푸리에의") == ("푸리에", "의")


@pytest.mark.parametrize("raw,noun", [
    ("**속도**는", "속도"),
    ('"비트"는', "비트"),
    ("“비트”를", "비트"),
    ("「정의」에", "정의"),
])
def test_markup_inside_the_word_does_not_glue_onto_the_noun(raw, noun):
    """The adapter can only strip the ends of an 어절, so **속도**는 used to
    leave 속도** behind once the particle came off."""
    texts = [t.text for t in KoreanRuleAdapter().tag(raw)]
    assert noun in texts
    assert not any(ch in t for t in texts for ch in '*"“”「」')


def test_middle_dot_separates_words():
    texts = [t.text for t in KoreanRuleAdapter().tag("ACK·타임아웃·순서")]
    assert texts == ["ACK", "타임아웃", "순서"]


# ------------------------------------------------------------- verb forms


@pytest.mark.parametrize("word,root,ending", [
    ("처리하는", "처리", "하는"),
    ("약속하지", "약속", "하지"),
    ("분류한", "분류", "한"),
    ("필요한", "필요", "한"),
    ("전송되고", "전송", "되고"),
])
def test_hada_verbs_yield_their_noun(word, root, ending):
    """A Sino-Korean 하다/되다 verb carries its concept in the root."""
    pairs = [(t.text, t.pos) for t in KoreanRuleAdapter().tag(word)]
    assert pairs == [(root, "NN"), (ending, "XSV")]


@pytest.mark.parametrize("word", ["시분할", "무제한", "인수분해"])
def test_nouns_ending_like_hada_forms_stay_whole(word):
    pairs = [(t.text, t.pos) for t in KoreanRuleAdapter().tag(word)]
    assert pairs == [(word, "NN")]


@pytest.mark.parametrize("word", ["비슷하다", "똑똑하다", "깨끗한"])
def test_native_adjectives_give_no_noun(word):
    """비슷 and 똑똑 are not nouns: ㅅ 받침 and reduplication mark a native
    root. The word is still recognised as a verb form."""
    toks = KoreanRuleAdapter().tag(word)
    assert all(t.pos != "NN" for t in toks)


@pytest.mark.parametrize("word", [
    "보냈다", "이어지므로", "빠르게", "아닌지", "보내고", "알아보다",
    "받아들이고", "산다", "흐르며",
])
def test_verb_forms_are_tagged_vv(word):
    pairs = [(t.text, t.pos) for t in KoreanRuleAdapter().tag(word)]
    assert pairs == [(word, "VV")]


@pytest.mark.parametrize("word,noun", [
    ("생각보다", "생각"),       # 보다 the particle, not the verb
    ("바다에서", "바다"),       # a noun ending in 다
    ("냉장고가", "냉장고"),     # a noun ending in 고
    ("메시지를", "메시지"),     # a noun ending in 지
    ("파이프라인이", "파이프라인"),
])
def test_nouns_that_end_like_verbs_survive(word, noun):
    pairs = [(t.text, t.pos) for t in KoreanRuleAdapter().tag(word)]
    assert (noun, "NN") in pairs


@pytest.mark.parametrize("word", [
    "보냈다", "빠르게", "아닌지", "하는", "되어", '두는가"다',
    "나은지는",      # a verb only once its particle is off — stored whole as VV
    "느려지거나", "가리듯", "일부냐",
])
def test_verb_forms_are_not_concepts(word):
    """The store decides on the surface form too, so a store indexed before
    the adapter knew these is filtered the same way."""
    from src.store import GraphStore
    assert GraphStore._is_content_word(word) is False


@pytest.mark.parametrize("word", ["바다", "람다", "냉장고", "메시지", "시분할", "체인지"])
def test_nouns_shaped_like_verbs_are_concepts(word):
    from src.store import GraphStore
    assert GraphStore._is_content_word(word) is True


def test_quoted_word_sheds_both_particles():
    """\"방송부원들은\"으로 — the particle inside the quotes belongs to the word
    as much as the one outside."""
    texts = [t.text for t in KoreanRuleAdapter().tag('"방송부원들은"으로')]
    assert texts[0] == "방송부원"
