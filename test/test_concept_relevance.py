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
from src.store import MAX_DF_RATIO, MIN_FILES_FOR_DF, MIN_FILES_FOR_IDF, GraphStore

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


# ----------------------------------- one-note concepts and the denominator


@pytest.mark.parametrize("n,expected", [
    (2, math.inf),                 # uniform weights: exclude nothing
    (MIN_FILES_FOR_IDF, math.log(MIN_FILES_FOR_IDF)),
    (100, math.log(100)),
])
def test_idf_ceiling_is_the_one_note_idf(n, expected):
    """idf < ln(n) is exactly df >= 2."""
    assert GraphStore._idf_ceiling(n) == pytest.approx(expected)


def test_one_off_vocabulary_does_not_dilute_relatedness(tmp_path):
    """A concept only one note uses can never be shared, so it must not weigh
    on that note's score either. Here octopus-long says exactly what
    octopus-short says plus a pile of words no other note has. Without the
    ceiling that pile shrinks the cosine; with it the two notes are the same
    vector and score 1."""
    filler = " ".join(f"zorb{chr(97 + i)}{chr(97 + j)}" for i in range(6) for j in range(6))
    files = dict(MIXED)
    files["bio-octopus-short.md"] = (
        "# Octopus\nThe octopus is a cephalopod with camouflage and hemocyanin.\n")
    files["bio-octopus-long.md"] = (
        "# Octopus\nThe octopus is a cephalopod with camouflage and hemocyanin. "
        + filler + "\n")
    store = _build(tmp_path, files)
    try:
        rows = store.bridges(store.file_uuid_for("bio-octopus-short.md"))
        score = {r["name"]: r["score"] for r in rows}.get("bio-octopus-long.md")
        assert score == pytest.approx(1.0)
    finally:
        store.close()


# Two notes on amplitude modulation written the way a Korean vault is: the same
# nouns under different particles, copulas and verb endings. Before verb forms
# were recognised, each 어절 like "실어서" or "바뀐다" was a concept of its own,
# and they swamped the shared nouns.
KOREAN = {
    "am-signals.md": "# 진폭 변조\n진폭 변조는 반송파의 진폭을 신호에 맞춰 바꾸는 방법이다. 반송파를 곱하면 스펙트럼이 반송파 주파수만큼 옮겨진다. 라디오는 목소리를 반송파에 실어서 보낸다.\n",
    "am-network.md": "# 변조 방식\n진폭 변조에서는 반송파의 진폭이 데이터에 따라 바뀐다. 반송파 주파수는 그대로이고, 라디오 방송이 대표적인 예이다. 변조된 신호는 대역폭을 차지한다.\n",
    "sort-merge.md": "# 병합 정렬\n병합 정렬은 배열을 반으로 나누고, 각각 정렬한 뒤 합친다. 합칠 때는 두 배열의 맨 앞 원소를 비교한다.\n",
    "sort-quick.md": "# 퀵 정렬\n퀵 정렬은 기준 원소를 골라 배열을 둘로 나눈다. 기준보다 작은 원소는 왼쪽으로, 큰 원소는 오른쪽으로 보낸다.\n",
    "graph-bfs.md": "# 너비 우선 탐색\n너비 우선 탐색은 시작 정점에서 가까운 정점부터 차례로 방문한다. 큐에 정점을 넣고 하나씩 꺼낸다.\n",
}


@pytest.fixture
def korean(tmp_path):
    store = _build(tmp_path, KOREAN)
    yield store
    store.close()


def test_korean_notes_on_one_topic_bridge(korean):
    rows = korean.bridges(korean.file_uuid_for("am-signals.md"))
    assert "am-network.md" in [r["name"] for r in rows]
    shared = next(r["shared"] for r in rows if r["name"] == "am-network.md")
    assert {"반송파", "진폭"} <= set(shared)


def test_korean_bridges_never_cross_topics(korean):
    offenders = []
    for name, _ in korean.list_files():
        for r in korean.bridges(korean.file_uuid_for(name)):
            if r["name"].split("-")[0] != name.split("-")[0]:
                offenders.append(f"{name} → {r['name']} ({r['shared']})")
    assert offenders == [], "\n".join(offenders)


def test_korean_shared_reasons_are_nouns(korean):
    """What a suggestion shows as its reason must be a word, not an inflection."""
    from src.adapters_ko import looks_conjugated
    for name, _ in korean.list_files():
        for r in korean.bridges(korean.file_uuid_for(name)):
            assert not any(looks_conjugated(w) for w in r["shared"]), r["shared"]
