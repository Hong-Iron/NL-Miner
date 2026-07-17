"""``bridge`` / ``audit`` CLI contract — the shapes the plugin consumes.

Pins the JSON the Obsidian plugin's BridgeJson/AuditJson mirror, plus the
exit-code behaviour for the store-has-no-token-layer case (which would
otherwise be reported as an empty, and therefore misleading, result).
"""
import json

import pytest

from interface.cli import main


def _run(argv, capsys):
    rc = main(argv)
    out = capsys.readouterr()
    return rc, out.out, out.err


@pytest.fixture
def vault(tmp_path):
    (tmp_path / "해양생물.md").write_text(
        "# 해양생물\n문어는 바다에서 흥미로운 생물이다. [[두족류]] 참고.\n",
        encoding="utf-8")
    (tmp_path / "두족류.md").write_text(
        "# 두족류\n두족류에는 문어와 오징어가 있다. 문어의 지능은 높다.\n",
        encoding="utf-8")
    (tmp_path / "지능.md").write_text(
        "# 지능\n지능이 높은 동물로 문어를 꼽는다. 까마귀의 지능도 높다.\n",
        encoding="utf-8")
    db = tmp_path / "g.db"
    return tmp_path, db


def _index(vault, capsys, *, pos=True):
    root, db = vault
    argv = ["--db", str(db), "index", str(root)]
    if pos:
        argv.append("--pos")
    rc, *_ = _run(argv, capsys)
    assert rc == 0
    return db


# ------------------------------------------------------------- bridge


def test_bridge_json_shape(vault, capsys):
    db = _index(vault, capsys)
    # --min-score 0: a 3-note fixture cannot clear the default bar (see
    # store.MIN_SCORE); this test is about the JSON shape, not the threshold.
    rc, out, err = _run(
        ["--db", str(db), "bridge", "지능.md", "--json", "--min-score", "0"], capsys)
    assert rc == 0
    d = json.loads(out)
    assert d["doc"] == "지능.md"
    assert isinstance(d["bridges"], list) and d["bridges"]
    for b in d["bridges"]:
        assert set(b) >= {"file_uuid", "name", "shared", "n_shared", "score"}
        assert b["n_shared"] == len(b["shared"])
        assert 0.0 <= b["score"] <= 1.0
    assert "두족류.md" in [b["name"] for b in d["bridges"]]


def test_bridge_text_output_is_not_json(vault, capsys):
    db = _index(vault, capsys)
    rc, out, err = _run(
        ["--db", str(db), "bridge", "지능.md", "--min-score", "0"], capsys)
    assert rc == 0
    # the text view shows the link to *write*, not the file name to derive one
    # from — deriving it is where the phantom-note bug came from
    assert "[[두족류]]" in out
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)


def test_bridge_unknown_doc_errors(vault, capsys):
    db = _index(vault, capsys)
    rc, out, err = _run(["--db", str(db), "bridge", "없는노트.md", "--json"], capsys)
    assert rc == 1
    assert "not indexed" in err


def test_bridge_without_token_layer_errors_rather_than_lying(vault, capsys):
    db = _index(vault, capsys, pos=False)
    rc, out, err = _run(["--db", str(db), "bridge", "지능.md", "--json"], capsys)
    assert rc == 1
    assert "--pos" in err, "must say how to fix it, not return an empty list"


# -------------------------------------------------------------- audit


def test_audit_json_shape(vault, capsys):
    db = _index(vault, capsys)
    rc, out, err = _run(["--db", str(db), "audit", "--json"], capsys)
    assert rc == 0
    d = json.loads(out)
    assert set(d) >= {"n_files", "n_links", "orphans", "unlinked_pairs"}
    assert d["n_files"] == 3
    assert d["n_links"] == 1
    assert "지능.md" in [o["name"] for o in d["orphans"]]
    for p in d["unlinked_pairs"]:
        assert set(p) >= {"a", "b", "shared", "n_shared"}


def test_audit_text_output_is_not_json(vault, capsys):
    db = _index(vault, capsys)
    rc, out, err = _run(["--db", str(db), "audit"], capsys)
    assert rc == 0
    assert "orphans" in out
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)


def test_audit_without_token_layer_errors(vault, capsys):
    db = _index(vault, capsys, pos=False)
    rc, out, err = _run(["--db", str(db), "audit", "--json"], capsys)
    assert rc == 1
    assert "--pos" in err


# -------------------------------------------------------------- vault


def test_vault_json_shape(vault, capsys):
    db = _index(vault, capsys)
    rc, out, err = _run(["--db", str(db), "vault", "--json"], capsys)
    assert rc == 0
    d = json.loads(out)
    assert set(d) >= {"scope", "root", "folders", "notes", "links", "bridges"}
    assert d["scope"]["kind"] == "vault"
    for n in d["notes"]:
        assert set(n) >= {"file_uuid", "name", "path", "folder", "n_blocks",
                          "concepts"}


def test_vault_concept_scope(vault, capsys):
    db = _index(vault, capsys)
    rc, out, err = _run(
        ["--db", str(db), "vault", "--concept", "지능", "--json"], capsys)
    assert rc == 0
    d = json.loads(out)
    assert d["scope"] == {"kind": "concept", "value": "지능"}
    names = {n["name"] for n in d["notes"]}
    assert "지능.md" in names and "두족류.md" in names
    assert "해양생물.md" not in names


def test_vault_without_token_layer_errors(vault, capsys):
    db = _index(vault, capsys, pos=False)
    rc, out, err = _run(["--db", str(db), "vault", "--json"], capsys)
    assert rc == 1
    assert "--pos" in err


# ------------------------------------------------- incremental indexing


def test_reindex_skips_unchanged_files(vault, capsys):
    """`index` compares mtime against the store and reparses only what changed.

    This is what makes "index before every query" affordable, and that in turn is
    what stops an agent answering from a stale database — the failure this
    guards is `search` returning count 0 for text sitting in the vault.
    """
    db = _index(vault, capsys)
    rc, out, err = _run(
        ["--db", str(db), "index", str(vault[0]), "--pos", "--json"], capsys)
    assert rc == 0
    d = json.loads(out)
    assert d["files"] == [], "nothing changed — nothing should be reparsed"
    assert d["totals"]["skipped_unchanged"] == 3


def test_edited_file_is_picked_up(vault, capsys):
    root, _ = vault
    db = _index(vault, capsys)
    (root / "지능.md").write_text(
        "# 지능\n지능이 높은 동물. 프로메테우스로 관측한다.\n", encoding="utf-8")
    rc, out, err = _run(
        ["--db", str(db), "index", str(root), "--pos", "--json"], capsys)
    assert rc == 0
    d = json.loads(out)
    assert [f["name"] for f in d["files"]] == ["지능.md"]
    assert d["totals"]["skipped_unchanged"] == 2
    rc, out, err = _run(["--db", str(db), "search", "프로메테우스", "--json"], capsys)
    assert json.loads(out)["count"] == 1, "an edit must be visible to the next query"


def test_force_reprocesses_everything(vault, capsys):
    """mtime says a file is unchanged, but the *tokenizer* can change under it —
    after upgrading, only --force rebuilds those rows."""
    db = _index(vault, capsys)
    rc, out, err = _run(
        ["--db", str(db), "index", str(vault[0]), "--pos", "--force", "--json"], capsys)
    assert rc == 0
    d = json.loads(out)
    assert len(d["files"]) == 3
    assert d["totals"]["skipped_unchanged"] == 0
