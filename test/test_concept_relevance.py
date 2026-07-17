"""Sharing a word is not evidence — it depends entirely on *which* word.

The first version suggested a link whenever two notes shared any content word.
On this repo's own `docs/` that produced 296 "shared concepts" between two pages,
justified by `--json`, `--db`, `-m` and `$DEST`; in a mixed vault it linked an
octopus note to a git note because both said "use". That is not a threshold to
tune, it is the wrong signal.

Three things fix it, and all three are needed:

* **code blocks are not prose** — their tokens are not what a note is *about*;
* **a concept must be a word** — not a flag, path, or shell variable;
* **document frequency decides informativeness** — a stoplist cannot, because
  the uninformative words are not a fixed list. They are whatever *this* vault
  repeats. "plugin" is noise in a plugin repo and signal anywhere else.
"""
import math

import pytest

from src.context import process
from src.schema import FileMeta
from src.store import MAX_DF_RATIO, MIN_FILES_FOR_DF, GraphStore

# Three unrelated topics. Across topics the notes share only ordinary words
# ("use", "well", "often", "work") — precisely the overlap that must NOT link.
MIXED = {
    "bio-octopus.md": "# Octopus\nThe octopus is a cephalopod that lives in the ocean. It uses camouflage to hide from predators. Researchers note that the octopus can solve puzzles.\n",
    "bio-squid.md": "# Squid\nThe squid is also a cephalopod. It swims faster than the octopus and lives in the deep ocean. Researchers study how the squid hunts.\n",
    "bio-camouflage.md": "# Camouflage\nCamouflage lets an animal hide from predators. The octopus changes colour. Many animals in the ocean use camouflage.\n",
    "bio-intelligence.md": "# Animal intelligence\nResearchers note that some animals solve puzzles. The octopus and the crow both use tools. Intelligence is hard to measure.\n",
    "bio-crow.md": "# Crow\nThe crow is a clever bird. It uses tools and can solve puzzles. Researchers study crow intelligence.\n",
    "cook-pasta.md": "# Pasta\nBoil water in a large pot. Add salt and the pasta. Cook it for ten minutes and drain it well.\n",
    "cook-bread.md": "# Bread\nMix flour, water and yeast. Knead the dough well and let it rise. Bake it for thirty minutes.\n",
    "cook-soup.md": "# Soup\nChop the onion and the carrot. Add water and boil it for twenty minutes. Add salt to taste.\n",
    "cook-salad.md": "# Salad\nWash the lettuce well. Chop the tomato and the onion. Add oil and salt and mix it.\n",
    "dev-git.md": "# Git\nCommit your work often. Use a branch for each change and merge it when the work is done.\n",
    "dev-testing.md": "# Testing\nWrite a test for each change. Run the tests often so a change that breaks the work is caught.\n",
    "dev-review.md": "# Code review\nRead the change carefully. Ask why the work is done this way. A review catches what a test cannot.\n",
    "misc-notes.md": "# Notes\nI use this note to keep track of things. It is well worth writing them down often.\n",
}


def _build(tmp_path, files: dict[str, str]) -> GraphStore:
    store = GraphStore(tmp_path / "g.db")
    for name, text in files.items():
        p = tmp_path / name
        p.write_text(text, encoding="utf-8")
        ctx, _ = process(FileMeta(file_path=p, file_name=name), pos=True)
        store.upsert_file(ctx)
    return store


@pytest.fixture
def mixed(tmp_path):
    store = _build(tmp_path, MIXED)
    yield store
    store.close()


def _topic(name: str) -> str:
    return name.split("-")[0]


# ------------------------------------------------------- the actual goal


def test_bridges_never_cross_unrelated_topics(mixed):
    """The complaint, as a test: an octopus note must not be linked to a git
    note because both happen to say "use"."""
    offenders = []
    for name, _ in mixed.list_files():
        for r in mixed.bridges(mixed.file_uuid_for(name)):
            if _topic(r["name"]) != _topic(name):
                offenders.append(f"{name} → {r['name']} ({r['shared']})")
    assert offenders == [], "suggested links across unrelated topics:\n" + "\n".join(offenders)


def test_bridges_still_find_related_notes(mixed):
    """Precision must not be bought by suggesting nothing."""
    rows = mixed.bridges(mixed.file_uuid_for("bio-octopus.md"))
    assert {r["name"] for r in rows} >= {"bio-squid.md", "bio-camouflage.md"}


def test_a_note_about_nothing_suggests_nothing(mixed):
    """misc-notes.md is generic prose sharing only common words with everything.
    Its correct bridge list is empty."""
    assert mixed.bridges(mixed.file_uuid_for("misc-notes.md")) == []


def test_ranked_by_informativeness_not_overlap_count(mixed):
    rows = mixed.bridges(mixed.file_uuid_for("bio-octopus.md"))
    scores = [r["score"] for r in rows]
    assert scores == sorted(scores, reverse=True)
    # the rarest shared word is the reason shown first
    top = rows[0]
    assert top["shared"], "a suggestion must say what it is based on"


# --------------------------------------------------- why it works: idf


def test_common_words_score_zero_and_drop_out(mixed):
    by = mixed._concepts_by_file()
    idf = mixed._concept_idf(by)
    floor = mixed._idf_floor(len(by))
    # "use" is spread across topics; the topical terms are not
    assert idf["use"] < floor, "vault-wide vocabulary must not count as evidence"
    for topical in ("camouflage", "cephalopod", "ocean"):
        assert idf[topical] > floor


def test_idf_is_zero_for_a_word_in_every_note(tmp_path):
    store = _build(tmp_path, {
        "a.md": "# A\nThe octopus swims.\n",
        "b.md": "# B\nThe octopus hides.\n",
        "c.md": "# C\nThe octopus hunts.\n",
    })
    try:
        idf = store._concept_idf(store._concepts_by_file())
        assert idf["octopus"] == pytest.approx(0.0)
    finally:
        store.close()


@pytest.mark.parametrize("n,expected", [
    (2, -1.0),                              # "shared" and "everywhere" coincide
    (3, 0.0),                               # only drop what's in *every* note
    (MIN_FILES_FOR_DF, -math.log(MAX_DF_RATIO)),   # drop vault-wide vocabulary
])
def test_idf_floor_scales_with_vault_size(n, expected):
    """A ratio needs an "everywhere" to mean anything. Applying the full filter
    to a 2-note vault would leave it with no bridges at all."""
    assert GraphStore._idf_floor(n) == pytest.approx(expected)


def test_small_vault_keeps_its_bridges(tmp_path):
    """Regression for the floor: with 2 notes every shared word has idf 0, so a
    naive filter would delete the only signal there is."""
    store = _build(tmp_path, {
        "a.md": "# A\nThe octopus uses camouflage.\n",
        "b.md": "# B\nThe octopus hides with camouflage.\n",
    })
    try:
        rows = store.bridges(store.file_uuid_for("a.md"))
        assert rows and "b.md" in [r["name"] for r in rows]
    finally:
        store.close()


# ------------------------------------------- what is not a concept at all


def test_code_blocks_do_not_contribute_concepts(tmp_path):
    """A docs vault otherwise links every page to every other on `--json`."""
    store = _build(tmp_path, {
        "a.md": "# A\nRun the indexer.\n\n```sh\npython -m interface.cli --db x index . --json\n```\n",
        "b.md": "# B\nRun the query.\n\n```sh\npython -m interface.cli --db y search foo --json\n```\n",
    })
    try:
        labels = [l for _, l, _, _ in store.top_concepts(limit=999, min_files=1)]
        for junk in ("--json", "--db", "-m", "interface.cli"):
            assert junk not in labels
    finally:
        store.close()


@pytest.mark.parametrize("junk", [
    "--json", "-m", "$DEST", "plugin/main", "venv/bin/python",
    "md](ARCHITECTURE", "a=b", "x@y", "3", "50%",
])
def test_non_words_are_not_concepts(junk):
    assert GraphStore._is_content_word(junk) is False


@pytest.mark.parametrize("word", ["octopus", "camouflage", "문어", "snake_case", "e-mail"])
def test_real_words_are_still_concepts(word):
    assert GraphStore._is_content_word(word) is True
