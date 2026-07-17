"""Phase 3 — SQLite store: upsert idempotency, cross-file canonical, queries."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.context import process
from src.filestructure import discover
from src.schema import BlockKind, FileMeta
from src.store import GraphStore

PROJECT = Path(__file__).resolve().parent.parent
OCTOPUS = PROJECT / "octopus.md"


@pytest.fixture
def store(tmp_path):
    s = GraphStore(tmp_path / "t.db")
    yield s
    s.close()


def _dup_doc(tmp_path) -> Path:
    p = tmp_path / "dup.md"
    p.write_text(
        "# Notes\n\n"
        "The quick brown fox jumps over the lazy dog under the warm sun.\n\n"
        "Some unique paragraph that is not duplicated anywhere else.\n\n"
        "The quick brown fox jumps over the lazy dog under the warm sun.\n",
        encoding="utf-8",
    )
    return p


def test_upsert_and_query_octopus(store, tmp_path):
    ctx, _ = process(FileMeta(file_path=OCTOPUS, file_name="octopus.md"))
    store.upsert_file(ctx)
    files = store.list_files()
    assert files[0][0] == "octopus.md"

    fu = store.file_uuid_for("octopus.md")
    assert fu == str(ctx.file.unique_id)
    # Intelligence heading has children via contains
    intel = next(b for b in ctx.blocks if b.kind is BlockKind.HEADING and "Intelligence" in b.text)
    nb = store.neighbors(str(intel.block_id))
    assert any(e[1] == "contains" for e in nb["out"])


def test_idempotent_reindex(store, tmp_path):
    fm = FileMeta(file_path=OCTOPUS, file_name="octopus.md")
    ctx, _ = process(fm)
    store.upsert_file(ctx)
    store.upsert_file(ctx)
    rows = store.conn.execute("SELECT COUNT(*) FROM blocks WHERE file_uuid=?", (str(fm.unique_id),)).fetchone()[0]
    assert rows == len(ctx.blocks), "second index must not duplicate rows"
    assert len(store.list_files()) == 1


def test_canonical_dedup_saves_and_top_canonical(store, tmp_path):
    p = _dup_doc(tmp_path)
    fm = FileMeta(file_path=p, file_name="dup.md")
    ctx, stats = process(fm)
    assert stats["dup_groups"] == 1
    store.upsert_file(ctx)

    top = store.top_canonical(limit=5)
    assert top, "at least one canonical node expected"
    nid, label, n = top[0]
    assert n == 2, "two blocks reference the dup canonical node"


def test_canonical_shared_across_files(store, tmp_path):
    """Phase 3 invariant: the same near-duplicate content produces the same
    content-deterministic canonical id in two files; the store's PK then keeps
    it as ONE row (cross-file shared). Each file must carry the dup twice so
    the Phase 1 linker creates the canonical node in each."""
    dup = "The quick brown fox jumps over the lazy dog under the warm sun."
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    a.write_text(f"# T\n\n{dup}\n\nUnique A.\n\n{dup}\n", encoding="utf-8")
    b.write_text(f"# T\n\n{dup}\n\nUnique B.\n\n{dup}\n", encoding="utf-8")

    ca, _ = process(FileMeta(file_path=a, file_name="a.md"))
    cb, _ = process(FileMeta(file_path=b, file_name="b.md"))
    ca_canon = {n.node_id for n in ca.nodes if n.kind == "canonical"}
    cb_canon = {n.node_id for n in cb.nodes if n.kind == "canonical"}
    assert ca_canon and ca_canon == cb_canon, "identical content → same canonical id"

    store.upsert_file(ca)
    store.upsert_file(cb)
    nrows = store.conn.execute(
        "SELECT COUNT(*) FROM nodes WHERE kind='canonical'"
    ).fetchone()[0]
    assert nrows == 1, "store collapses the same canonical id across files"


def test_remove_file_keeps_other_files(store, tmp_path):
    ca, _ = process(FileMeta(file_path=OCTOPUS, file_name="octopus.md"))
    store.upsert_file(ca)
    assert len(store.list_files()) == 1
    store.remove_file(ca.file.unique_id)
    assert store.list_files() == []


def test_purge_by_name(store, tmp_path):
    ca, _ = process(FileMeta(file_path=OCTOPUS, file_name="octopus.md"))
    store.upsert_file(ca)
    n = store.purge_file("octopus.md")
    assert n == 1
    assert store.list_files() == []


def test_reindex_by_path_is_idempotent_new_uuid(store, tmp_path):
    """Regression: CLI re-index creates a fresh uuid4 FileMeta each run, so
    delete-by-new-uuid wouldn't catch the previous run's rows. The store must
    re-use the file_path to find & drop the old file before inserting the new."""
    p = tmp_path / "doc.md"
    p.write_text("# H\n\nbody text alpha beta gamma.\n", encoding="utf-8")
    from uuid import uuid4
    # two independent runs — same path, DIFFERENT uuids (as the CLI produces)
    ctx1, _ = process(FileMeta(file_path=p, file_name="doc.md", unique_id=uuid4()))
    ctx2, _ = process(FileMeta(file_path=p, file_name="doc.md", unique_id=uuid4()))
    assert ctx1.file.unique_id != ctx2.file.unique_id  # sanity: random ids
    store.upsert_file(ctx1)
    store.upsert_file(ctx2)
    assert len(store.list_files()) == 1, "re-index must not duplicate by path"
    rows = store.conn.execute("SELECT COUNT(*) FROM files WHERE file_path=?", (str(p),)).fetchone()[0]
    assert rows == 1
    # only the latest uuid's blocks remain
    assert store.file_uuid_for("doc.md") == str(ctx2.file.unique_id)


def test_concurrent_upserts_from_threads(store, tmp_path):
    """Regression: upserts from worker threads must not crash with
    'SQLite objects created in a thread can only be used in that same thread'.
    The store uses check_same_thread=False + a serialising lock."""
    import threading
    files = []
    for i in range(10):
        p = tmp_path / f"f{i}.md"
        p.write_text(f"# H{i}\n\nunique body alpha{i} beta{i} gamma{i}.\n", encoding="utf-8")
        files.append(p)
    def up(p):
        ctx, _ = process(FileMeta(file_path=p, file_name=p.name))
        store.upsert_file(ctx)
    ts = [threading.Thread(target=up, args=(p,)) for p in files]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(store.list_files()) == len(files), "every file survived concurrent upsert"
    # no partial/zero rows
    rows = store.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    assert rows == len(files)
