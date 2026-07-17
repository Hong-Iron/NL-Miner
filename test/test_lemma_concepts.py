"""Inflection folding — "dogs" and "dog" are one concept.

``RulePOSTagger.lemma`` existed but ``tag()`` never called it, so ``Token.lemma``
was always None and the store had nothing to fold. Node identity deliberately
stays on the *surface* form (``canonical_id(text, pos)``) so the graph still
says what the note says; the lemma rides alongside purely so concept queries can
fold. Since the tagger lowercases the lemma, one key covers both inflection and
sentence-initial capitalisation.

The rules bias against inventing words: over-stripping ("octopus"→"octopu")
would put a nonexistent concept in the UI and merge unrelated notes, while
under-stripping merely misses a suggestion.
"""
import pytest

from src.context import process
from src.postagger import RulePOSTagger
from src.schema import FileMeta
from src.store import GraphStore


def _lemma(word: str) -> str:
    t = RulePOSTagger()
    return t.lemma(word, t._pos(word))


# ------------------------------------------------------------ lemma rules


@pytest.mark.parametrize("word,expected", [
    ("dogs", "dog"), ("notes", "note"), ("plugins", "plugin"),
    ("ideas", "idea"), ("areas", "area"),
    ("walked", "walk"), ("walking", "walk"),
    ("Camouflage", "camouflage"),          # case folds too
])
def test_inflections_fold(word, expected):
    assert _lemma(word) == expected


@pytest.mark.parametrize("word,expected", [
    ("boxes", "box"), ("dishes", "dish"), ("churches", "church"),
    ("buses", "bus"), ("processes", "process"), ("octopuses", "octopus"),
])
def test_es_plural_after_sibilant_drops_both_letters(word, expected):
    """The bare-s rule alone would leave "boxe"."""
    assert _lemma(word) == expected


@pytest.mark.parametrize("word", [
    "octopus", "analysis", "status", "virus", "crisis", "axis",
    "class", "process", "bus", "gas",
])
def test_latin_singulars_are_not_mistaken_for_plurals(word):
    """These end in -s/-is/-us and trip the naive "ends in s → plural" tag;
    stripping would invent "octopu"/"analysi"."""
    assert _lemma(word) == word.lower()


def test_tag_attaches_the_lemma():
    toks = {t.text: t.lemma for t in RulePOSTagger().tag("The dogs walked")}
    assert toks["dogs"] == "dog"
    assert toks["walked"] == "walk"


# ------------------------------------------------- folding reaches concepts


def _vault(tmp_path, files: dict[str, str]) -> GraphStore:
    store = GraphStore(tmp_path / "g.db")
    for name, text in files.items():
        p = tmp_path / name
        p.write_text(text, encoding="utf-8")
        ctx, _ = process(FileMeta(file_path=p, file_name=name), pos=True)
        store.upsert_file(ctx)
    return store


def test_bridge_matches_singular_against_plural(tmp_path):
    store = _vault(tmp_path, {
        "a.md": "# A\nThe octopus uses a tool.\n",
        "b.md": "# B\nMany animals use tools.\n",
    })
    try:
        fu = store.file_uuid_for("a.md")
        shared = {s.lower() for r in store.bridges(fu) for s in r["shared"]}
        assert "tool" in shared, "tool/tools must be one concept"
    finally:
        store.close()


def test_top_concepts_counts_inflections_once(tmp_path):
    store = _vault(tmp_path, {
        "a.md": "# A\nA tool is useful.\n",
        "b.md": "# B\nTools are useful.\n",
        "c.md": "# C\nThe tool broke.\n",
    })
    try:
        rows = [r for r in store.top_concepts(min_files=1) if r[0] == "tool"]
        assert len(rows) == 1, "tool/tools/Tools must be one row"
        assert rows[0][2] == 3, "and must count all three notes"
    finally:
        store.close()


def test_node_labels_keep_the_surface_form(tmp_path):
    """The fold is query-only: the graph must still show what the note wrote."""
    store = _vault(tmp_path, {"a.md": "# A\nThe dogs barked.\n"})
    try:
        labels = [
            r[0] for r in store.conn.execute(
                "SELECT label FROM nodes WHERE kind='token'"
            )
        ]
        assert "dogs" in labels, "node label must stay the surface form"
        assert "dog" not in labels
    finally:
        store.close()


# ------------------------------------------- sentence-initial capitalisation


def _tag_lemmas(text: str) -> dict[str, str | None]:
    return {t.text: t.lemma for t in RulePOSTagger().tag(text)}


@pytest.mark.parametrize("text,word,expected", [
    ("Tools are useful.", "Tools", "tool"),
    ("Octopuses are smart.", "Octopuses", "octopus"),
    ("Dogs bark.", "Dogs", "dog"),
    ("A dog barks. Dogs bark.", "Dogs", "dog"),   # initial after a full stop
    ("- Tools are useful", "Tools", "tool"),      # a bullet is not the word
    ("Notes.\nDogs bark.", "Dogs", "dog"),        # newline also starts a line
])
def test_sentence_initial_plural_folds(text, word, expected):
    """Plurals open sentences constantly. The capital gets them tagged NNP, and
    proper nouns are never plural-stripped — so without position awareness
    "Tools are useful." never folds onto "tool"."""
    assert _tag_lemmas(text)[word] == expected


@pytest.mark.parametrize("text", [
    "A dog barks. Dogs bark.",
    "One. Two! Three?",
    "- a\n- b",
    "no punctuation here",
    "",
])
def test_sentence_split_does_not_change_the_token_stream(text):
    """tag() splits into sentences to find each one's first word. The split is
    on whitespace, which the tokenizer splits on anyway — so the tokens it
    yields must be identical, or node ids would move."""
    from src.postagger import _word_tokenize
    assert [t.text for t in RulePOSTagger().tag(text)] == _word_tokenize(text)


@pytest.mark.parametrize("text,word", [
    ("I visited Athens today.", "Athens"),
    ("We use Windows here.", "Windows"),
])
def test_mid_sentence_proper_noun_keeps_its_s(text, word):
    """Away from a sentence start the capital is real evidence: "Athens" must
    not become "Athen", nor "Windows" collapse onto "window"."""
    assert _tag_lemmas(text)[word] == word.lower()


def test_pos_and_node_identity_are_unaffected():
    """The position rule adjusts the lemma only. POS feeds
    ``canonical_id(text, pos)``, so changing it would move every node id."""
    toks = {t.text: t.pos for t in RulePOSTagger().tag("Tools are useful.")}
    assert toks["Tools"] == "NNP"


# --------------------------------------------- markdown is not a word

@pytest.mark.parametrize("text,expected", [
    ("See [[Other Note]] here", ["See", "Other", "Note", "here"]),
    ("Use **bold** and `code`", ["Use", "bold", "and", "code"]),
    ("A #tag and |cell|", ["A", "tag", "and", "cell"]),
    ("keep snake_case intact", ["keep", "snake_case", "intact"]),
])
def test_markdown_syntax_is_stripped_from_tokens(text, expected):
    """Wikilink and emphasis syntax would otherwise become concepts of their
    own: "[[Other Note]]" yields "[[Other"/"Note]]", and "**bold**" a node
    distinct from "bold". Only the token's ends are stripped, so identifiers
    with inner underscores survive."""
    from src.postagger import _word_tokenize
    assert _word_tokenize(text) == expected
