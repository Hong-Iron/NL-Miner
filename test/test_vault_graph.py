"""Scoped, hierarchical vault graph.

Obsidian's graph is a flat force-directed blob of every note: it throws away the
folder tree the user already built, and shows only links that exist. This view
keeps the hierarchy, can be narrowed to a folder subtree or to the notes that
discuss one concept, and separates real links from *suggested* ones — a
suggestion must never be drawn as though it were a link.
"""
import pytest

from src.context import process
from src.schema import FileMeta
from src.store import GraphStore

VAULT = {
    "생물/두족류/문어.md": "# 문어\n문어는 바다에서 흥미로운 생물이다. [[두족류 개요]] 참고.\n",
    "생물/두족류/오징어.md": "# 오징어\n오징어는 문어와 비슷하다. 오징어의 지능도 연구된다.\n",
    "생물/두족류/두족류 개요.md": "# 두족류 개요\n두족류에는 문어와 오징어가 있다.\n",
    "생물/조류/까마귀.md": "# 까마귀\n까마귀는 도구를 쓴다. 까마귀의 지능은 놀랍다.\n",
    "생물/지능.md": "# 동물 지능\n지능이 높은 동물로 문어와 까마귀를 꼽는다.\n",
    "일상/쇼핑.md": "# 쇼핑\n우유와 계란을 사야 한다.\n",
}


@pytest.fixture
def vault(tmp_path):
    for rel, text in VAULT.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    store = GraphStore(tmp_path / "g.db")
    for rel in VAULT:
        p = tmp_path / rel
        ctx, _ = process(FileMeta(file_path=p, file_name=p.name), pos=True)
        store.upsert_file(ctx)
    yield store, tmp_path
    store.close()


def _names(g):
    return {n["name"] for n in g["notes"]}


# ----------------------------------------------------------- hierarchy


def test_folder_hierarchy_is_preserved(vault):
    store, root = vault
    g = store.vault_graph(root=str(root))
    folders = {n["name"]: n["folder"] for n in g["notes"]}
    assert folders["문어.md"] == "생물/두족류"
    assert folders["지능.md"] == "생물"
    assert folders["쇼핑.md"] == "일상"
    assert "생물/두족류" in g["folders"] and "생물/조류" in g["folders"]


def test_root_defaults_to_common_ancestor(vault):
    """Without --root the folders must still be readable, not absolute paths."""
    store, root = vault
    g = store.vault_graph()
    assert all(not n["folder"].startswith("/") for n in g["notes"])
    assert any(n["folder"] == "생물/두족류" for n in g["notes"])


# --------------------------------------------------------------- scope


def test_folder_scope_restricts_to_the_subtree(vault):
    store, root = vault
    g = store.vault_graph(root=str(root), folder="생물/두족류")
    assert _names(g) == {"문어.md", "오징어.md", "두족류 개요.md"}
    assert g["scope"] == {"kind": "folder", "value": "생물/두족류"}


def test_folder_scope_includes_nested_subfolders(vault):
    store, root = vault
    g = store.vault_graph(root=str(root), folder="생물")
    assert "까마귀.md" in _names(g)      # 생물/조류/
    assert "문어.md" in _names(g)        # 생물/두족류/
    assert "쇼핑.md" not in _names(g)    # 일상/


def test_concept_scope_selects_notes_that_discuss_it(vault):
    store, root = vault
    g = store.vault_graph(root=str(root), concept="지능")
    assert _names(g) == {"지능.md", "오징어.md", "까마귀.md"}
    assert "쇼핑.md" not in _names(g)
    assert g["scope"] == {"kind": "concept", "value": "지능"}


def test_concept_scope_matches_across_particle_forms(vault):
    """지능이 / 지능도 / 지능은 — one concept."""
    store, root = vault
    assert _names(store.vault_graph(concept="지능")) == {
        "지능.md", "오징어.md", "까마귀.md"
    }


def test_unknown_concept_scope_is_empty_not_everything(vault):
    store, root = vault
    g = store.vault_graph(concept="없는개념")
    assert g["notes"] == [] and g["links"] == [] and g["bridges"] == []


# --------------------------------------------------------------- edges


def test_real_links_are_reported(vault):
    store, root = vault
    g = store.vault_graph(root=str(root))
    by_id = {n["file_uuid"]: n["name"] for n in g["notes"]}
    pairs = {(by_id[l["src"]], by_id[l["dst"]]) for l in g["links"]}
    assert ("문어.md", "두족류 개요.md") in pairs


def test_links_and_bridges_are_disjoint(vault):
    """A suggestion must never duplicate a real link — drawing it would claim a
    connection the vault does not have."""
    store, root = vault
    g = store.vault_graph(root=str(root))
    linked = {frozenset((l["src"], l["dst"])) for l in g["links"]}
    suggested = {frozenset((b["a"], b["b"])) for b in g["bridges"]}
    assert linked.isdisjoint(suggested)


def test_edges_stay_inside_the_scope(vault):
    """An edge to a note outside the scope has no node to attach to."""
    store, root = vault
    g = store.vault_graph(root=str(root), folder="생물/두족류")
    ids = {n["file_uuid"] for n in g["notes"]}
    for l in g["links"]:
        assert l["src"] in ids and l["dst"] in ids
    for b in g["bridges"]:
        assert b["a"] in ids and b["b"] in ids


def test_bridges_ranked_by_shared_count(vault):
    store, root = vault
    counts = [b["n_shared"] for b in store.vault_graph(root=str(root))["bridges"]]
    assert counts == sorted(counts, reverse=True)


def test_notes_carry_their_concepts(vault):
    store, root = vault
    g = store.vault_graph(root=str(root))
    munueo = next(n for n in g["notes"] if n["name"] == "문어.md")
    assert "문어" in munueo["concepts"]


# ------------------------------------------- markdown must not be a concept


def test_wikilink_syntax_is_not_a_concept(vault):
    """"[[두족류 개요]]" must tokenise to 두족류/개요 — the brackets would
    otherwise ride along as the concept "[[두족류"."""
    store, root = vault
    g = store.vault_graph(root=str(root))
    for n in g["notes"]:
        for c in n["concepts"]:
            assert "[" not in c and "]" not in c, f"markup leaked: {c!r}"
    labels = [r[0] for r in store.conn.execute(
        "SELECT label FROM nodes WHERE kind='token'")]
    assert not any("[" in l for l in labels)
