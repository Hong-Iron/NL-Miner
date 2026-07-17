"""Cross-note concept linking — words that bridge notes.

``top_canonical`` could never serve this: it ranks ``kind='canonical'`` nodes,
which exist only for byte-identical duplicate *blocks*, so it is empty on every
corpus whose blocks differ (i.e. all of them). The concepts that actually recur
live in the token layer, where ``canonical_id(text, pos)`` collapses each
``(word, pos)`` onto one node shared by every block that uses it.

For Korean this is only possible because 조사 are split off first — 문어는 and
문어를 are the same concept, and a plain tokenizer would file them apart.
"""
from pathlib import Path

import pytest

from src.context import process
from src.schema import FileMeta
from src.store import GraphStore


def _index(store: GraphStore, path: Path) -> None:
    fm = FileMeta(file_path=path, file_name=path.name)
    ctx, _ = process(fm, pos=True)
    store.upsert_file(ctx)


@pytest.fixture
def ko_vault(tmp_path):
    (tmp_path / "a.md").write_text(
        "# 해양 생물\n문어는 바다에서 가장 흥미로운 생물이다.\n", encoding="utf-8"
    )
    (tmp_path / "b.md").write_text(
        "# 두족류\n두족류에는 문어와 오징어가 있다.\n", encoding="utf-8"
    )
    (tmp_path / "c.md").write_text(
        "# 동물의 지능\n지능이 높은 동물로 문어를 꼽는다.\n", encoding="utf-8"
    )
    store = GraphStore(tmp_path / "g.db")
    for name in ("a.md", "b.md", "c.md"):
        _index(store, tmp_path / name)
    yield store
    store.close()


def test_korean_concept_bridges_notes_despite_different_particles(ko_vault):
    """문어는 / 문어와 / 문어를 — three spellings, one concept, three notes."""
    concepts = dict((lab, nf) for _, lab, nf, _ in ko_vault.top_concepts())
    assert "문어" in concepts, f"문어 must bridge notes; got {list(concepts)}"
    assert concepts["문어"] == 3


def test_function_words_are_not_concepts(ko_vault):
    labels = [lab for _, lab, _, _ in ko_vault.top_concepts()]
    for noise in ("는", "이", "의", "를", ".", "1"):
        assert noise not in labels, f"{noise!r} is not a concept"


def test_min_files_keeps_only_bridging_words(ko_vault):
    """A word confined to one note links nothing and must be excluded."""
    for _, label, n_files, _ in ko_vault.top_concepts(min_files=2):
        assert n_files >= 2
    # 오징어 appears in b.md only
    assert "오징어" not in [lab for _, lab, _, _ in ko_vault.top_concepts(min_files=2)]
    assert "오징어" in [lab for _, lab, _, _ in ko_vault.top_concepts(min_files=1)]


def test_ranked_by_reach_across_notes(ko_vault):
    rows = ko_vault.top_concepts(min_files=1)
    counts = [nf for _, _, nf, _ in rows]
    assert counts == sorted(counts, reverse=True), "most-shared concept first"


def test_english_concepts(tmp_path):
    (tmp_path / "x.md").write_text(
        "# Octopus\nThe octopus is an intelligent animal.\n", encoding="utf-8"
    )
    (tmp_path / "y.md").write_text(
        "# Camouflage\nAn octopus can change colour to hide.\n", encoding="utf-8"
    )
    store = GraphStore(tmp_path / "g.db")
    try:
        _index(store, tmp_path / "x.md")
        _index(store, tmp_path / "y.md")
        labels = [lab for _, lab, _, _ in store.top_concepts()]
        assert "octopus" in [l.lower() for l in labels]
        # determiners bridge both notes but carry no meaning
        assert "the" not in [l.lower() for l in labels]
        assert "an" not in [l.lower() for l in labels]
    finally:
        store.close()
