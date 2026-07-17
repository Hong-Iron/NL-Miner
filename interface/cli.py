"""Phase 3 — thin CLI surface (argparse, stdlib only).

Subcommands (phase3_ingest.md §1 `interface/`, §2.8 쿼리 API):
    nlm index  <file|dir> [--parse] [--pos] [--json]
        preprocess→block→link→store(SQLite)+payload(JSONL)
    nlm query  <doc> [--block <id>] [--json]
    nlm graph  [--top-canonical N] [--json]
    nlm watch  <dir>        poll for changes → live re-index
    nlm purge  <doc>        confirm-deleted removal from the store
    nlm view   <file> [--parse] [--json]
        emit the parse-graph snapshot (JSON) for one file — the Obsidian
        plugin's graph view / decorations data source. In-memory (no store).
    nlm search  <pattern> [--kind K] [--limit N] [--json]
        scoped text search over indexed blocks (find blocks mentioning X) —
        the token-saving lever for the Claude Code skill: returns only
        matching rows so an agent needn't load every block's text.

Run with ``python -m interface.cli ...``.

Every subcommand keeps its human **text output by default**; ``--json`` makes a
subcommand print a single machine-readable JSON object on stdout (errors still
go to stderr with a nonzero exit, never partial JSON). The new ``view``
subcommand is JSON-native. The default text paths are unchanged so the existing
test suite stays green.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Before importing anything that builds a pydantic model — the point is to fail
# with a sentence rather than a TypeError from inside pydantic's type resolution.
from src.preflight import check as _preflight
_preflight()

from src.context import process
from src.filestructure import discover
from src.schema import BlockKind, FileMeta
from src.store import DEFAULT_DB, GraphStore
from observer.watchdog import Watcher

PAYLOAD_DIR = Path("payload")


def _index_one(store: GraphStore, f: Path, *, pos: bool, parse: bool) -> tuple[object, dict]:
    fm = FileMeta(file_path=f, file_name=f.name)
    ctx, stats = process(fm, pos=pos, parse=parse)
    from payload import save as save_payload
    save_payload(ctx, PAYLOAD_DIR)
    store.upsert_file(ctx)
    return ctx, stats


def cmd_index(args) -> int:
    target = Path(args.path)
    files = discover(target)
    if not files:
        print(f"no .md/.txt files under {target}", file=sys.stderr)
        return 1
    store = GraphStore(args.db)
    # Skip files whose mtime matches what the store recorded. Without this a
    # reindex costs a full reparse, which is why callers were tempted to skip it
    # and answer from a stale index instead — the expensive-but-correct option
    # losing to the cheap-but-wrong one. Making it cheap removes the temptation.
    # --force reprocesses everything: the *tokenizer* can change under a file
    # that didn't, and then mtime says "fresh" about rows built by old rules.
    skipped = 0
    if not args.force:
        known = store.file_mtimes()
        keep = []
        for fm in files:
            prev = known.get(str(fm.file_path))
            if prev is not None and fm.mtime is not None and prev == fm.mtime:
                skipped += 1
            else:
                keep.append(fm)
        files = keep
    total_saved = total_raw = 0
    tok_saved = tok_raw = 0
    cky_trees = cky_attempted = skipped_lang = 0
    file_rows: list[dict] = []
    try:
        for fm in files:
            ctx, stats = _index_one(store, fm.file_path, pos=args.pos, parse=args.parse)
            total_saved += stats["saved_tokens"]
            total_raw += stats["raw_total_tokens"]
            tok_saved += stats.get("token_saved", 0)
            tok_raw += stats.get("token_raw", 0)
            cky_trees += stats.get("cky_trees", 0)
            cky_attempted += (stats.get("parsed_sentences", 0)
                              + stats.get("skipped_sentences", 0))
            skipped_lang += stats.get("skipped_lang", 0)
            file_rows.append({
                "name": fm.file_name,
                "n_blocks": stats["n_blocks"],
                "n_nodes": stats["n_nodes"],
                "n_edges": stats["n_edges"],
                "lang": ctx.file.lang,
                "raw_total_tokens": stats["raw_total_tokens"],
                # dup-block dedup: ~always 0 on real prose (no two blocks are
                # byte-identical). Kept for contract stability; ``token_saved``
                # below is the dedup that actually fires.
                "saved_tokens": stats["saved_tokens"],
                "token_raw": stats.get("token_raw", 0),
                "token_unique": stats.get("token_unique", 0),
                "token_saved": stats.get("token_saved", 0),
                "cky_trees": stats.get("cky_trees", 0),
                "cky_coverage": stats.get("cky_coverage", 0.0),
                "skipped_lang": stats.get("skipped_lang", 0),
                "n_phrase_nodes": stats.get("n_phrase_nodes", 0),
                "n_dep_edges": stats.get("n_dep_edges", 0),
            })
            if not args.json:
                print(
                    f"  indexed {fm.file_name:30s} "
                    f"blocks={stats['n_blocks']:3d} nodes={stats['n_nodes']:3d} "
                    f"edges={stats['n_edges']:3d} lang={ctx.file.lang}"
                )
    finally:
        store.close()
    ratio = total_saved / total_raw if total_raw else 0.0
    ratio_token = tok_saved / tok_raw if tok_raw else 0.0
    cky_cov = cky_trees / cky_attempted if cky_attempted else 0.0
    if args.json:
        out = {
            "ok": True,
            "db": str(args.db),
            "parse": args.parse,
            "pos": args.parse or args.pos,
            "files": file_rows,
            "skipped_unchanged": skipped,
            "totals": {
                "files": len(file_rows),
                "skipped_unchanged": skipped,
                "raw_tokens": total_raw,
                "saved_tokens": total_saved,
                "ratio": ratio,
                # the dedup that actually fires (canonical token collapse)
                "token_raw": tok_raw,
                "token_saved": tok_saved,
                "ratio_token": ratio_token,
                # honest constituency coverage over attempted EN sentences
                "cky_trees": cky_trees,
                "cky_attempted": cky_attempted,
                "cky_coverage": cky_cov,
                "skipped_lang": skipped_lang,
            },
        }
        print(json.dumps(out))
        return 0
    # Report the dedup that fires (token) rather than leading with the
    # dup-block ratio, which reads 0.000 on any real corpus.
    unchanged = f" | {skipped} unchanged, skipped" if skipped else ""
    print(f"\n{len(files)} file(s){unchanged} | tokens={tok_raw} deduped={tok_saved} "
          f"({ratio_token:.1%}) | dup-block saved={total_saved} ({ratio:.1%})")
    if args.parse:
        print(f"parse: CKY trees {cky_trees}/{cky_attempted} ({cky_cov:.1%} of EN "
              f"sentences) | non-EN sentences skipped: {skipped_lang}")
    print(f"store: {args.db} | payloads: {PAYLOAD_DIR}/")
    return 0


def cmd_query(args) -> int:
    store = GraphStore(args.db)
    try:
        fu = store.file_uuid_for(args.doc)
        if not fu:
            print(f"no indexed file named {args.doc!r}", file=sys.stderr)
            return 1
        if args.block:
            nb = store.neighbors(args.block)
            # A block links to every distinct word it contains — on a real note
            # that is ~300 `token` edges, which drowned the handful of edges that
            # actually say something (ref-to, contains, dep:*) in 3.5k tokens of
            # hashes. Summarise the words, list the relations. --tokens to opt in.
            def split(rows):
                rel = [r for r in rows if r[1] != "token"]
                tok = [r for r in rows if r[1] == "token"]
                return rel, tok
            out_rel, out_tok = split(nb["out"])
            in_rel, in_tok = split(nb["in"])
            if args.tokens:
                out_rel, in_rel = nb["out"], nb["in"]
                out_tok = in_tok = []
            row = lambda r: {"node": r[0], "label": r[1], "text": r[2]}
            if args.json:
                payload = {
                    "doc": args.doc, "block": args.block,
                    "out": [row(r) for r in out_rel],
                    "in":  [row(r) for r in in_rel],
                }
                if out_tok or in_tok:
                    words = sorted({r[2] for r in out_tok + in_tok if r[2]})
                    payload["words"] = {
                        "n": len(words),
                        "sample": words[:25],
                        "note": "pass --tokens for every word edge",
                    }
                print(json.dumps(payload))
                return 0
            print(f"block {args.block} neighbours:")
            for node, label, text in out_rel:
                print(f"  -> {label:9s} {text or node}")
            for node, label, text in in_rel:
                print(f"  <- {label:9s} {text or node}")
            if out_tok or in_tok:
                words = sorted({r[2] for r in out_tok + in_tok if r[2]})
                print(f"  ({len(words)} words: {', '.join(words[:12])}…  --tokens for all)")
            return 0
        rows = store.conn.execute(
            "SELECT block_id, kind, level, substr(text,1,60) "
            "FROM blocks WHERE file_uuid=? ORDER BY span_start", (fu,)
        ).fetchall()
        if args.json:
            full = store.conn.execute(
                "SELECT block_id, kind, level, text, span_start, span_end, parent_block_id "
                "FROM blocks WHERE file_uuid=? ORDER BY span_start", (fu,)
            ).fetchall()
            print(json.dumps({"doc": args.doc, "file_uuid": fu, "blocks": [
                {"block_id": bid, "kind": k, "level": lvl,
                 "text": txt, "span_start": ss, "span_end": se,
                 "parent_block_id": par}
                for bid, k, lvl, txt, ss, se, par in full
            ]}))
            return 0
        print(f"{args.doc}: {len(rows)} blocks (file_uuid={fu})")
        for bid, kind, level, text in rows:
            lvl = f"L{level}" if level else "  "
            print(f"  {bid}  {kind:8s} {lvl} {text}")
    finally:
        store.close()
    if args.path:
        print(f"--path {args.path!r}: not implemented yet (Phase 2 parser layer)")
    return 0


def cmd_search(args) -> int:
    """Scoped text search over stored blocks (token-saving for the skill).

    ``nlm search <pattern> [--kind K] [--limit N] [--json]`` returns the blocks
    whose (case-insensitive) text matches ``<pattern>``, joined to their file —
    so an agent can "find blocks mentioning X" and only load those, not the
    whole doc. The graph must already be indexed (see ``index``).
    """
    store = GraphStore(args.db)
    try:
        rows = store.search_blocks(
            args.pattern,
            kind=args.kind,
            limit=args.limit,
        )
    finally:
        store.close()
    if args.json:
        print(json.dumps({
            "db": str(args.db),
            "pattern": args.pattern,
            "kind": args.kind,
            "limit": args.limit,
            "count": len(rows),
            "matches": [
                {"doc": doc, "block_id": bid, "kind": k, "level": lvl,
                 "text": txt, "span_start": ss, "span_end": se}
                for doc, bid, k, lvl, txt, ss, se in rows
            ],
        }))
        return 0
    if not rows:
        print(f"no blocks matching {args.pattern!r} in {args.db}")
        return 0
    print(f"{len(rows)} block(s) match {args.pattern!r}:")
    for doc, bid, k, lvl, txt, ss, se in rows:
        snippet = (txt or "").strip().replace("\n", " ")[:60]
        print(f"  {doc:20s} {bid} {k:8s} {snippet}")
    return 0


def cmd_graph(args) -> int:
    store = GraphStore(args.db)
    try:
        rows = store.list_files()
        limit = args.top_canonical or 10
        if args.json:
            top = store.top_canonical(limit=limit)
            concepts = store.top_concepts(limit=limit)
            print(json.dumps({
                "db": str(args.db),
                "files": [{"name": n, "file_uuid": u} for n, u in rows],
                "top_canonical": [{"node_id": nid, "label": lab, "refs": n}
                                  for nid, lab, n in top],
                # what recurs across notes — the view top_canonical cannot give
                "top_concepts": [{"node_id": nid, "label": lab,
                                  "n_files": nf, "n_blocks": nb}
                                 for nid, lab, nf, nb in concepts],
            }))
            return 0
        print(f"store {args.db}: {len(rows)} file(s)")
        for name, fu in rows:
            print(f"  {name}  ({fu})")
        print()
        concepts = store.top_concepts(limit=limit)
        print("shared concepts (words appearing in 2+ notes):")
        if not concepts:
            print("  (none yet — index more than one note)")
        for nid, label, nf, nb in concepts:
            print(f"  {label:<20} {nf} notes, {nb} blocks")
    finally:
        store.close()
    return 0


# Concept queries need the token layer, which only ``--pos``/``--parse`` write.
# Without it they'd return "nothing shared" — indistinguishable from a genuinely
# unconnected vault — so say which it is.
_NO_TOKENS = (
    "this store has no concept (token) layer — reindex with --pos to enable "
    "concept queries:\n"
    "  python -m interface.cli --db <db> index <path> --pos"
)


def _outline(path: Path) -> dict:
    """A document's shape without its body: sections, sizes, line ranges.

    The gap this fills: every existing way to "read" a document through this tool
    costs *more* than the file. On a 20.5k-token note, ``query <doc> --json`` is
    34.8k and ``view --json`` is 56.9k, because both carry every block's text.
    ``search`` is no help either — it needs a word you only know once you've read
    the thing.

    So: no body text at all. Just what a reader needs to decide *where to look* —
    each section's title, how big it is, and what it's made of. 609 tokens for
    that 20.5k note (2%).

    Ranges are **line numbers**, not the char spans the graph uses, because the
    caller's next move is `Read(file, offset, limit)` and that speaks lines.
    Handing over char offsets would make every caller redo this conversion, and
    some would get it wrong.

    In-memory like ``view``: no store, no ``--db``, no indexing first. "Let me
    look at this file cheaply" shouldn't require a database.
    """
    fm = FileMeta(file_path=path, file_name=path.name)
    ctx, _ = process(fm)
    body = path.read_text(encoding="utf-8", errors="replace")
    # char offset -> 1-based line, by counting newlines before it
    starts = [0]
    for i, ch in enumerate(body):
        if ch == "\n":
            starts.append(i + 1)
    def line_of(off: int) -> int:
        lo, hi = 0, len(starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if starts[mid] <= off:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    blocks = sorted(ctx.blocks, key=lambda b: b.span[0])
    sections: list[dict] = []
    cur: dict | None = None
    for b in blocks:
        if b.kind is BlockKind.HEADING:
            cur = {
                "block_id": str(b.block_id),
                "level": b.level,
                "title": b.text.strip(),
                "_start": b.span[0], "_end": b.span[1],
                "kinds": {},
            }
            sections.append(cur)
            continue
        if cur is None:
            # prose before the first heading is a section with no title
            cur = {"block_id": None, "level": 0, "title": "",
                   "_start": b.span[0], "_end": b.span[1], "kinds": {}}
            sections.append(cur)
        cur["_end"] = max(cur["_end"], b.span[1])
        k = b.kind.value
        cur["kinds"][k] = cur["kinds"].get(k, 0) + 1
    for s in sections:
        s["chars"] = s["_end"] - s["_start"]
        s["from_line"] = line_of(s["_start"])
        s["to_line"] = line_of(max(s["_start"], s["_end"] - 1))
        del s["_start"], s["_end"]
    return {
        "doc": path.name,
        "file_path": str(path),
        "n_chars": len(body),
        "n_lines": len(starts),
        "n_sections": len(sections),
        "sections": sections,
    }


def cmd_outline(args) -> int:
    path = Path(args.file)
    if not path.exists():
        print(f"no such file: {path}", file=sys.stderr)
        return 1
    o = _outline(path)
    if args.json:
        print(json.dumps(o, ensure_ascii=False))
        return 0
    print(f"{o['doc']} — {o['n_chars']:,} chars, {o['n_lines']:,} lines, "
          f"{o['n_sections']} section(s)")
    for s in o["sections"]:
        made = " ".join(f"{v} {k}" for k, v in sorted(s["kinds"].items()))
        indent = "  " * max(0, (s["level"] or 1) - 1)
        title = s["title"] or "(opening)"
        print(f"  L{s['from_line']:>4}-{s['to_line']:<4} {s['chars']:>7,}c  "
              f"{indent}{title[:44]:<44} {made}")
    return 0


def cmd_cards(args) -> int:
    """One card per note — read this instead of the vault, to decide where to look."""
    store = GraphStore(args.db)
    try:
        if not store.has_concepts():
            print(_NO_TOKENS, file=sys.stderr)
            return 1
        cards = store.doc_cards(keywords=args.keywords)
        if args.json:
            print(json.dumps({"db": str(args.db), "n": len(cards),
                              "cards": cards}, ensure_ascii=False))
            return 0
        print(f"{len(cards)} note(s)")
        for c in cards:
            hs = " › ".join(h["title"] for h in c["headings"][:3])
            print(f"\n  {c['name']}  ({c['n_chars']:,}c, {c['lang']})")
            if hs:
                print(f"    {hs}")
            if c["keywords"]:
                print(f"    ~ {', '.join(c['keywords'])}")
    finally:
        store.close()
    return 0


def cmd_bridge(args) -> int:
    """Use case 1 — notes this one should probably link to.

    A "bridge" is a note sharing canonical concepts with the target that no
    wikilink connects yet. Obsidian's own unlinked-mentions matches literal
    strings, so it cannot see 문어는/문어를 as one mention; this compares
    canonical token nodes, which 조사 stripping already collapsed.
    """
    store = GraphStore(args.db)
    try:
        fu = store.file_uuid_for(args.doc)
        if fu is None:
            print(f"not indexed: {args.doc}", file=sys.stderr)
            return 1
        if not store.has_concepts():
            print(_NO_TOKENS, file=sys.stderr)
            return 1
        kw = {} if args.min_score is None else {"min_score": args.min_score}
        rows = store.bridges(fu, limit=args.limit, min_shared=args.min_shared, **kw)
        if args.json:
            print(json.dumps({"doc": args.doc, "file_uuid": fu, "bridges": rows}))
            return 0
        if not rows:
            print(f"{args.doc}: no unlinked notes share concepts with this one")
            return 0
        print(f"{args.doc} — 아직 링크되지 않았지만 개념을 공유하는 노트:")
        for r in rows:
            shared = ", ".join(r["shared"][:6])
            more = f" (+{r['n_shared'] - 6})" if r["n_shared"] > 6 else ""
            print(f"  {r['link']:<24} 연관 {r['score']:.2f}  {shared}{more}")
    finally:
        store.close()
    return 0


def cmd_vault(args) -> int:
    """Scoped, hierarchical vault graph — notes + folders + links + bridges.

    Unlike Obsidian's flat whole-vault graph this keeps the folder hierarchy and
    lets you narrow to a folder subtree or to the notes discussing one concept.
    """
    store = GraphStore(args.db)
    try:
        if not store.has_concepts():
            print(_NO_TOKENS, file=sys.stderr)
            return 1
        g = store.vault_graph(
            root=args.root, folder=args.folder, concept=args.concept,
            min_shared=args.min_shared,
        )
        if args.json:
            print(json.dumps(g))
            return 0
        sc = g["scope"]
        label = f"{sc['kind']}={sc['value']}" if sc["value"] else "vault"
        print(f"scope: {label} — {len(g['notes'])} note(s), "
              f"{len(g['links'])} link(s), {len(g['bridges'])} suggested")
        by_folder: dict[str, list[dict]] = {}
        for n in g["notes"]:
            by_folder.setdefault(n["folder"], []).append(n)
        for f in sorted(by_folder):
            print(f"\n  {f or '(root)'}/")
            for n in by_folder[f]:
                print(f"    {n['name']:<26} blocks={n['n_blocks']:<4} "
                      f"{', '.join(n['concepts'][:4])}")
        if g["bridges"]:
            names = {n["file_uuid"]: n["name"] for n in g["notes"]}
            print("\n  제안 (개념 공유, 링크 없음):")
            for b in g["bridges"][:10]:
                print(f"    {names.get(b['a'],'?')} ↔ {names.get(b['b'],'?')}"
                      f"  연관 {b['score']:.2f}  {', '.join(b['shared'][:5])}")
    finally:
        store.close()
    return 0


def cmd_audit(args) -> int:
    """Use case 3 — vault health: orphan notes + strongest unlinked pairs."""
    store = GraphStore(args.db)
    try:
        if not store.has_concepts():
            print(_NO_TOKENS, file=sys.stderr)
            return 1
        rep = store.audit(min_shared=args.min_shared, limit=args.limit)
        if args.json:
            print(json.dumps(rep))
            return 0
        print(f"vault: {rep['n_files']} note(s), {rep['n_links']} wikilink(s)")
        print(f"\norphans (링크가 하나도 없는 노트) — {len(rep['orphans'])}:")
        if not rep["orphans"]:
            print("  (없음)")
        for o in rep["orphans"]:
            print(f"  {o['name']}")
        pairs = rep["unlinked_pairs"]
        print(f"\n개념을 공유하지만 링크가 없는 노트 쌍 — {len(pairs)}:")
        if not pairs:
            print("  (없음)")
        for p in pairs:
            shared = ", ".join(p["shared"][:6])
            more = f" (+{p['n_shared'] - 6})" if p["n_shared"] > 6 else ""
            print(f"  {p['a']} ↔ {p['b']}  연관 {p['score']:.2f}  {shared}{more}")
    finally:
        store.close()
    return 0


def _view_snapshot(path: Path, *, parse: bool) -> dict:
    """Build the parse-graph JSON snapshot for one file, in-memory (no store).

    Reuses :func:`src.context.process` and the linker/parser output already on
    the returned :class:`Context`; projection-only. The Obsidian plugin's graph
    view + decorations consume this. Per-block ``phrases`` are the phrase nodes
    attributed to that block (``ref_block_id``), with their head token's text via
    the ``head`` edge; per-block ``deps`` are the file's ``dep:*`` edges whose
    head token belongs to that block (token→block map from ``token`` edges).
    """
    fm = FileMeta(file_path=path, file_name=path.name)
    ctx, stats = process(fm, parse=parse)

    token_text: dict[str, str] = {}
    for n in ctx.nodes:
        if n.kind == "token":
            token_text[n.node_id] = n.label

    # canonical token id -> the block(s) that reference it (token edge)
    token_to_block: dict[str, str] = {}
    for e in ctx.edges:
        if e.label == "token":
            token_to_block[e.dst] = e.src

    # phrase id -> head token canonical id (phrase -> token)
    phrase_head: dict[str, str] = {}
    # phrase id -> child phrase ids (parent phrase -> child phrase); plus
    # block_id -> top phrase id (block contains top phrase)
    block_top_phrase: dict[str, str] = {}
    for e in ctx.edges:
        if e.label == "head":
            phrase_head[e.src] = e.dst

    # blocks (ctx.blocks) with their attributed phrases + deps
    blocks_out: list[dict] = []
    for b in ctx.blocks:
        bid = str(b.block_id)
        phrases = []
        for n in ctx.nodes:
            if n.kind == "phrase" and n.ref_block_id is not None and str(n.ref_block_id) == bid:
                head_tok = phrase_head.get(n.node_id)
                phrases.append({
                    "id": n.node_id,
                    "label": n.label,
                    "head_text": token_text.get(head_tok, ""),
                })
        deps: list[dict] = []
        for e in ctx.edges:
            if e.label.startswith("dep:"):
                # attribute by head endpoint's block (arc head token belongs to
                # this sentence's block); only one file in play, so unique.
                blk = token_to_block.get(e.src)
                if blk == bid:
                    deps.append({
                        "src_text": token_text.get(e.src, ""),
                        "dst_text": token_text.get(e.dst, ""),
                        "label": e.label,
                    })
        blocks_out.append({
            "block_id": bid,
            "kind": b.kind.value,
            "level": b.level,
            "text": b.text,
            "span": [b.span[0], b.span[1]],
            "parent_block_id": str(b.parent_block_id) if b.parent_block_id else None,
            "n_phrase": len(phrases),
            "n_dep": len(deps),
            "phrases": phrases,
            "deps": deps,
        })

    return {
        "doc": fm.file_name,
        "file_path": str(fm.file_path),
        "file_uuid": str(fm.unique_id),
        "schema_version": ctx.schema_version,
        "parse": parse,
        "pos": stats.get("pos", False),
        "stats": {
            "n_blocks": stats["n_blocks"],
            "n_nodes": stats["n_nodes"],
            "n_edges": stats["n_edges"],
            "n_phrase_nodes": stats.get("n_phrase_nodes", 0),
            "n_dep_edges": stats.get("n_dep_edges", 0),
            "parsed_sentences": stats.get("parsed_sentences", 0),
            "skipped_sentences": stats.get("skipped_sentences", 0),
            # honest constituency coverage: ``parsed_sentences`` is satisfied by
            # the dependency parser alone, so it hides CKY finding nothing.
            "cky_trees": stats.get("cky_trees", 0),
            "cky_coverage": stats.get("cky_coverage", 0.0),
            # sentences the EN-only parsers were never run on (non-EN script)
            "skipped_lang": stats.get("skipped_lang", 0),
            # real canonical-token dedup (absent unless pos/parse ran)
            "token_raw": stats.get("token_raw", 0),
            "token_unique": stats.get("token_unique", 0),
            "token_saved": stats.get("token_saved", 0),
            "ratio_token": stats.get("ratio_token", 0.0),
            "lang": ctx.file.lang or "",
        },
        "blocks": blocks_out,
        "nodes": [{"id": n.node_id, "kind": n.kind, "label": n.label,
                   "ref_block_id": str(n.ref_block_id) if n.ref_block_id else None}
                  for n in ctx.nodes],
        "edges": [{"src": e.src, "dst": e.dst, "label": e.label}
                  for e in ctx.edges],
    }


def cmd_view(args) -> int:
    path = Path(args.file)
    if not path.exists():
        print(f"no such file: {path}", file=sys.stderr)
        return 1
    snap = _view_snapshot(path, parse=args.parse)
    print(json.dumps(snap))
    return 0


def cmd_watch(args) -> int:
    target = Path(args.dir)
    store = GraphStore(args.db)

    def on_change(p: Path) -> None:
        print(f"[watch] re-indexing {p.name}")
        _index_one(store, p, pos=args.pos, parse=args.parse)

    w = Watcher(target, on_change, debounce=args.debounce)
    print(f"watching {target} (debounce={args.debounce}s) — Ctrl-C to stop")
    try:
        w.run(interval=args.interval)
    finally:
        store.close()
    return 0


def cmd_purge(args) -> int:
    store = GraphStore(args.db)
    try:
        n = store.purge_file(args.doc)
        if args.json:
            print(json.dumps({"ok": True, "doc": args.doc, "purged": n, "db": str(args.db)}))
            return 0
        print(f"purged {n} file(s) named {args.doc!r} from {args.db}")
    finally:
        store.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nlm", description="NL-Miner — markdown→graph miner")
    p.add_argument("--db", default=str(DEFAULT_DB), help=f"SQLite path (default: {DEFAULT_DB})")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("index", help="index a file or directory")
    sp.add_argument("path")
    sp.add_argument("--parse", action="store_true", help="run Phase 2 CKY + arc-eager parsers")
    sp.add_argument("--pos", action="store_true", help="attach POS (per-token canonical nodes)")
    sp.add_argument("--force", action="store_true",
                    help="reprocess every file, ignoring mtime — use after "
                         "upgrading, since the tokenizer can change under a file "
                         "that did not")
    sp.add_argument("--json", action="store_true", help="emit one JSON summary object")
    sp.set_defaults(func=cmd_index)

    sp = sub.add_parser("query", help="query an indexed document")
    sp.add_argument("doc")
    sp.add_argument("--block", help="block id to list neighbours of")
    sp.add_argument("--tokens", action="store_true",
                    help="include every word edge (a block links to each distinct "
                         "word it contains — hundreds of rows; summarised by default)")
    sp.add_argument("--path", help="path pattern (Phase 2, e.g. NP/VP)")
    sp.add_argument("--json", action="store_true", help="emit JSON instead of text")
    sp.set_defaults(func=cmd_query)

    sp = sub.add_parser(
        "graph", help="store summary + concepts shared across notes"
    )
    sp.add_argument("--top-canonical", type=int, default=10,
                    help="max rows (name kept for compatibility; the text view "
                         "lists shared concepts, not dup-block groups)")
    sp.add_argument("--json", action="store_true", help="emit JSON instead of text")
    sp.set_defaults(func=cmd_graph)

    sp = sub.add_parser(
        "search", help="scoped text search over indexed blocks (find blocks mentioning X)"
    )
    sp.add_argument("pattern", help="case-insensitive substring to match in block text")
    sp.add_argument("--kind", help="restrict to a block kind (heading|para|sentence|list|quote|code|table)")
    # 20, not 100. The point of this command is to answer without loading the
    # documents, and on a real vault "search CI" matched 100 blocks averaging 774
    # characters — 28k tokens, only 20% less than just reading the 8 files. At 20
    # it is 89%, at 10 it is 97%. A default that defeats the command's reason to
    # exist is a bug, not a preference; raise it explicitly when you want a census.
    sp.add_argument("--limit", type=int, default=20,
                    help="max matches (default 20; raise it only if you truly "
                         "need every hit — each match carries its whole block)")
    sp.add_argument("--json", action="store_true", help="emit JSON instead of text")
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser(
        "cards",
        help="one card per note (headings + distinctive keywords, no body) — "
             "the cheap map of a vault you haven't read",
    )
    sp.add_argument("--keywords", type=int, default=8)
    sp.add_argument("--json", action="store_true", help="emit JSON instead of text")
    sp.set_defaults(func=cmd_cards)

    sp = sub.add_parser(
        "outline",
        help="a document's shape (sections + line ranges) without its body — "
             "read this before reading the file",
    )
    sp.add_argument("file")
    sp.add_argument("--json", action="store_true", help="emit JSON instead of text")
    sp.set_defaults(func=cmd_outline)

    sp = sub.add_parser(
        "bridge",
        help="notes sharing concepts with DOC that aren't linked to it yet",
    )
    sp.add_argument("doc", help="indexed file name, e.g. octopus.md")
    sp.add_argument("--limit", type=int, default=20)
    sp.add_argument("--min-shared", type=int, default=2,
                    help="minimum *informative* shared concepts (default 2); "
                         "one word is never enough evidence")
    sp.add_argument("--min-score", type=float, default=None,
                    help="minimum relatedness, cosine 0..1 (default 0.15)")
    sp.add_argument("--json", action="store_true", help="emit JSON instead of text")
    sp.set_defaults(func=cmd_bridge)

    sp = sub.add_parser(
        "vault",
        help="scoped hierarchical vault graph (notes + folders + links + bridges)",
    )
    sp.add_argument("--root", help="vault root, for relative folder paths "
                                   "(default: common ancestor of indexed notes)")
    sp.add_argument("--folder", help="restrict to this folder subtree")
    sp.add_argument("--concept", help="restrict to notes discussing this concept")
    sp.add_argument("--min-shared", type=int, default=1)
    sp.add_argument("--json", action="store_true", help="emit JSON instead of text")
    sp.set_defaults(func=cmd_vault)

    sp = sub.add_parser(
        "audit", help="vault health: orphan notes + unlinked notes that share concepts"
    )
    sp.add_argument("--limit", type=int, default=20)
    sp.add_argument("--min-shared", type=int, default=2,
                    help="minimum shared concepts for an unlinked pair (default 2)")
    sp.add_argument("--json", action="store_true", help="emit JSON instead of text")
    sp.set_defaults(func=cmd_audit)

    sp = sub.add_parser("watch", help="watch a directory and live-reindex")
    sp.add_argument("dir")
    sp.add_argument("--interval", type=float, default=0.5)
    sp.add_argument("--debounce", type=float, default=0.3)
    sp.add_argument("--parse", action="store_true")
    sp.add_argument("--pos", action="store_true")
    sp.set_defaults(func=cmd_watch)

    sp = sub.add_parser("purge", help="remove a document from the store")
    sp.add_argument("doc")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_purge)

    sp = sub.add_parser("view", help="emit the parse-graph JSON snapshot of one file")
    sp.add_argument("file")
    sp.add_argument("--parse", action="store_true")
    sp.add_argument("--json", action="store_true",
                    help="(view is JSON-native; flag kept for interface symmetry)")
    sp.set_defaults(func=cmd_view)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
