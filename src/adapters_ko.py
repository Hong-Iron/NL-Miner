"""Korean morpheme adapter — rule-based 조사 (particle) stripping, no deps.

Why this exists
---------------
The canonical-node dedup keys on ``canonical_id(text, pos)``, so two notes only
share a concept node when they spell the word identically. Korean marks
grammatical role with trailing particles, so a whitespace tokenizer emits
``문어는`` / ``문어가`` / ``문어를`` as three unrelated 어절 — the same noun, three
nodes, zero linking. Splitting the particle off collapses them onto one
``(문어, NN)`` node, which is what makes cross-note concept linking possible at
all for a Korean vault.

Scope, honestly
---------------
This handles **조사**, the copula **이다**, the plural **들**, and recognises the
commonest **verb/adjective forms** so they stop posing as nouns. It is still not
a morphological analyzer — real 어미 analysis needs KoNLPy/MeCab, which stays an
opt-in adapter. What it can do without one:

* ``처리하는`` / ``전송되고`` → the noun ``처리`` / ``전송`` plus a 하다/되다
  ending. Sino-Korean 하다-verbs carry their concept in the root, so this is a
  gain, not just a filter (:func:`split_hada`).
* ``보냈다``, ``이어지므로``, ``빠르게``, ``아닌지`` → tagged ``VV`` and kept out
  of the concept layer (:func:`looks_conjugated`). Before this every Hangul 어절
  was tagged ``NN``, so on a real course folder 3,848 of 6,706 "concepts" were
  one-off inflections — and idf, which rewards rarity, ranked exactly those
  first.

Precision over recall
---------------------
Stripping is ambiguous — ``사과`` (apple) ends in ``과``, a genuine particle, and
``나는`` is both "I + 는" and a conjugation of 날다. Getting it wrong merges
unrelated concepts onto one node, which corrupts the graph silently; getting it
too conservatively wrong merely leaves today's behaviour (no dedup). So the
rules bias hard toward *not* splitting:

* the stem must be at least ``MIN_STEM`` syllables — this is what saves ``사과``
  (stem ``사`` is 1 syllable → left intact), at the cost of missing genuine
  1-syllable nouns like ``차는`` → ``차``;
* the particle must **agree** with the stem's final sound: 이/은/을/과/으로 only
  after a final consonant (받침), 가/는/를/와 only after a vowel. That is a rule
  of Korean, not a heuristic, and it is what saves ``디스플레이`` (레 has no 받침,
  so the 이 is not a particle), ``역효과`` and ``재평가``;
* words in :data:`NOT_SPLIT` (``푸리에``, ``해상도``, ``허프만``) are never split —
  nouns that end in a particle-shaped syllable and pass every other check;
* only particles in :data:`JOSA` are ever stripped, longest match first;
* non-Hangul words are delegated to the EN rule tagger untouched.
"""
from __future__ import annotations

import re
from functools import lru_cache

from .postagger import TOKEN_STRIP, RulePOSTagger, Token

# A stem shorter than this is more likely a false split (사|과) than a real noun.
MIN_STEM = 2

# The copula 이다 and the forms built on it. "파장이다" is the noun 파장 plus the
# copula; left unsplit it is a concept of its own, distinct from 파장, and a
# vault written in the plain "~이다" style loses half its noun occurrences.
# These are matched before the verb-form rules, so "노드이므로" splits as
# 노드 + 이므로 rather than being discarded for ending in 므로.
COPULA: frozenset[str] = frozenset({
    "이다", "이었다", "입니다", "이면", "이므로", "이지만",
    "이고", "이며", "이라고", "이라는", "이라도", "이라면", "이나마",
    "이나", "이란", "이든", "이야",
})

# 조사, longest-first so 에서 wins over 에, 으로서 over 로. Verb endings are
# deliberately absent — see the module docstring.
JOSA: tuple[str, ...] = tuple(sorted(
    set(COPULA) | {
        # compound / multi-syllable
        "에서는", "에게는", "에서도", "에게도", "으로는", "으로도",
        "으로서", "으로써", "에게서", "한테서",
        "라고", "라는", "라도", "라면",
        "에서", "에게", "께서", "한테", "더러",
        "으로", "로서", "로써",
        "부터", "까지", "마다", "조차", "마저", "처럼", "보다", "만큼",
        "밖에", "대로", "커녕", "든지", "치고", "말고",
        # single syllable
        "은", "는", "이", "가", "을", "를", "의", "에", "도", "만",
        "와", "과", "로", "랑", "께", "뿐",
        # 나 / 야 / 여 are deliberately absent. They exist as particles ("너나
        # 나나"), but in written prose they are far more often the tail of a verb
        # ending — 하거[나], 포함하[여], 하여[야] — and stripping them invents
        # words that never existed ("유도하거", "포함하"), which then surface in
        # the UI as concepts. Telling the two apart needs 어미 analysis, which is
        # out of scope; until then the rare real particle is the cheaper loss.
    },
    key=len,
    reverse=True,
))

# Particles whose shape depends on the noun's last sound. Korean picks 이/가,
# 은/는, 을/를, 과/와, 으로/로 by whether the noun ends in a consonant (받침), so
# a "particle" in the wrong shape is not a particle: 디스플레이 cannot be
# 디스플레 + 이, because after a vowel the subject marker is 가.
_NEEDS_BATCHIM = frozenset({"이", "은", "을", "과", "으로", "으로는", "으로도", "으로서", "으로써"})
_NEEDS_VOWEL = frozenset({"가", "는", "를", "와"})
_RO = frozenset({"로", "로서", "로써"})      # after a vowel or ㄹ (서울로, 경로로)

# 받침 index (jongseong) of a precomposed syllable: 0 = none.
_RIEUL = 8
_SSANG_SIOT = 20
# The only finals a Sino-Korean syllable can have (ㄱ ㄴ ㄹ ㅁ ㅂ ㅇ) — see split_hada.
_SINO_FINALS = frozenset({0, 1, 4, 8, 16, 17, 21})

# Nouns that end in a particle-shaped syllable and survive both the stem-length
# and the agreement rule, so only a list can save them. A word here is never
# split; it may still be judged a verb form (받아들이다).
NOT_SPLIT: frozenset[str] = frozenset({
    # loanwords and names
    "푸리에", "마이크로", "매크로", "메트로", "레트로", "허프만", "노이만", "파인만",
    # 도 (度) as a measure suffix, not the particle 도
    "해상도", "정확도", "복잡도", "신뢰도", "유사도", "민감도", "중요도", "선명도",
    "자유도", "난이도", "만족도", "활용도", "의존도", "집중도", "응집도", "결합도",
    "이용도", "충실도", "가용도", "혼잡도", "밀집도",
    "재정의",
    # verbs whose tail looks like the copula
    "받아들이다", "끌어들이다", "기울이다",
})

# 보다 is a particle ("생각보다") and a verb ("알아보다"). After a connective
# ending in 아/어/해 it is the verb.
_CONNECTIVE_TAILS = frozenset("아어여해워펴봐줘쳐져써겨혀려러")

HANGUL_RE = re.compile(r"[가-힣]")

# Korean closed-class words — the counterpart of postagger._LEXICON, which only
# covers English. Without this the two languages are filtered asymmetrically: an
# English note's "the/of/here" are dropped as function words while a Korean
# note's 여기/위해/함께 are treated as concepts and link notes that share nothing.
#
# These are genuinely closed classes (의존명사, 부사, 접속사, 형식용언), so
# enumerating them is the same move as the English lexicon, not a patch over a
# ranking problem. Conjugated verbs are *not* here — see looks_conjugated.
STOP_KO: frozenset[str] = frozenset({
    # 의존명사 / 형식명사
    "것", "것이", "것을", "것은", "수", "때", "곳", "등", "점", "바", "뿐",
    "중", "간", "채", "만큼", "대로", "무엇", "누구", "거", "게",
    # 지시/의문
    "이것", "그것", "저것", "여기", "거기", "저기", "어디", "언제", "누가",
    "왜", "어떻게", "무슨", "어느", "이런", "그런", "저런", "이렇게", "그렇게",
    # 부사
    "매우", "아주", "너무", "정말", "특히", "다시", "또", "또한", "더", "덜",
    "가장", "잘", "많이", "조금", "함께", "같이", "먼저", "이미", "아직",
    "바로", "직접", "자세히", "제대로", "실제로", "물론", "역시", "즉",
    "예를", "보통", "항상", "늘", "종종", "가끔", "결국", "따라서",
    # 접속
    "그리고", "그러나", "하지만", "그래서", "그런데", "또는", "혹은", "및",
    "만약", "만일", "다만", "단", "때문", "때문에", "덕분에", "대신",
    # 형식용언·기능표현 (활용형이지만 닫힌 집합처럼 굳어진 것들)
    "위해", "위한", "통해", "통한", "대해", "대한", "관해", "관한",
    "있다", "없다", "하다", "되다", "이다", "아니다",
})

# ------------------------------------------------------------ verb forms

# Endings of 하다/되다 verbs and adjectives. 처리하는 = 처리 + 하는: the root is
# the concept. Single-syllable 관형형 (한/할/된/될) are included but guarded by
# _HADA_NOUN_TAILS, because 시분할 and 무제한 are nouns, not 시분 + 할.
# 해/함/됨/돼 are left out entirely: 인수분해 and 우편함 are nouns too, and
# there is no cheap way to tell.
HADA_ENDINGS: frozenset[str] = frozenset({
    "하다", "한다", "하는", "하고", "하지", "하면", "하게", "하여", "하며", "해서",
    "했다", "했고", "했던", "했는", "했으며", "해야", "하자", "합니다", "하므로",
    "하니", "하니까", "하던", "하기", "하기에", "할까", "할지", "하려면", "하려고",
    "하도록", "하면서", "하지만", "하는데", "하는지", "한지", "한데", "하였다",
    "하였고", "하여야", "해도", "하거나", "하든", "할수록", "하며", "하세요",
    "한", "할",
    "되다", "된다", "되는", "되고", "되지", "되면", "되게", "되어", "되었다",
    "되었고", "되며", "돼서", "됐다", "됐고", "돼야", "됩니다", "되므로", "되도록",
    "되면서", "되지만", "되는데", "되는지", "된지", "되기", "되던", "될까", "될지",
    "되거나", "되어야", "돼도", "되어도",
    "된", "될",
})
_HADA_NOUN_TAILS = frozenset({
    "분할", "역할", "제한", "권한", "기한", "무한", "유한", "상한", "하한", "시한",
})

# Endings that only ever close a verb or adjective — no noun ends this way.
_VERB_ENDINGS: tuple[str, ...] = (
    "므로", "지만", "면서", "니까", "도록", "려고", "려면", "으면",
    "어서", "아서", "여서", "워서", "져서", "쳐서",
    "어야", "아야", "여야", "어도", "아도", "으며", "으니",
    "는데", "은데", "는지", "는가", "는다", "던가", "을까", "을지",
    "거나", "듯",
)

# Nouns that the looser rules below would otherwise catch.
_DA_NOUNS = frozenset({
    "바다", "람다", "판다", "아젠다", "베란다", "우간다", "캐나다", "네바다",
    "아나콘다", "플로리다",
})
_GO_NOUN_TAILS = frozenset({
    "장고", "창고", "광고", "참고", "최고", "경고", "재고", "보고", "사고", "금고",
    "원고", "신고",
})
_GE_NOUN_TAILS = frozenset({"무게", "가게", "지게"})
_NIEUN_JI_NOUNS: tuple[str, ...] = ("체인지", "레인지", "오렌지", "스펀지", "챌린지")


def _is_hangul(word: str) -> bool:
    return bool(HANGUL_RE.search(word))


def _batchim(ch: str) -> int | None:
    """Final-consonant index of a Hangul syllable (0 = none); None otherwise."""
    code = ord(ch) - 0xAC00
    if 0 <= code < 11172:
        return code % 28
    return None


def _agrees(stem: str, josa: str) -> bool:
    """Whether ``josa`` has the right shape for a noun ending like ``stem``.

    Only Hangul stems are judged: after a latin word (``JSON은``, ``agent가``)
    the choice follows pronunciation, which spelling cannot tell us.
    """
    b = _batchim(stem[-1])
    if b is None:
        return True
    if josa in _NEEDS_BATCHIM:
        return b != 0
    if josa in _NEEDS_VOWEL:
        return b == 0
    if josa in _RO:
        return b in (0, _RIEUL)
    return True


def _ok_stem(stem: str) -> bool:
    """Whether ``stem`` can stand alone once a 조사 is removed.

    Hangul stems need ``MIN_STEM`` syllables — that is what stops ``사과``
    becoming ``사``+``과``.

    A **latin/alnum** stem is judged differently and deliberately so. Korean
    technical writing attaches 조사 straight onto English words — ``agent가``,
    ``args의``, ``CSV로``, ``JSON은`` — and requiring a Hangul stem meant none of
    them ever split, so ``agent``/``agent가``/``agent를`` stayed three unrelated
    concepts. The homograph risk that motivates ``MIN_STEM`` doesn't arise here:
    an ASCII word ending in a Hangul syllable is a 조사 attachment, never a word
    that merely looks like one.
    """
    if not stem:
        return False
    if _is_hangul(stem):
        return len(stem) >= MIN_STEM
    return len(stem) >= 2 and any(ch.isalnum() for ch in stem)


def _blocked(stem: str, josa: str) -> bool:
    """Splits that pass the general rules but are known verb shapes."""
    if josa == "보다" and stem[-1] in _CONNECTIVE_TAILS:
        return True                       # 알아보다, 해보다
    if josa in COPULA and len(stem) >= 3 and stem.endswith(("아들", "어들")):
        return True                       # 받아들이고, 끌어들이며
    return False


def _split(word: str) -> tuple[str, bool, str | None]:
    """``(stem, plural, josa)`` — the whole heuristic, for the adapter and tests.

    Punctuation is stripped again from the stem, because the adapter can only
    strip the ends of the 어절: ``**속도**는`` arrives as ``속도**는``, and
    ``"비트"는`` as ``비트"는``. Without the second strip those became the
    concepts ``속도**`` and ``비트"``.
    """
    if word in NOT_SPLIT:
        return word, False, None
    stem, josa = word, None
    for j in JOSA:
        if len(word) > len(j) and word.endswith(j):
            raw = word[: -len(j)]
            cand = raw.strip(TOKEN_STRIP)
            if cand != raw and cand:
                # A quoted word with a particle outside the quotes —
                # "방송부원들은"으로 — carries its own particle inside them.
                # Peel that one too; the inner particle is not emitted.
                inner, inner_plural, inner_josa = _split(cand)
                if inner_josa or inner_plural:
                    return inner, inner_plural, j
            if _ok_stem(cand) and _agrees(cand, j) and not _blocked(cand, j):
                stem, josa = cand, j
            # a longest-match that fails means every shorter suffix of it
            # would be a more aggressive guess — stop here.
            break
    plural = False
    # 들 marks a plural noun: 우체국들 / 호스트들이 / agent들. Same stem rule, so
    # 그들 and 만들(다) are never cut.
    if stem.endswith("들"):
        base = stem[:-1].strip(TOKEN_STRIP)
        if _ok_stem(base) and base not in NOT_SPLIT:
            stem, plural = base, True
    return stem, plural, josa


def split_josa(word: str) -> tuple[str, str | None]:
    """Split one 어절 into ``(stem, suffix)``; ``suffix`` is None when nothing
    was stripped. The suffix is the particle, prefixed by 들 for a plural. Pure
    function — the splitting heuristic lives in :func:`_split`.

    >>> split_josa("문어는")
    ('문어', '는')
    >>> split_josa("agent가")       # 조사 on a latin word
    ('agent', '가')
    >>> split_josa("사과")          # stem would be 1 syllable → left alone
    ('사과', None)
    >>> split_josa("디스플레이")    # 이 after a vowel is not a particle
    ('디스플레이', None)
    >>> split_josa("파장이다")      # copula
    ('파장', '이다')
    >>> split_josa("우체국들이")
    ('우체국', '들이')
    """
    stem, plural, josa = _split(word)
    suffix = ("들" if plural else "") + (josa or "")
    return stem, (suffix or None)


def split_hada(word: str) -> tuple[str, str] | None:
    """``(root, ending)`` for a 하다/되다 form like ``처리하는``, else None.

    The root has to look Sino-Korean: two or more syllables, and a last syllable
    whose final consonant is one a Sino-Korean syllable can have (ㄱ ㄴ ㄹ ㅁ ㅂ
    ㅇ or none). That keeps native adjective roots out — 비슷하다, 깨끗하다,
    따뜻하다 all end in ㅅ — and a reduplicated root (똑똑, 넉넉) is refused too.
    Those words are still recognised as verb forms; they just yield no noun.
    """
    for i in range(MIN_STEM, len(word)):
        root, rest = word[:i], word[i:]
        if rest not in HADA_ENDINGS:
            continue
        if len(rest) == 1 and word[-2:] in _HADA_NOUN_TAILS:
            return None                   # 시분할, 무제한
        if not all(_batchim(ch) is not None for ch in root):
            return None
        if _batchim(root[-1]) not in _SINO_FINALS or root[-1] == root[-2]:
            return None
        if root in STOP_KO or looks_conjugated(root):
            return None
        return root, rest
    return None


def _surely_conjugated(word: str) -> bool:
    """The rules no word ending in a real particle can trip: ㅆ 받침, a
    하다/되다 ending, or a verb-only connective. ``생각보다`` ends in 다 but the
    다 belongs to the particle 보다, so the looser 다-rule must not see it."""
    if any(_batchim(ch) == _SSANG_SIOT for ch in word):
        return True
    if word in HADA_ENDINGS:
        return True
    if any(word.endswith(e) for e in HADA_ENDINGS if len(e) >= 2):
        return True
    return word.endswith(_VERB_ENDINGS)


def looks_conjugated(word: str) -> bool:
    """Whether a Hangul word is a verb/adjective form rather than a noun.

    Judged on the surface, because that is all the store has (the POS is baked
    into the canonical id). Every rule is one that no ordinary noun breaks, or
    comes with the short list of nouns that do:

    * a syllable with ㅆ 받침 — past tense and 있다 (보냈다, 했던, 있는);
    * a 하다/되다 ending (``HADA_ENDINGS``) or a connective that only verbs take
      (므로, 지만, 면서, 는지 …);
    * ending in 다, or 며 (빠르다, 흐르며), bar a few nouns (바다, 람다);
    * three or more syllables ending in 고/게/한/할/된/될/냐/려, or in 지 after
      a ㄴ 받침 (보내고, 빠르게, 분류한, 일부냐, 아닌지), bar the nouns that end
      so (냉장고, 무게, 시분할, 체인지).
    """
    if not word or not _is_hangul(word[-1]):
        return False
    if _surely_conjugated(word):
        return True
    last = word[-1]
    if last == "다":
        return len(word) >= 2 and word not in _DA_NOUNS
    if last == "며":
        return len(word) >= 2
    if len(word) < 3:
        return False
    tail = word[-2:]
    if last == "고":
        return tail not in _GO_NOUN_TAILS
    if last == "게":
        return tail not in _GE_NOUN_TAILS
    if last in "한할된될":
        return tail not in _HADA_NOUN_TAILS
    if last in "냐려":                                  # 일부냐, 붙이려 (but 케냐, 고려)
        return True
    if last == "지" and _batchim(word[-2]) == 4:       # ㄴ 받침 + 지: 아닌지
        return not word.endswith(_NIEUN_JI_NOUNS)
    return False


class KoreanRuleAdapter:
    """POS-tag Korean by stripping 조사; delegate non-Hangul to the EN tagger.

    Stems are tagged ``NN`` so they share the canonical-id space with English
    nouns (the id is ``(text, pos)``); particles get ``JO`` so they stay
    inspectable as their own low-value nodes, the way ``the`` does in English.
    Verb forms are tagged ``VV`` whole; a 하다/되다 form yields its root as
    ``NN`` and the ending as ``XSV``; a plural 들 is ``XSN``.
    """

    name = "ko-rule"

    def __init__(self) -> None:
        self._en = RulePOSTagger()

    def tag(self, text: str) -> list[Token]:
        out: list[Token] = []
        for raw in text.split():
            # A middle dot lists items inside one 어절 (ACK·타임아웃·순서); each
            # item is its own word.
            for piece in raw.split("·"):
                # Strip punctuation from both ends — leading too, or "[[두족류"
                # (from a wikilink) survives as a token and then as a concept.
                # Punctuation is dropped rather than emitted, matching the EN
                # tokenizer.
                word = piece.strip(TOKEN_STRIP)
                if not word:
                    continue
                if not _is_hangul(word):
                    # latin/number inside a Korean note: the EN rules still apply
                    out.extend(self._en.tag(word))
                    continue
                out.extend(self._tag_word(word))
        return out

    def _tag_word(self, word: str) -> list[Token]:
        hada = split_hada(word)
        if hada:
            root, rest = hada
            return [Token(text=root, pos="NN", lemma=root),
                    Token(text=rest, pos="XSV", lemma=rest)]
        stem, plural, josa = _split(word)
        if josa is None:
            if looks_conjugated(word):
                return [Token(text=word, pos="VV", lemma=word)]
        else:
            # A particle was found. The copula wins outright (노드이므로 is a
            # noun though it ends in 므로); any other particle only yields to
            # the sure verb rules (보냈다는 = 보냈다 + 는), never to the loose
            # ones — 생각보다 ends in 다 because 보다 does.
            if josa not in COPULA and _surely_conjugated(word):
                return [Token(text=word, pos="VV", lemma=word)]
            if _is_hangul(stem) and looks_conjugated(stem):
                return [Token(text=word, pos="VV", lemma=word)]
        out: list[Token] = []
        if _is_hangul(stem):
            out.append(Token(text=stem, pos="NN", lemma=stem))
        else:
            # a latin stem left by a 조사 ("JSON은" → "JSON") must be tagged
            # by the EN rules, or its canonical id would differ from the same
            # word written without a 조사 — (JSON, NN) here vs (JSON, NNP)
            # there, and the concept would not link.
            out.extend(self._en.tag(stem))
        if plural:
            out.append(Token(text="들", pos="XSN", lemma="들"))
        if josa:
            out.append(Token(text=josa, pos="JO", lemma=josa))
        return out


_ADAPTER: KoreanRuleAdapter | None = None


@lru_cache(maxsize=65536)
def is_verb_form(word: str) -> bool:
    """Whether the adapter keeps ``word`` out of the concept layer — the
    question the store asks of every stored Hangul label.

    It is answered by the adapter itself so the two cannot disagree. A surface
    test on the label alone would miss ``나은지는``: the adapter judges it a verb
    only once the 는 is off, and stores the whole word as ``VV``.
    """
    global _ADAPTER
    if _ADAPTER is None:
        _ADAPTER = KoreanRuleAdapter()
    return not any(t.pos == "NN" for t in _ADAPTER._tag_word(word))
