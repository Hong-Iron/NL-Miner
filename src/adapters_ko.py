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
This handles **조사 only** — the particles that attach to nouns. Verb/adjective
endings (어미: 높고, 뛰어나다) are left alone; real 어미 handling needs a proper
morphological analyzer (KoNLPy/MeCab), which stays an opt-in adapter. So a
Korean note gets reliable *noun* concepts, not a full parse. That is deliberate:
nouns are what concept linking runs on.

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
* only particles in :data:`JOSA` are ever stripped, longest match first;
* non-Hangul words are delegated to the EN rule tagger untouched.
"""
from __future__ import annotations

import re

from .postagger import TOKEN_STRIP, RulePOSTagger, Token

# A stem shorter than this is more likely a false split (사|과) than a real noun.
MIN_STEM = 2

# 조사, longest-first so 에서 wins over 에, 으로서 over 로. Verb endings are
# deliberately absent — see the module docstring.
JOSA: tuple[str, ...] = tuple(sorted(
    (
        # compound / multi-syllable
        "에서는", "에게는", "에서도", "에게도", "으로는", "으로도",
        "이라고", "이라는", "이라도", "이라면", "이나마",
        "으로서", "으로써", "에게서", "한테서",
        "라고", "라는", "라도", "라면",
        "에서", "에게", "께서", "한테", "더러",
        "으로", "로서", "로써",
        "부터", "까지", "마다", "조차", "마저", "처럼", "보다", "만큼",
        "밖에", "대로", "커녕", "이나", "이란", "이든", "든지", "이며",
        "이고", "이야", "이란", "치고", "말고",
        # single syllable
        "은", "는", "이", "가", "을", "를", "의", "에", "도", "만",
        "와", "과", "로", "랑", "께", "뿐",
        # 나 / 야 / 여 are deliberately absent. They exist as particles ("너나
        # 나나"), but in written prose they are far more often the tail of a verb
        # ending — 하거[나], 포함하[여], 하여[야] — and stripping them invents
        # words that never existed ("유도하거", "포함하"), which then surface in
        # the UI as concepts. Telling the two apart needs 어미 analysis, which is
        # out of scope; until then the rare real particle is the cheaper loss.
    ),
    key=len,
    reverse=True,
))

HANGUL_RE = re.compile(r"[가-힣]")

# Korean closed-class words — the counterpart of postagger._LEXICON, which only
# covers English. Without this the two languages are filtered asymmetrically: an
# English note's "the/of/here" are dropped as function words while a Korean
# note's 여기/위해/함께 are treated as concepts and link notes that share nothing.
#
# These are genuinely closed classes (의존명사, 부사, 접속사, 형식용언), so
# enumerating them is the same move as the English lexicon, not a patch over a
# ranking problem. Conjugated verbs are *not* here and cannot be — 어미 handling
# needs a real analyzer; see the module docstring.
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


def _is_hangul(word: str) -> bool:
    return bool(HANGUL_RE.search(word))


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


def split_josa(word: str) -> tuple[str, str | None]:
    """Split one 어절 into ``(stem, josa)``; ``josa`` is None when nothing was
    stripped. Pure function — the whole heuristic lives here so it can be
    tested directly.

    >>> split_josa("문어는")
    ('문어', '는')
    >>> split_josa("agent가")       # 조사 on a latin word
    ('agent', '가')
    >>> split_josa("사과")          # stem would be 1 syllable → left alone
    ('사과', None)
    """
    for j in JOSA:
        if len(word) > len(j) and word.endswith(j):
            stem = word[: -len(j)]
            if _ok_stem(stem):
                return stem, j
            # a longest-match that fails the stem rule means every shorter
            # suffix of it would too — stop rather than fall through to a
            # more aggressive split.
            break
    return word, None


class KoreanRuleAdapter:
    """POS-tag Korean by stripping 조사; delegate non-Hangul to the EN tagger.

    Stems are tagged ``NN`` so they share the canonical-id space with English
    nouns (the id is ``(text, pos)``); particles get ``JO`` so they stay
    inspectable as their own low-value nodes, the way ``the`` does in English.
    """

    name = "ko-rule"

    def __init__(self) -> None:
        self._en = RulePOSTagger()

    def tag(self, text: str) -> list[Token]:
        out: list[Token] = []
        for raw in text.split():
            # Strip punctuation from both ends — leading too, or "[[두족류" (from
            # a wikilink) survives as a token and then as a concept. Punctuation
            # is dropped rather than emitted, matching the EN tokenizer.
            word = raw.strip(TOKEN_STRIP)
            if not word:
                continue
            if not _is_hangul(word):
                # latin/number inside a Korean note: the EN rules still apply
                out.extend(self._en.tag(word))
                continue
            stem, josa = split_josa(word)
            if _is_hangul(stem):
                out.append(Token(text=stem, pos="NN", lemma=stem))
            else:
                # a latin stem left by a 조사 ("JSON은" → "JSON") must be tagged
                # by the EN rules, or its canonical id would differ from the same
                # word written without a 조사 — (JSON, NN) here vs (JSON, NNP)
                # there, and the concept would not link.
                out.extend(self._en.tag(stem))
            if josa:
                out.append(Token(text=josa, pos="JO", lemma=josa))
        return out
