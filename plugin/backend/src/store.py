"""Phase 3 — SQLite graph store (adjacency list).

Migrates the Phase 1 JSONL ``Payload`` into a queryable ``nlp_graph.db``
(phase3_ingest.md §2.5–§2.6). **Canonical nodes are content-deterministic**, so
they are shared *across* files (``node_id`` is the PK); a re-index of one file
must not wipe another file's canonical node — deletion is scoped to that file's
block nodes + the file's own edges + its FILE/BLOCK rows, and canonical nodes
linked only to deleted blocks are pruned.

Key properties (§3.4 멱등성): upsert = delete-then-insert keyed by
``file_uuid``; readers→writers safe via WAL + single connection writer.

Threading: SQLite per-connection objects are bound to their creating thread by
default; a parallel batch indexer or watcher callback can land on another thread.
We pass ``check_same_thread=False`` and serialise **all** access (read+write)
through one ``threading.Lock`` — the standard "single connection + lock" model.
Writers stay single-writer in effect; readers never see a half-committed txn.
"""
from __future__ import annotations

import math
import os
import re
import sqlite3
import threading
from pathlib import Path
from uuid import UUID

from .schema import Context, GraphEdge, GraphNode

DEFAULT_DB = Path("nlp_graph.db")

# A concept in more than this fraction of the notes is the vault's own
# vocabulary, not a link between two of them. Only applied once there are
# enough notes for the ratio to mean anything — see GraphStore._idf_floor.
#
# A third is deliberately aggressive. At a half, words like "use" (6 of 13 notes
# in the mixed-topic fixture) squeak through and bridge an octopus note to a git
# note — the exact complaint this guards against. The cost of being wrong here is
# asymmetric: a missed suggestion is invisible, a junk one erodes trust in every
# suggestion beside it.
MAX_DF_RATIO = 0.35
MIN_FILES_FOR_DF = 8
# Below this, document frequency carries no information at all — see
# GraphStore._concept_idf.
MIN_FILES_FOR_IDF = 3

# Cosine below this is coincidence, not relatedness.
#
# Calibrated against a real 80-note vault, which offers 2137 possible pairs: at
# 0.05 you get 715 of them, including "cache ↔ idempotence" bridged by
# {Restore, fetched, times}; at 0.15 you get 38, and they are the ones a person
# would actually draw ("canary-deployment ↔ rollout", "graph-theory ↔ dag").
# Because cosine is length-normalised, one number means the same thing in every
# vault — a summed-idf threshold could never be portable that way.
MIN_SCORE = 0.15

# ``[[target]]`` in any of Obsidian's spellings. Cross-*file* link resolution
# lives here rather than in the linker because ``linker.link`` sees one file at
# a time and cannot know what other notes exist — its ``ref-to`` rule therefore
# only ever resolved wikilinks against headings *within* the same file, which
# is not what ``[[Note]]`` means in a vault.
_WIKI_RE = re.compile(r"\[\[([^\[\]]+)\]\]")

_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS files(
    file_uuid     TEXT PRIMARY KEY,
    file_path     TEXT,
    file_name     TEXT,
    creator       TEXT,
    lang          TEXT,
    mtime         REAL
);
CREATE TABLE IF NOT EXISTS blocks(
    block_id        TEXT PRIMARY KEY,
    file_uuid       TEXT NOT NULL,
    kind            TEXT,
    level           INTEGER,
    text            TEXT,
    span_start      INTEGER,
    span_end        INTEGER,
    parent_block_id TEXT,
    FOREIGN KEY(file_uuid) REFERENCES files(file_uuid)
);
CREATE INDEX IF NOT EXISTS blocks_file ON blocks(file_uuid);

CREATE TABLE IF NOT EXISTS nodes(
    node_id      TEXT PRIMARY KEY,
    kind         TEXT,
    label        TEXT,
    ref_block_id TEXT,
    file_uuid    TEXT,
    lemma        TEXT,
    FOREIGN KEY(file_uuid) REFERENCES files(file_uuid)
);
CREATE INDEX IF NOT EXISTS nodes_file   ON nodes(file_uuid);
CREATE INDEX IF NOT EXISTS nodes_kind   ON nodes(kind);

CREATE TABLE IF NOT EXISTS edges(
    src    TEXT,
    dst    TEXT,
    label  TEXT,
    file_uuid TEXT,
    PRIMARY KEY(src, dst, label, file_uuid)
);
CREATE INDEX IF NOT EXISTS edges_dst   ON edges(dst);
CREATE INDEX IF NOT EXISTS edges_label ON edges(label);
"""


class GraphStore:
    """A thin SQLite-backed store for parsed-document graphs.

    Use as a context manager; the single connection keeps WAL + upsert atomic.
    """

    def __init__(self, db: Path | str = DEFAULT_DB):
        self.db = Path(db)
        self.db.parent.mkdir(parents=True, exist_ok=True)
        # The store holds the full text of every block, i.e. the user's private
        # notes, so it must not be world-readable — sqlite creates new files at
        # 0644 by default. Tighten to owner-only *before* connecting, so no data
        # is ever written to a readable file. A no-op on an existing db (the user
        # may have chosen their own mode) and on Windows (POSIX bits are ignored,
        # where ACLs govern instead).
        fresh = not self.db.exists()
        # check_same_thread=False + a serialising lock = the standard
        # "single connection shared across threads" pattern. All public
        # methods hold ``self._lock``; private ``_delete_file_locked`` and
        # the raw ``conn`` field are only touched *inside* that lock.
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(self.db), check_same_thread=False)
        if fresh:
            try:
                os.chmod(self.db, 0o600)
            except OSError:
                pass
        with self._lock:
            self.conn.executescript(_SCHEMA)
            self._migrate_locked()
            self.conn.commit()

    def _migrate_locked(self) -> None:
        """Additive column migrations for stores written by an older version.

        ``CREATE TABLE IF NOT EXISTS`` leaves an *existing* table exactly as it
        was, so a store created before a column existed would raise on every
        query selecting it. Adding the column keeps old stores readable; the
        values stay NULL until the vault is reindexed, and every reader treats
        NULL as "not known" rather than assuming it is populated.
        """
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(nodes)")}
        if "lemma" not in cols:
            self.conn.execute("ALTER TABLE nodes ADD COLUMN lemma TEXT")

    # -- context manager -------------------------------------------------
    def __enter__(self) -> "GraphStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            try:
                self.conn.commit()
            finally:
                self.conn.close()

    # -- writers ---------------------------------------------------------
    def upsert_file(self, ctx: Context) -> None:
        """Insert (or replace) one file's full graph. Idempotent per ``file_uuid``."""
        f = ctx.file
        fu = str(f.unique_id)
        with self._lock:
            try:
                self.conn.execute("BEGIN")
                # Idempotent re-index (§3.4): FileMeta.uuid is random per run,
                # so deleting by the *new* uuid wouldn't catch the previous
                # run's rows. First purge any existing rows for this same
                # file_path, then drop the (probably-absent) new-uuid row too.
                existing = self.conn.execute(
                    "SELECT file_uuid FROM files WHERE file_path=? AND file_uuid<>?",
                    (str(f.file_path), fu),
                ).fetchall()
                for (old_fu,) in existing:
                    self._delete_file_locked(old_fu)
                self._delete_file_locked(fu)
                self.conn.execute(
                    "INSERT INTO files(file_uuid,file_path,file_name,creator,lang,mtime) "
                    "VALUES(?,?,?,?,?,?)",
                    (fu, str(f.file_path), f.file_name, f.creator, f.lang, f.mtime),
                )
                for b in ctx.blocks:
                    self.conn.execute(
                        "INSERT INTO blocks(block_id,file_uuid,kind,level,text,"
                        "span_start,span_end,parent_block_id) VALUES(?,?,?,?,?,?,?,?)",
                        (str(b.block_id), fu, b.kind.value, b.level, b.text,
                         b.span[0], b.span[1],
                         str(b.parent_block_id) if b.parent_block_id else None),
                    )
                for n in ctx.nodes:
                    self.conn.execute(
                        "INSERT OR IGNORE INTO nodes"
                        "(node_id,kind,label,ref_block_id,file_uuid,lemma) "
                        "VALUES(?,?,?,?,?,?)",
                        (n.node_id, n.kind, n.label,
                         str(n.ref_block_id) if n.ref_block_id else None,
                         fu if n.kind != "canonical" else None,  # canonical = cross-file
                         n.lemma),
                    )
                for e in ctx.edges:
                    self.conn.execute(
                        "INSERT OR IGNORE INTO edges(src,dst,label,file_uuid) VALUES(?,?,?,?)",
                        (e.src, e.dst, e.label, fu),
                    )
                self.conn.execute("COMMIT")
            except Exception:
                try:
                    self.conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass  # txn may be gone already
                raise

    def _delete_file_locked(self, fu: str) -> None:
        """Caller MUST hold ``self._lock`` (and be inside an open txn)."""
        self.conn.execute("DELETE FROM edges  WHERE file_uuid=?", (fu,))
        # block nodes for this file; canonical nodes are global
        self.conn.execute("DELETE FROM nodes  WHERE file_uuid=?", (fu,))
        self.conn.execute("DELETE FROM blocks WHERE file_uuid=?", (fu,))
        self.conn.execute("DELETE FROM files  WHERE file_uuid=?", (fu,))
        # prune now-orphaned canonical nodes (no edges anywhere reference them)
        self.conn.execute(
            "DELETE FROM nodes WHERE kind='canonical' AND node_id NOT IN "
            "(SELECT DISTINCT src FROM edges UNION SELECT DISTINCT dst FROM edges)"
        )

    def remove_file(self, file_uuid: UUID | str) -> None:
        with self._lock:
            try:
                self.conn.execute("BEGIN")
                self._delete_file_locked(str(file_uuid))
                self.conn.execute("COMMIT")
            except Exception:
                try:
                    self.conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise

    def purge_file(self, file_name: str) -> int:
        """Delete all rows of files matching ``file_name`` (§3.2 delayed removal)."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT file_uuid FROM files WHERE file_name=?", (file_name,)
            ).fetchall()
            try:
                self.conn.execute("BEGIN")
                for (fu,) in rows:
                    self._delete_file_locked(fu)
                self.conn.execute("COMMIT")
            except Exception:
                try:
                    self.conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
            return len(rows)

    # -- readers ---------------------------------------------------------
    def list_files(self) -> list[tuple[str, str]]:
        with self._lock:
            return [(r[0], r[1]) for r in self.conn.execute(
                "SELECT file_name, file_uuid FROM files ORDER BY file_name")]

    def neighbors(self, block_id: str) -> dict[str, list[tuple[str, str, str]]]:
        """Outgoing + incoming edges for a block, as ``(node_id, label, text)``.

        ``text`` is what the endpoint *says* — a token's word, a block's opening
        text. Without it a caller receives rows like
        ``{"node": "0071da39ef647e7e", "label": "token"}``: a content hash, which
        is the right key for the graph and worth nothing to a reader. Resolving
        it here is what turns "here are 295 pointers" into an answer.
        """
        with self._lock:
            out = self.conn.execute(
                "SELECT e.dst, e.label, "
                "       COALESCE(n.label, substr(b.text,1,60)) "
                "FROM edges e "
                "LEFT JOIN nodes  n ON n.node_id  = e.dst "
                "LEFT JOIN blocks b ON b.block_id = e.dst "
                "WHERE e.src=?", (block_id,)).fetchall()
            inc = self.conn.execute(
                "SELECT e.src, e.label, "
                "       COALESCE(n.label, substr(b.text,1,60)) "
                "FROM edges e "
                "LEFT JOIN nodes  n ON n.node_id  = e.src "
                "LEFT JOIN blocks b ON b.block_id = e.src "
                "WHERE e.dst=?", (block_id,)).fetchall()
            return {
                "out": [(o[0], o[1], o[2] or "") for o in out],
                "in":  [(i[0], i[1], i[2] or "") for i in inc],
            }

    # ------------------------------------------------------------ links
    #
    # Resolved at query time from ``blocks.text`` rather than persisted: the
    # text is already stored, so re-extraction needs no migration and can never
    # go stale against an edited note.

    @staticmethod
    def _link_target(raw: str) -> str | None:
        """Normalise one ``[[…]]`` body to the note name it points at.

        Handles Obsidian's spellings: ``[[Note|alias]]``, ``[[Note#Heading]]``,
        ``[[Note#^block]]``, ``[[folder/Note]]``. Returns None for a pure
        in-page anchor (``[[#Heading]]``), which targets no other note.
        """
        t = raw.split("|", 1)[0]          # drop alias
        t = t.split("#", 1)[0]            # drop heading/block anchor
        t = t.rsplit("/", 1)[-1]          # keep the basename of a path link
        t = t.strip()
        return t or None

    @staticmethod
    def _stem(file_name: str) -> str:
        return file_name.rsplit(".", 1)[0].strip().lower()

    @staticmethod
    def wikilink_for(file_name: str) -> str:
        """The exact text to write to link to this note: ``[[bluegreen]]``.

        Obsidian resolves ``[[X]]`` against the **file name**, not the note's
        heading — so an agent reading a suggestion for "bluegreen.md" and writing
        the obvious ``[[블루그린]]`` (its title) does not create a link: it creates
        a *phantom note*, which is worse than doing nothing and which nothing in
        the output warns about. Handing back the literal string removes the
        inference, and with it the failure.
        """
        return f"[[{file_name.rsplit('.', 1)[0]}]]"

    def file_links(self) -> dict[str, set[str]]:
        """``file_uuid -> set of file_uuids it wikilinks to`` (resolved by name).

        Unresolvable targets (a link to a note that isn't indexed) are dropped:
        for "already linked?" purposes only edges between known notes count.
        """
        with self._lock:
            # ORDER BY so that two notes sharing a name (same basename in
            # different folders) resolve the same way every run rather than by
            # SQL row order. Obsidian disambiguates those by path proximity;
            # we do not model folders, so the first by name wins — deterministic
            # if not always the note Obsidian would pick.
            files = self.conn.execute(
                "SELECT file_uuid, file_name FROM files ORDER BY file_name, file_uuid"
            ).fetchall()
            blocks = self.conn.execute(
                "SELECT file_uuid, text FROM blocks WHERE text LIKE '%[[%'"
            ).fetchall()
        by_stem: dict[str, str] = {}
        for fu, name in files:
            by_stem.setdefault(self._stem(name), fu)
        out: dict[str, set[str]] = {fu: set() for fu, _ in files}
        for fu, text in blocks:
            for raw in _WIKI_RE.findall(text or ""):
                target = self._link_target(raw)
                if not target:
                    continue
                dst = by_stem.get(target.lower())
                if dst and dst != fu:
                    out.setdefault(fu, set()).add(dst)
        return out

    def _linked_pairs(self) -> set[frozenset[str]]:
        """Undirected "these two notes are already connected" set. Direction
        doesn't matter for suggesting a bridge — one link is enough."""
        pairs: set[frozenset[str]] = set()
        for src, dsts in self.file_links().items():
            for dst in dsts:
                pairs.add(frozenset((src, dst)))
        return pairs

    @staticmethod
    def _concept_key(label: str, lemma: str | None = None) -> str:
        """Fold a token to the concept it denotes.

        Canonical nodes key on ``(text, pos)``, which is right for the graph but
        too strict for "do these notes talk about the same thing":

        * a word starting a sentence is capitalised, so the tagger calls
          ``Camouflage`` an NNP and ``camouflage`` an NN — two nodes, and a
          bridge between two notes about camouflage that never appears;
        * ``dogs`` and ``dog`` are two nodes for one idea.

        The lemma answers both (the tagger lowercases it), so prefer it. It is
        NULL for nodes written before the column existed and for non-token
        nodes, hence the case-fold fallback.

        The known cost is conflating a proper noun with its common-noun
        homograph (Apple/apple). For *suggesting* a link that trade is worth it,
        and the graph's own nodes are untouched — this fold is query-only.
        """
        return (lemma or label).lower()

    def _concept_rows(self) -> list[tuple[str, str, str, str | None]]:
        """``(file_uuid, block_id, label, lemma)`` per content-word token use.

        Code blocks are excluded. They are not prose, and their tokens are not
        things two notes can be *about*: left in, a docs vault links every page
        to every other on ``--json``, ``-m`` and ``$DEST``. The parser layer
        already skips ``BlockKind.CODE`` for exactly this reason; the token layer
        did not, which is where most junk concepts came from.
        """
        with self._lock:
            rows = self.conn.execute(
                "SELECT DISTINCT b.file_uuid, b.block_id, n.label, n.lemma "
                "FROM nodes n "
                "JOIN edges e  ON e.dst = n.node_id AND e.label = 'token' "
                "JOIN blocks b ON b.block_id = e.src "
                "WHERE n.kind = 'token' AND b.kind != 'code'"
            ).fetchall()
        return [(fu, bid, lab, lem) for fu, bid, lab, lem in rows
                if self._is_content_word(lab)]

    def _concepts_by_file(self) -> dict[str, dict[str, str]]:
        """``file_uuid -> {concept_key: representative label}``."""
        out: dict[str, dict[str, str]] = {}
        for fu, _bid, label, lemma in self._concept_rows():
            out.setdefault(fu, {}).setdefault(
                self._concept_key(label, lemma), label
            )
        return out

    def _concept_idf(self, by_file: dict[str, dict[str, str]]) -> dict[str, float]:
        """``concept_key -> ln(n_files / document_frequency)``.

        Sharing a word is not evidence that two notes belong together — it
        depends *entirely* on which word. "use", "note", "see" appear in
        everything a person writes and connect nothing; "hemocyanin" in two
        notes is a real signal. A closed-class stoplist cannot express that,
        because the uninformative words are not a fixed list — they are whatever
        *this* vault says constantly. "plugin" is noise in a plugin repo and
        meaningful anywhere else.

        Document frequency measures it directly and calibrates itself per vault:
        a concept in every note scores 0 and contributes nothing.
        """
        n = len(by_file)
        if not n:
            return {}
        df: dict[str, int] = {}
        for concepts in by_file.values():
            for key in concepts:
                df[key] = df.get(key, 0) + 1
        if n < MIN_FILES_FOR_IDF:
            # Below three notes, "in both" and "in every note" are the same fact,
            # so ln(n/df) is 0 for every shared concept — the vectors come out
            # all-zero and cosine is 0/0. There is no rarity to measure yet.
            # Weight every concept equally instead, which degrades the score to
            # plain proportional overlap: the only signal a vault this small has.
            return {k: 1.0 for k in df}
        return {k: math.log(n / c) for k, c in df.items()}

    @staticmethod
    def _idf_floor(n_files: int) -> float:
        """Score a concept must beat to count as evidence, given vault size.

        The threshold has to scale, because "appears everywhere" only means
        something once there is an everywhere:

        * **< 3 notes** — "in both notes" and "in every note" are the same fact,
          so document frequency carries no information at all; keep everything
          (a floor here would leave a 2-note vault with no bridges).
        * **< MIN_FILES_FOR_DF** — only discard what is in *literally* every
          note (idf == 0). Unarguable, and safe on a small vault where an honest
          shared term may still be common.
        * **beyond that** — discard vault-wide vocabulary: anything in more than
          ``MAX_DF_RATIO`` of the notes. ``idf > -ln(ratio)`` is exactly
          ``df < ratio * n``.
        """
        if n_files < MIN_FILES_FOR_IDF:
            return -1.0     # weights are uniform 1.0 here; keep every concept
        if n_files < MIN_FILES_FOR_DF:
            return 0.0
        return -math.log(MAX_DF_RATIO)

    def _relate(
        self,
        ca: dict[str, str],
        cb: dict[str, str],
        idf: dict[str, float],
        floor: float,
        labels: dict[str, str],
    ) -> tuple[list[str], float]:
        """How related two notes are: cosine over idf-weighted concept vectors.

        Returns the informative shared concepts (rarest first) and a score in
        **0..1**.

        Summing idf instead makes length the dominant signal: on a real vault two
        long guides scored 759 simply for being long, burying a short note that
        was genuinely about the same thing. Cosine divides by each note's own
        magnitude, so the question becomes "what fraction of what these notes are
        about is shared" — which is the actual question — and the number means
        the same thing for a one-line note and a 500-line one.

        Ordering the concepts by idf matters as much as the filtering: the reason
        shown for a suggestion should be its rarest word, not whichever sorted
        first alphabetically.
        """
        a = {k: v for k in ca if (v := idf.get(k, 0.0)) > floor}
        b = {k: v for k in cb if (v := idf.get(k, 0.0)) > floor}
        shared = [k for k in a if k in b]
        if not shared:
            return [], 0.0
        dot = sum(a[k] * b[k] for k in shared)
        na = math.sqrt(sum(v * v for v in a.values()))
        nb = math.sqrt(sum(v * v for v in b.values()))
        shared.sort(key=lambda k: -idf[k])
        return [labels[k] for k in shared], (dot / (na * nb) if na and nb else 0.0)

    def file_mtimes(self) -> dict[str, float]:
        """``file_path -> mtime`` as recorded at index time.

        The column has been written since Phase 1 and read by nobody, so every
        reindex reprocessed the whole corpus and callers who skipped indexing got
        silently stale answers. This is the missing half.
        """
        with self._lock:
            rows = self.conn.execute(
                "SELECT file_path, mtime FROM files WHERE mtime IS NOT NULL"
            ).fetchall()
        return {p: m for p, m in rows}

    def doc_cards(self, *, keywords: int = 8) -> list[dict]:
        """One cheap card per note: what it is, without reading it.

        ``keywords`` are the note's most *distinctive* concepts — ranked by idf,
        so they are the words this note uses and the rest of the vault doesn't.
        Ranking by frequency instead would hand back "the", "note", "use" for
        every card and distinguish nothing.

        Deliberately **no summary**. A summary needs comprehension, i.e. an LLM,
        which this project doesn't have and won't; and generating one per note
        would mean reading every note in full — spending the cost the card exists
        to avoid. Headings and distinctive keywords are what can be had for free,
        and for a structured note the headings *are* the summary.
        """
        by_file = self._concepts_by_file()
        idf = self._concept_idf(by_file)
        with self._lock:
            files = self.conn.execute(
                "SELECT file_uuid, file_name, file_path, lang FROM files "
                "ORDER BY file_name"
            ).fetchall()
            heads = self.conn.execute(
                "SELECT file_uuid, level, text, span_start FROM blocks "
                "WHERE kind='heading' ORDER BY file_uuid, span_start"
            ).fetchall()
            sizes = dict(self.conn.execute(
                "SELECT file_uuid, MAX(span_end) FROM blocks GROUP BY file_uuid"
            ).fetchall())
        by_head: dict[str, list[dict]] = {}
        for fu, lvl, text, _ in heads:
            by_head.setdefault(fu, []).append(
                {"level": lvl, "title": (text or "").strip()}
            )
        out: list[dict] = []
        for fu, name, path, lang in files:
            concepts = by_file.get(fu, {})
            ranked = sorted(
                concepts.items(), key=lambda kv: -idf.get(kv[0], 0.0)
            )[:keywords]
            out.append({
                "name": name,
                "file_path": path,
                "lang": lang or "",
                "n_chars": sizes.get(fu) or 0,
                "headings": by_head.get(fu, []),
                "keywords": [label for _key, label in ranked],
            })
        return out

    def has_concepts(self) -> bool:
        """Whether the token layer exists at all.

        Concepts are token nodes, which ``index`` only writes when ``--pos``
        (or ``--parse``, which implies it) was passed. Without them every
        concept query returns an empty list that looks like "your notes share
        nothing" rather than "you indexed without the layer this needs" —
        callers use this to tell the two apart.
        """
        with self._lock:
            row = self.conn.execute(
                "SELECT 1 FROM nodes WHERE kind='token' LIMIT 1"
            ).fetchone()
        return row is not None

    def bridges(
        self, file_uuid: str, *, limit: int = 20, min_shared: int = 2,
        min_score: float = MIN_SCORE,
    ) -> list[dict]:
        """Notes that share concepts with ``file_uuid`` but aren't linked to it.

        This is the "unlinked mention" that Obsidian's own feature cannot find:
        it matches literal strings, so a Korean vault never surfaces
        문어는/문어를 as the same mention. Here the comparison is over canonical
        token nodes, which 조사 stripping has already collapsed.

        Only *informative* shared concepts count — see :meth:`_concept_idf`.
        Sharing "use" or "note" with another page is not a reason to link it, and
        weighting by document frequency is what separates that from sharing
        "hemocyanin". Rows rank by summed idf, not by how many words overlap, so
        one rare term outranks a dozen common ones.

        Returns one row per candidate note, strongest first::

            {"file_uuid", "name", "shared": ["문어", "지능"], "n_shared": 2,
             "score": 1.83}
        """
        by_file = self._concepts_by_file()
        mine = by_file.get(file_uuid, {})
        if not mine:
            return []
        idf = self._concept_idf(by_file)
        floor = self._idf_floor(len(by_file))
        linked = self._linked_pairs()
        with self._lock:
            names = dict(
                self.conn.execute("SELECT file_uuid, file_name FROM files").fetchall()
            )
        rows: list[dict] = []
        for other, concepts in by_file.items():
            if other == file_uuid:
                continue
            if frozenset((file_uuid, other)) in linked:
                continue          # already connected — nothing to suggest
            # Label each shared concept with *this* note's spelling: the reader
            # is looking at this note, so showing the other note's inflection
            # ("tools" when they wrote "tool") reads like a different word.
            shared, score = self._relate(mine, concepts, idf, floor, mine)
            if len(shared) < min_shared or score < min_score:
                continue
            other_name = names.get(other, "?")
            rows.append({
                "file_uuid": other,
                "name": other_name,
                # the literal text to insert — see wikilink_for
                "link": self.wikilink_for(other_name),
                "shared": shared,
                "n_shared": len(shared),
                "score": round(score, 3),
            })
        rows.sort(key=lambda r: (-r["score"], r["name"]))
        return rows[:limit]

    def orphans(self) -> list[tuple[str, str]]:
        """Notes with no wikilink in or out — ``(file_name, file_uuid)``."""
        links = self.file_links()
        linked: set[str] = set()
        for src, dsts in links.items():
            if dsts:
                linked.add(src)
            linked.update(dsts)
        with self._lock:
            files = self.conn.execute(
                "SELECT file_uuid, file_name FROM files ORDER BY file_name"
            ).fetchall()
        return [(name, fu) for fu, name in files if fu not in linked]

    def vault_graph(
        self,
        *,
        root: str | None = None,
        folder: str | None = None,
        concept: str | None = None,
        min_shared: int = 2,
        min_score: float = MIN_SCORE,
        top_concepts_per_note: int = 5,
    ) -> dict:
        """A scoped, hierarchical picture of how notes relate.

        Obsidian's own graph is a flat force-directed blob of every note and
        every link: it discards the folder tree the user already built, and it
        shows only links that exist. This returns the two things it can't —

        * **hierarchy**: each note's folder path (from ``files.file_path``,
          relativised to ``root``), so a view can lay notes out under the
          structure the vault already has instead of scattering them;
        * **scope**: ``folder`` restricts to a subtree, ``concept`` to the notes
          that actually discuss one thing — a graph you can read, rather than
          the whole vault at once.

        Edges come in two kinds and must stay distinguishable: ``links`` are
        real wikilinks, ``bridges`` are *suggestions* (shared concepts, no link)
        and are never to be drawn as though they were real.

        ``root`` defaults to the deepest common ancestor of the notes in scope,
        which is what makes folders readable when the caller (the CLI) doesn't
        know the vault root.
        """
        with self._lock:
            rows = self.conn.execute(
                "SELECT file_uuid, file_name, file_path, lang FROM files "
                "ORDER BY file_path"
            ).fetchall()
            block_counts = dict(self.conn.execute(
                "SELECT file_uuid, COUNT(*) FROM blocks GROUP BY file_uuid"
            ).fetchall())

        by_file = self._concepts_by_file()

        # ---- scope
        keep = {fu for fu, _n, _p, _l in rows}
        scope_kind, scope_value = "vault", ""
        if concept:
            key = self._concept_key(concept, None)
            keep = {fu for fu, cs in by_file.items() if key in cs}
            scope_kind, scope_value = "concept", concept
        sel = [r for r in rows if r[0] in keep]

        # ---- root + folders
        paths = [Path(p) for _fu, _n, p, _l in sel]
        if root:
            base = Path(root)
        elif len(paths) > 1:
            try:
                base = Path(os.path.commonpath([str(p.parent) for p in paths]))
            except ValueError:      # different drives (Windows)
                base = paths[0].parent
        elif paths:
            base = paths[0].parent
        else:
            base = Path(".")

        def rel_folder(p: Path) -> str:
            try:
                r = p.parent.relative_to(base)
            except ValueError:
                return ""
            return "" if str(r) == "." else str(r).replace("\\", "/")

        notes: list[dict] = []
        for fu, name, path, lang in sel:
            f = rel_folder(Path(path))
            if folder is not None:
                want = folder.strip("/")
                if want and not (f == want or f.startswith(want + "/")):
                    continue
            notes.append({
                "file_uuid": fu,
                "name": name,
                "path": path,
                "folder": f,
                "lang": lang or "",
                "n_blocks": block_counts.get(fu, 0),
                "concepts": sorted(by_file.get(fu, {}).values())[
                    :top_concepts_per_note
                ],
            })
        if folder is not None:
            scope_kind, scope_value = "folder", folder.strip("/")
            keep = {n["file_uuid"] for n in notes}

        # ---- edges, restricted to the scope
        links = [
            {"src": src, "dst": dst}
            for src, dsts in self.file_links().items() if src in keep
            for dst in dsts if dst in keep
        ]
        linked = self._linked_pairs()
        # idf over the whole vault, not just the scope: how common a word is is a
        # property of the vault. Recomputing it inside a 3-note scope would make
        # every word there look rare.
        idf = self._concept_idf(by_file)
        floor = self._idf_floor(len(by_file))
        bridges: list[dict] = []
        seen: set[frozenset[str]] = set()
        for a in keep:
            for b in keep:
                if a >= b:
                    continue
                pair = frozenset((a, b))
                if pair in seen or pair in linked:
                    continue
                seen.add(pair)
                ca, cb = by_file.get(a, {}), by_file.get(b, {})
                shared, score = self._relate(ca, cb, idf, floor, ca)
                if len(shared) < min_shared or score < min_score:
                    continue
                bridges.append({"a": a, "b": b, "shared": shared,
                                "n_shared": len(shared), "score": round(score, 3)})
        bridges.sort(key=lambda r: -r["score"])

        folders = sorted({n["folder"] for n in notes})
        return {
            "scope": {"kind": scope_kind, "value": scope_value},
            "root": str(base),
            "folders": folders,
            "notes": notes,
            "links": links,
            "bridges": bridges,
        }

    def audit(
        self, *, min_shared: int = 2, limit: int = 20,
        min_score: float = MIN_SCORE,
    ) -> dict:
        """Vault-level health: orphans, and the strongest unlinked note pairs.

        ``min_shared=2`` because a single shared word is weak evidence that two
        notes belong together; two or more is where the suggestion earns its
        place.
        """
        by_file = self._concepts_by_file()
        idf = self._concept_idf(by_file)
        floor = self._idf_floor(len(by_file))
        linked = self._linked_pairs()
        with self._lock:
            files = self.conn.execute(
                "SELECT file_uuid, file_name FROM files"
            ).fetchall()
        names = {fu: name for fu, name in files}

        seen: set[frozenset[str]] = set()
        pairs: list[dict] = []
        for a, ca in by_file.items():
            for b, cb in by_file.items():
                if a >= b:
                    continue
                key = frozenset((a, b))
                if key in seen or key in linked:
                    continue
                seen.add(key)
                # label from `a` (the pair is ordered a < b, so this is stable)
                shared, score = self._relate(ca, cb, idf, floor, ca)
                if len(shared) < min_shared or score < min_score:
                    continue
                a_name, b_name = names.get(a, "?"), names.get(b, "?")
                pairs.append({
                    "a": a_name, "b": b_name,
                    "link_a": self.wikilink_for(a_name),
                    "link_b": self.wikilink_for(b_name),
                    "shared": shared, "n_shared": len(shared),
                    "score": round(score, 3),
                })
        pairs.sort(key=lambda p: (-p["score"], p["a"], p["b"]))

        orphan_rows = self.orphans()
        n_links = sum(len(d) for d in self.file_links().values())
        return {
            "n_files": len(files),
            "n_links": n_links,
            "orphans": [{"name": n, "file_uuid": u} for n, u in orphan_rows],
            "unlinked_pairs": pairs[:limit],
        }

    @staticmethod
    def _is_content_word(label: str) -> bool:
        """Closed-class words and punctuation are not concepts.

        A concept list topped by ``의`` / ``the`` / ``.`` is noise: those bridge
        every note precisely because they carry no meaning. The two taggers
        already enumerate exactly the closed classes — the EN function-word
        lexicon and the KO particle table — so this reuses them rather than
        inventing a third stoplist that could drift from either.

        POS would be the principled key, but ``GraphNode`` carries only
        ``(node_id, kind, label)`` — the tag is baked into the canonical id and
        not recoverable — so this matches on the surface form.
        """
        from .adapters_ko import JOSA, STOP_KO
        from .postagger import _LEXICON

        w = label.strip()
        if not w:
            return False
        # A concept has to be a *word*. "Contains a letter or digit somewhere"
        # is far too weak: it admits `--json`, `-m`, `$DEST`, `plugin/main` and
        # `venv/bin/python`, which then bridge every page of a docs vault to
        # every other. Require it to start with a letter and carry no path or
        # shell punctuation.
        if not w[0].isalpha():
            return False
        # Path, shell and markup punctuation *inside* a token means it isn't a
        # word. TOKEN_STRIP only trims the ends, so `[ARCHITECTURE.md](…)` still
        # yields `md](ARCHITECTURE`, which starts with a letter and would
        # otherwise sail through.
        if any(ch in w for ch in "/\\=$@%^&+()[]{}<>|"):
            return False
        # bare numbers ("1", "2") bridge notes without meaning anything
        if w.isdigit():
            return False
        # single characters are tokenizer residue more often than concepts —
        # the "s" left by a possessive, a stray initial. A 1-syllable Korean
        # noun is the acceptable loss here (adapters_ko.MIN_STEM already
        # declines to split those, so they rarely surface alone).
        if len(w) == 1:
            return False
        if w.lower() in _LEXICON:      # EN determiners, prepositions, pronouns…
            return False
        if w in JOSA or w in STOP_KO:  # KO particles + closed-class words
            return False
        return True

    def top_concepts(
        self, limit: int = 20, *, min_files: int = 2
    ) -> list[tuple[str, str, int, int]]:
        """Concepts ranked by how many distinct files use them.

        Returns ``(concept_key, label, n_files, n_blocks)``.

        This is the cross-note concept view. :meth:`top_canonical` cannot serve
        it: that one ranks ``kind='canonical'`` nodes, which are the
        *duplicate-block* group markers and so are absent from any corpus whose
        blocks all differ — i.e. every real one. The concepts that actually
        recur live in the token layer, where ``canonical_id(text, pos)`` already
        collapses each ``(word, pos)`` onto one shared node; a token used by
        blocks in several files *is* a concept those notes have in common.

        Aggregation happens here rather than in SQL because the unit is the
        :meth:`_concept_key` fold, not the node: ``Camouflage`` and
        ``camouflage`` are two nodes but one concept, and ``GROUP BY node_id``
        would report them as two half-strength rows. Folding here also keeps
        this view consistent with :meth:`bridges` and :meth:`audit`.

        ``min_files=2`` keeps only words that bridge notes, which is the whole
        point — a term used once in one note links nothing.
        """
        agg: dict[str, tuple[str, set[str], set[str]]] = {}
        for fu, bid, label, lemma in self._concept_rows():
            key = self._concept_key(label, lemma)
            rep, files, blocks = agg.get(key, (label, set(), set()))
            files.add(fu)
            blocks.add(bid)
            agg[key] = (rep, files, blocks)
        out = [
            (key, rep, len(files), len(blocks))
            for key, (rep, files, blocks) in agg.items()
            if len(files) >= min_files
        ]
        out.sort(key=lambda r: (-r[2], -r[3], r[0]))
        return out[:limit]

    def top_canonical(self, limit: int = 10) -> list[tuple[str, str, int]]:
        """Duplicate-block group nodes ranked by member count.

        Note this is *not* the concept view — ``kind='canonical'`` nodes only
        exist for byte-identical duplicate blocks, which real prose does not
        have. See :meth:`top_concepts` for cross-note shared concepts.
        """
        with self._lock:
            rows = self.conn.execute(
                "SELECT n.node_id, n.label "
                "FROM nodes n WHERE n.kind='canonical'"
            ).fetchall()
            out: list[tuple[str, str, int]] = []
            for nid, label in rows:
                n = self.conn.execute(
                    "SELECT COUNT(DISTINCT src) FROM edges WHERE dst=? AND label='rep'",
                    (nid,)
                ).fetchone()[0]
                out.append((nid, label, n))
            out.sort(key=lambda t: t[2], reverse=True)
            return out[:limit]

    def search_blocks(
        self,
        pattern: str,
        *,
        kind: str | None = None,
        limit: int = 100,
    ) -> list[tuple[str, str, str, "int | None", str, int, int]]:
        """Scoped text search over stored blocks — the token-saving lever the
        Claude Code skill uses to "find blocks mentioning X" without loading
        every block's text into context.

        Case-insensitive substring (``LIKE %pat%``) on ``blocks.text``, joined
        to ``files.file_name`` so each hit carries its doc. Returns rows of
        ``(file_name, block_id, kind, level, text, span_start, span_end)`` —
        only matching blocks, ordered by file then position. ``kind`` filters
        (e.g. ``"heading"`` / ``"para"``); ``limit`` caps the result set.
        """
        # Escape the caller's text before wrapping it in %…%. `%` and `_` are
        # LIKE wildcards, so an unescaped pattern makes `search "%"` return every
        # block in the vault and `search "50%"` or `search "snake_case"` match
        # things that don't contain them. Callers type search terms, not
        # patterns; nothing in the interface suggests otherwise.
        esc = (pattern.replace("\\", "\\\\")
                      .replace("%", "\\%")
                      .replace("_", "\\_"))
        sql = (
            "SELECT f.file_name, b.block_id, b.kind, b.level, "
            "b.text, b.span_start, b.span_end "
            "FROM blocks b JOIN files f ON b.file_uuid = f.file_uuid "
            "WHERE b.text LIKE ? ESCAPE '\\' COLLATE NOCASE"
        )
        params: list = [f"%{esc}%"]
        if kind:
            sql += " AND b.kind = ?"
            params.append(kind)
        sql += " ORDER BY f.file_name, b.span_start LIMIT ?"
        params.append(limit)
        with self._lock:
            return self.conn.execute(sql, tuple(params)).fetchall()

    def block_text_for(self, file_uuid: str, block_id: str) -> str | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT text FROM blocks WHERE block_id=? AND file_uuid=?",
                (block_id, file_uuid)
            ).fetchone()
            return row[0] if row else None

    def file_uuid_for(self, file_name: str) -> str | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT file_uuid FROM files WHERE file_name=? LIMIT 1", (file_name,)
            ).fetchone()
            return row[0] if row else None


def store_context(ctx: Context, db: Path | str = DEFAULT_DB) -> GraphStore:
    """Convenience: open a store, upsert one context, return it (unclosed)."""
    s = GraphStore(db)
    s.upsert_file(ctx)
    return s
