"""Phase 2 — flat rule-based POS tagger (EN-first, no external deps).

Per phase2_parsers.md §2.4–§2.5 the POS tagger is the *first* Phase 2 piece
because linker's per-token canonical-id ``canonical_id(label, pos)`` needs a
real POS (Phase 1 used the ``"NA"`` sentinel). We stay "no-LM": a hand lexicon
of closed-class words + suffix/morphology heuristics, no model, no training.

An adapter interface (:class:`MorphAdapter`) lets a heavier analyzer
(spaCy / KoNLPy / MeCab / jieba) be swapped in via the ``NLM_MORPH`` env var;
those are *optional* and feature-flagged — if the package is absent we fall
back to the rule tagger so the default install stays pydantic-only. For Korean
/ Japanese the rule tagger is admittedly weak (CJK needs morphological analysis
for real POS); the limitation is documented, not hidden.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Protocol

# --------------------------------------------------------------------- Token


@dataclass(frozen=True)
class Token:
    """Uniform token — the parsing/linker layers operate on these.

    ``pos`` defaults to ``"NA"`` (Phase 1 sentinel) so a tagger that cannot
    decide a word still yields a stable canonical id via :func:`canonical_id`.
    """
    text: str
    pos: str = "NA"
    lemma: str | None = None

    @property
    def canonical(self) -> str:
        from .schema import canonical_id
        return canonical_id(self.text, self.pos)


# ----------------------------------------------------------------- lexicon (EN)

# Closed-class / function words → deterministic, no heuristic needed.
_LEXICON: dict[str, str] = {
    # determiners
    "the": "DT", "a": "DT", "an": "DT", "this": "DT", "that": "DT",
    "these": "DT", "those": "DT", "every": "DT", "some": "DT", "any": "DT",
    "no": "DT",
    # prepositions
    "in": "IN", "on": "IN", "at": "IN", "by": "IN", "for": "IN", "of": "IN",
    "to": "IN", "with": "IN", "from": "IN", "over": "IN", "under": "IN",
    "into": "IN", "through": "IN", "about": "IN", "between": "IN", "after": "IN",
    "before": "IN", "during": "IN", "without": "IN", "among": "IN",
    # pronouns
    "i": "PRP", "you": "PRP", "he": "PRP", "she": "PRP", "it": "PRP",
    "we": "PRP", "they": "PRP", "me": "PRP", "him": "PRP", "her": "PRP",
    "us": "PRP", "them": "PRP", "my": "PRP$", "your": "PRP$", "his": "PRP$",
    "its": "PRP$", "our": "PRP$", "their": "PRP$", "who": "WP", "what": "WP",
    "which": "WDT", "that_rel": "WDT",
    # auxiliaries / modals
    "is": "VBZ", "are": "VBP", "was": "VBD", "were": "VBD", "be": "VB",
    "been": "VBN", "being": "VBG", "am": "VBP",
    "has": "VBZ", "have": "VBP", "had": "VBD", "having": "VBG",
    "do": "VB", "does": "VBZ", "did": "VBD", "doing": "VBG", "done": "VBN",
    "will": "MD", "would": "MD", "shall": "MD", "should": "MD",
    "can": "MD", "could": "MD", "may": "MD", "might": "MD", "must": "MD",
    # coordinating conjunctions
    "and": "CC", "or": "CC", "but": "CC", "nor": "CC", "so": "CC", "yet": "CC",
    # particles / negation
    "not": "RB", "n't": "RB", "very": "RB", "too": "RB",
    # existence / there
    "there": "EX",
}

# Suffix → POS heuristics, tried in order (first match wins).
_SUFFIX_RULES: tuple[tuple[str, str], ...] = (
    ("ing", "VBG"),
    ("ed", "VBD"),
    ("ly", "RB"),
    ("tion", "NN"), ("sion", "NN"), ("ment", "NN"), ("ness", "NN"),
    ("ity", "NN"), ("ship", "NN"),
    ("able", "JJ"), ("ible", "JJ"), ("ful", "JJ"), ("less", "JJ"),
    ("ous", "JJ"), ("ive", "JJ"), ("al", "JJ"), ("ic", "JJ"),
)

_NUM_RE = re.compile(r"^[+-]?(\d+([.,]\d+)?|\d+:\d+)$")
_HAS_DIGIT_RE = re.compile(r"\d")
_CAPITALIZED_RE = re.compile(r"^[A-Z]")


# Stripped from both ends of every token. Beyond ordinary punctuation this
# covers markdown and wikilink syntax, which would otherwise become concepts in
# their own right: "[[Other Note]]" yields the tokens "[[Other" and "Note]]",
# and "**bold**" a node distinct from "bold". Only the ends are stripped, so
# "snake_case" survives intact. Curly quotes and corner brackets are how Korean
# prose often quotes a word (“비트”, 「정의」); left in, they glue onto it.
TOKEN_STRIP = ".,;:!?\"'()[]{}*_~`#<>|«»…“”‘’「」『』"


def _word_tokenize(text: str) -> list[str]:
    """Whitespace + simple punctuation separation (EN-oriented)."""
    # keep contractions as one token before splitting
    t = re.sub(r"(\w)([.,;:!?'\"])", r"\1 \2", text)
    toks = []
    for w in re.findall(r"\S+", t):
        w = w.strip(TOKEN_STRIP)
        if w:
            toks.append(w)
    return toks


# Sentence boundaries, needed because ``_word_tokenize`` strips punctuation and
# so cannot report one. A newline counts: list items and headings start a fresh
# capital without any terminator ("- Tools are useful").
_SENT_START_RE = re.compile(r"(?<=[.!?])\s+|\n+")


def _suffix_pos(word: str) -> str | None:
    low = word.lower()
    for suf, pos in _SUFFIX_RULES:
        if low.endswith(suf) and len(low) > len(suf):
            return pos
    return None


# ----------------------------------------------------------------- rule tagger


class RulePOSTagger:
    """Hand rule + lemma-lookup POS tagger (EN). No external deps."""

    name = "rule"

    def tag(self, text: str) -> list[Token]:
        out: list[Token] = []
        # Tag per sentence so the first word of each is identifiable. Splitting
        # first cannot change the token stream: the boundary is whitespace, and
        # ``_word_tokenize`` splits on whitespace anyway.
        for sent in _SENT_START_RE.split(text):
            out.extend(self._tag_sentence(sent))
        return out

    def _tag_sentence(self, text: str) -> list[Token]:
        out: list[Token] = []
        # "first" means the first *word*, not the first token: markdown lines
        # open with bullets and emphasis ("- Tools are useful"), and those must
        # not consume the sentence-initial position.
        pending_start = True
        for w in _word_tokenize(text):
            pos = self._pos(w)
            i = 0 if pending_start else 1
            if any(ch.isalnum() for ch in w):
                pending_start = False
            # Attach the lemma the rules can already derive. Node identity stays
            # ``canonical_id(text, pos)`` — surface, so the graph still says what
            # the note says — but the store folds concepts on the lemma, which is
            # what makes "dogs" and "dog" one concept for linking purposes.
            #
            # A capitalised word is tagged NNP, and proper nouns must never be
            # plural-stripped ("Athens" is not "Athen"). But at the start of a
            # sentence capitalisation is orthography, not evidence — so "Tools
            # are useful." would keep its NNP tag and never fold onto "tool",
            # and plurals open sentences constantly. Where the capital carries no
            # information, re-read the POS from the lowercased form for lemma
            # purposes only; ``pos`` itself (and therefore node identity) is
            # untouched, and a mid-sentence "Athens" still lemmatises to itself.
            lemma_pos = pos
            if i == 0 and pos == "NNP":
                lemma_pos = self._pos(w.lower())
            out.append(Token(text=w, pos=pos, lemma=self.lemma(w, lemma_pos)))
        return out

    @staticmethod
    def _pos(word: str) -> str:
        low = word.lower()
        # 1. closed-class lexicon (and obvious inflections by lookup)
        if low in _LEXICON:
            return _LEXICON[low]
        # 2. numbers
        if _NUM_RE.match(low):
            return "CD"
        if _HAS_DIGIT_RE.search(low) and not low.isalpha():
            return "CD"
        # 3. capitalised (likely proper noun) — only at non-lexicon words
        if _CAPITALIZED_RE.match(word):
            return "NNP"
        # 4. suffix morphology
        sp = _suffix_pos(word)
        if sp:
            return sp
        # 5. plural / 3rd-person -s heuristic
        if low.endswith("s") and not low.endswith("ss") and len(low) > 3:
            return "NNS"
        # 6. fallback: common noun
        return "NN"

    # A singular ending in -is/-us ("analysis", "octopus", "status", "virus")
    # trips the naive "ends in s → plural" rule and gets tagged NNS, so
    # stripping the s would invent "analysi"/"octopu". Plurals that genuinely
    # end -is/-us are vanishingly rare in English, which makes declining to
    # strip them the safe trade. -as is deliberately *not* excluded: "ideas",
    # "areas", "cameras" are ordinary plurals.
    _NO_S_STRIP = ("ss", "is", "us")

    @staticmethod
    def lemma(word: str, pos: str) -> str:
        low = word.lower()
        if pos in ("VBD", "VBN") and low.endswith("ed"):
            return low[:-2]
        if pos == "VBG" and low.endswith("ing"):
            return low[:-3]
        if pos in ("NNS", "VBZ") and len(low) > 3:
            # "-es" after a sibilant carries the whole plural ("boxes"→"box",
            # "processes"→"process", "octopuses"→"octopus"); elsewhere only the
            # "s" is the marker ("notes"→"note"). Checked first, since the
            # bare-s rule would otherwise leave "boxe".
            if (
                low.endswith("es")
                and len(low) > 4
                and low[:-2].endswith(("s", "x", "z", "ch", "sh"))
            ):
                return low[:-2]
            if low.endswith("s") and not low.endswith(RulePOSTagger._NO_S_STRIP):
                return low[:-1]
        return low


# --------------------------------------------------------------- adapter API


class MorphAdapter(Protocol):
    """Uniform interface every analyzer implements (ARCHITECTURE §2.1)."""
    name: str
    def tag(self, text: str) -> list[Token]: ...


class AutoAdapter:
    """Dispatch to a script-appropriate tagger per text.

    The rule tagger is EN-shaped: unknown words fall back to ``NN``, so on
    Korean every 어절 came back a noun *and* kept its 조사 attached — meaning
    ``문어는``/``문어가`` were distinct canonical nodes and never linked. Routing
    Korean to :class:`~src.adapters_ko.KoreanRuleAdapter` fixes the dedup that
    concept linking depends on.

    English is unchanged by construction: ``detect_lang`` returns ``"en"`` and
    this delegates to the very same :class:`RulePOSTagger` instance the default
    used before, so existing behaviour and ids are byte-identical.
    """

    name = "auto"

    def __init__(self) -> None:
        self._en = RulePOSTagger()
        self._ko: MorphAdapter | None = None

    def _korean(self) -> MorphAdapter:
        if self._ko is None:
            from .adapters_ko import KoreanRuleAdapter
            self._ko = KoreanRuleAdapter()
        return self._ko

    def tag(self, text: str) -> list[Token]:
        from .preprocess import detect_lang
        if detect_lang(text) == "ko":
            return self._korean().tag(text)
        return self._en.tag(text)


def get_adapter(env: str | None = None) -> MorphAdapter:
    """Return the configured :class:`MorphAdapter`. ``NLM_MORPH`` env selects
    an optional external analyzer; missing/unrecognised → :class:`AutoAdapter`,
    which routes by script (EN rule tagger for latin, 조사 splitter for Hangul)."""
    env = env or os.environ.get("NLM_MORPH", "").strip().lower()
    if env in ("rule", "en"):
        return RulePOSTagger()
    if env == "ko":
        try:
            from .adapters_ko import KoreanRuleAdapter
            return KoreanRuleAdapter()
        except Exception:
            pass
    if env == "spacy":
        try:
            from .adapters_spacy import SpaCyAdapter  # type: ignore
            return SpaCyAdapter()
        except Exception:
            pass
    if env == "konlpy":
        try:
            from .adapters_konlpy import KonlpyAdapter  # type: ignore
            return KonlpyAdapter()
        except Exception:
            pass
    if env == "mecab":
        try:
            from .adapters_mecab import MeCabAdapter  # type: ignore
            return MeCabAdapter()
        except Exception:
            pass
    if env == "jieba":
        try:
            from .adapters_jieba import JiebaAdapter  # type: ignore
            return JiebaAdapter()
        except Exception:
            pass
    return AutoAdapter()


def tag(text: str, adapter: MorphAdapter | None = None) -> list[Token]:
    """Convenience: tag with the configured adapter (or one passed in)."""
    a = adapter or get_adapter()
    return a.tag(text)
