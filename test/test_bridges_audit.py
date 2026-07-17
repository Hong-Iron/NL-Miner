"""Concept bridges (use case 1) and the vault audit (use case 3).

Both stand on cross-file link resolution, which did not exist: ``linker`` sees
one file at a time, so its ``ref-to`` rule only ever matched ``[[target]]``
against headings *inside the same file* — meaning a vault's actual
``[[Note]]`` links produced no edges at all. Resolution therefore lives in the
store, the only layer that knows what other notes exist.

The point of a bridge is the mention Obsidian's own feature misses: it matches
literal strings, so 문어는 / 문어를 / 문어와 never line up. Matching canonical
token nodes (after 조사 stripping) does.
"""
import pytest

from src.context import process
from src.schema import FileMeta
from src.store import GraphStore


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def _index(store, path):
    fm = FileMeta(file_path=path, file_name=path.name)
    ctx, _ = process(fm, pos=True)
    store.upsert_file(ctx)


@pytest.fixture
def vault(tmp_path):
    """해양생물 --[[두족류]]--> 두족류.  지능/까마귀/쇼핑 are unlinked."""
    _write(tmp_path, "해양생물.md",
           "# 해양생물\n문어는 바다에서 가장 흥미로운 생물이다. [[두족류]] 참고.\n")
    _write(tmp_path, "두족류.md",
           "# 두족류\n두족류에는 문어와 오징어가 있다. 문어의 지능은 높다.\n")
    _write(tmp_path, "지능.md",
           "# 동물의 지능\n지능이 높은 동물로 문어를 꼽는다. 까마귀의 지능도 주목받는다.\n")
    _write(tmp_path, "까마귀.md",
           "# 까마귀\n까마귀는 도구를 쓴다. 까마귀의 지능은 놀랍다.\n")
    _write(tmp_path, "쇼핑.md", "# 쇼핑 목록\n우유와 계란을 사야 한다.\n")
    store = GraphStore(tmp_path / "g.db")
    for n in ("해양생물.md", "두족류.md", "지능.md", "까마귀.md", "쇼핑.md"):
        _index(store, tmp_path / n)
    yield store
    store.close()


def _uuid(store, name):
    u = store.file_uuid_for(name)
    assert u, f"{name} not indexed"
    return u


def _bridges(store, name, **kw):
    """Bridges with the relatedness bar off.

    These tests are about *matching* — does 문어를 find 문어와, is an already
    linked note excluded — not about whether a pair clears MIN_SCORE. A 5-note
    fixture scores far below it by construction: the document-frequency filter
    needs MIN_FILES_FOR_DF notes to strip filler, so here the filler stays in
    both vectors and dilutes every cosine (real pairs land near 0.1). The bar is
    calibrated, and tested, on the 13-note fixture in test_concept_relevance.py.
    """
    kw.setdefault("min_score", 0.0)
    kw.setdefault("min_shared", 1)
    return store.bridges(_uuid(store, name), **kw)


# ------------------------------------------------------- link resolution


def test_wikilinks_resolve_across_files(vault):
    links = vault.file_links()
    src = _uuid(vault, "해양생물.md")
    assert _uuid(vault, "두족류.md") in links[src]


@pytest.mark.parametrize("raw,expected", [
    ("두족류", "두족류"),
    ("두족류|머리발동물", "두족류"),      # alias
    ("두족류#특징", "두족류"),            # heading anchor
    ("두족류#^abc123", "두족류"),         # block anchor
    ("생물/두족류", "두족류"),            # path
    ("  두족류  ", "두족류"),
    ("#같은문서제목", None),              # in-page anchor targets no note
])
def test_link_target_normalisation(raw, expected):
    assert GraphStore._link_target(raw) == expected


def test_unresolvable_link_is_ignored(tmp_path):
    _write(tmp_path, "a.md", "# A\n[[없는노트]] 를 가리킨다. 문어 이야기.\n")
    store = GraphStore(tmp_path / "g.db")
    try:
        _index(store, tmp_path / "a.md")
        links = store.file_links()
        assert links[_uuid(store, "a.md")] == set()
    finally:
        store.close()


# ------------------------------------------------- use case 1: bridges


def test_bridge_finds_unlinked_notes_sharing_concepts(vault):
    rows = _bridges(vault, "지능.md")
    by_name = {r["name"]: r for r in rows}
    assert "까마귀.md" in by_name
    assert set(by_name["까마귀.md"]["shared"]) == {"까마귀", "지능"}


def test_bridge_matches_across_particle_forms(vault):
    """지능.md says 문어를, 두족류.md says 문어와 — one concept, and Obsidian's
    string-matching unlinked-mentions would find neither."""
    shared = {r["name"]: r["shared"] for r in _bridges(vault, "지능.md")}
    assert "문어" in shared["두족류.md"]
    assert "문어" in shared["해양생물.md"]


def test_bridge_excludes_already_linked_notes(vault):
    """해양생물 already links to 두족류, so it must not be suggested again —
    even though the two share 문어."""
    rows = _bridges(vault, "해양생물.md")
    assert "두족류.md" not in [r["name"] for r in rows]


def test_bridge_excludes_unrelated_notes(vault):
    rows = _bridges(vault, "지능.md")
    assert "쇼핑.md" not in [r["name"] for r in rows]


def test_bridge_ranks_by_relatedness(vault):
    """Rank is by score, not overlap size: one rare word beats several common."""
    scores = [r["score"] for r in _bridges(vault, "지능.md")]
    assert scores == sorted(scores, reverse=True)


def test_bridge_never_suggests_itself(vault):
    rows = _bridges(vault, "지능.md")
    assert "지능.md" not in [r["name"] for r in rows]


def test_bridge_min_shared_filters_weak_suggestions(vault):
    strong = _bridges(vault, "지능.md", min_shared=2)
    for r in strong:
        assert r["n_shared"] >= 2
    # 해양생물 shares only 문어 with 지능 → dropped at min_shared=2
    assert "해양생물.md" not in [r["name"] for r in strong]
    assert "해양생물.md" in [r["name"] for r in _bridges(vault, "지능.md", min_shared=1)]


# ------------------------------------------------------- concept folding


def test_english_concept_matches_across_sentence_case(tmp_path):
    """"It uses camouflage." vs "Camouflage helps." — the tagger calls the
    capitalised one NNP and the other NN, so they are two canonical nodes. As
    a *concept* they are one, else a bridge between two notes about camouflage
    never appears."""
    _write(tmp_path, "octopus.md",
           "# Octopus\nThe octopus is intelligent. It uses camouflage.\n")
    _write(tmp_path, "camouflage.md",
           "# Camouflage\nCamouflage helps survival. An octopus hides.\n")
    store = GraphStore(tmp_path / "g.db")
    try:
        for n in ("octopus.md", "camouflage.md"):
            _index(store, tmp_path / n)
        rows = store.bridges(_uuid(store, "octopus.md"))
        shared = {s.lower() for r in rows for s in r["shared"]}
        assert "camouflage" in shared
        assert "octopus" in shared
    finally:
        store.close()


def test_case_variants_are_one_row_in_top_concepts(tmp_path):
    _write(tmp_path, "a.md", "# A\nCamouflage is useful.\n")
    _write(tmp_path, "b.md", "# B\nAn octopus uses camouflage.\n")
    store = GraphStore(tmp_path / "g.db")
    try:
        for n in ("a.md", "b.md"):
            _index(store, tmp_path / n)
        rows = [r for r in store.top_concepts() if r[0] == "camouflage"]
        assert len(rows) == 1, "Camouflage/camouflage must not split into two rows"
        assert rows[0][2] == 2, "and the fold must count both notes"
    finally:
        store.close()


# -------------------------------------------------- use case 3: audit


def test_audit_lists_orphans(vault):
    names = {o["name"] for o in vault.audit(min_score=0.0)["orphans"]}
    assert names == {"지능.md", "까마귀.md", "쇼핑.md"}
    # both ends of the one link are connected
    assert "해양생물.md" not in names and "두족류.md" not in names


def test_audit_counts_links(vault):
    assert vault.audit(min_score=0.0)["n_links"] == 1
    assert vault.audit(min_score=0.0)["n_files"] == 5


def test_audit_pairs_are_unlinked_and_symmetric(vault):
    pairs = vault.audit(min_shared=2, min_score=0.0)["unlinked_pairs"]
    keys = {frozenset((p["a"], p["b"])) for p in pairs}
    assert frozenset(("지능.md", "까마귀.md")) in keys
    # already linked → never a suggestion
    assert frozenset(("해양생물.md", "두족류.md")) not in keys
    # each pair appears once, not once per direction
    assert len(keys) == len(pairs)


def test_audit_min_shared_threshold(vault):
    assert all(p["n_shared"] >= 2 for p in vault.audit(min_shared=2, min_score=0.0)["unlinked_pairs"])
    assert all(p["n_shared"] >= 1 for p in vault.audit(min_shared=1, min_score=0.0)["unlinked_pairs"])


def test_audit_ranks_strongest_pair_first(vault):
    pairs = vault.audit(min_shared=1, min_score=0.0)["unlinked_pairs"]
    scores = [p["score"] for p in pairs]
    assert scores == sorted(scores, reverse=True)


# ------------------------------------------- the "indexed without --pos" trap


def test_has_concepts_distinguishes_missing_token_layer(tmp_path):
    """Without --pos there are no token nodes, so every concept query is empty.
    That must be reportable as "layer missing", not "nothing shared"."""
    _write(tmp_path, "a.md", "# A\n문어는 똑똑하다.\n")
    _write(tmp_path, "b.md", "# B\n문어를 보았다.\n")
    store = GraphStore(tmp_path / "g.db")
    try:
        for n in ("a.md", "b.md"):
            fm = FileMeta(file_path=tmp_path / n, file_name=n)
            ctx, _ = process(fm)          # pos=False → no token layer
            store.upsert_file(ctx)
        assert store.has_concepts() is False
        assert store.bridges(_uuid(store, "a.md"), min_score=0.0) == []
    finally:
        store.close()


def test_has_concepts_true_once_indexed_with_pos(vault):
    assert vault.has_concepts() is True
