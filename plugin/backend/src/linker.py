"""Phase 1/2 linker — rule-based edges + block-canonical dedup (token savings)
plus optional POS-attached per-token canonical nodes.

Design ref: phase1_blocks_links.md §2 (algorithms + trade-off tables), §5;
phase2_parsers.md §2.6 (POS attachment rehashes the per-token canonical id).

Token-saving mechanism (§2.6): two near-duplicate blocks collapse onto ONE
shared canonical node, so the duplicate's tokens are emitted once → ``saved``
counts those. Near-dup detection is shingling + MinHash (§2.4); a Jaccard
estimate ≥ ``DUP_THRESHOLD`` joins a group.

Phase 2 layer (§2.6, §3.5): with ``pos=True`` the linker additionally POS-tags
each block (via :mod:`src.postagger`) and collapses every ``(word, pos)`` onto
one ``canonical_id(word, pos)`` node of kind ``"token"``, each block linking to
its distinct tokens with ``token`` edges. POS rehashes the id away from the
Phase-1 ``"NA"`` sentinel → ``schema_version`` moves ``"0.1"`` → ``"0.2"``.
The two canonical schemes stay distinct: block-canonical (dup-group, kind
``"canonical"``) and per-token canonical (kind ``"token"``) never mix, and
``pos=False`` (the default) reproduces Phase 1 byte-for-byte.
"""
from __future__ import annotations

import re
from hashlib import sha256
from uuid import UUID

from .postagger import get_adapter as _get_morph_adapter
from .schema import Block, BlockKind, GraphEdge, GraphNode, canonical_id

# --- tunables (phase1 §3.6: "자위: shingle k와 θ 튜닝"; values are EN defaults) ---
SHINGLE_K = 5
MINHASH_N = 128
DUP_THRESHOLD = 0.8          # est. Jaccard to join a near-duplicate group
VOCAB_THRESHOLD = 0.5        # token-set Jaccard for a sim-vocab edge
VOCAB_MIN_TOKENS = 4        # ignore tiny blocks (function-word collisions)

CJK_RE = re.compile(r"[㐀-鿿豈-﫿぀-ヿ가-힯]")
WORD_RE = re.compile(r"\w+", re.UNICODE)
WIKI_RE = re.compile(r"\[\[([^\]]+)\]\]")
INLINE_STRIP_RE = re.compile(r"[`*_~\[\]()#>]")
DEF_RE = re.compile(r"^\s*(?:def|class)\s+(\w+)\b|^\s*(\w+)\s*=", re.MULTILINE)
IMPORT_RE = re.compile(r"^\s*(?:import\s+(\w+)|from\s+\S+\s+import\s+(\w+))", re.MULTILINE)
CALL_RE = re.compile(r"\b(\w+)\s*\(")
SENT_SPLIT_RE = re.compile(r"(?<=[.!?。！？])\s+")


# ----------------------------------------------------------------- tokenisation


def normalize(text: str) -> str:
    """Strip inline markdown noise before tokenising (§2.1 '정제만')."""
    return INLINE_STRIP_RE.sub(" ", text)


def tokenize(text: str) -> list[str]:
    """Word tokens; CJK runs fall back to per-character (§2.3/§3.4)."""
    t = normalize(text).lower()
    runs = WORD_RE.findall(t)
    if not CJK_RE.search(t):
        return runs
    out: list[str] = []
    for run in runs:
        if CJK_RE.search(run):
            out.extend(list(run))
        else:
            out.append(run)
    return out


def shingles(tokens: list[str], k: int = SHINGLE_K) -> list[str]:
    if len(tokens) < k:
        return [" ".join(tokens)] if tokens else []
    return [" ".join(tokens[i : i + k]) for i in range(len(tokens) - k + 1)]


def _hash_components(shingle: str, n: int) -> list[int]:
    comp: list[int] = []
    salt = 0
    while len(comp) < n:
        digest = sha256(f"{salt}|{shingle}".encode("utf-8")).digest()
        for j in range(8):  # 32 bytes → eight uint32
            comp.append(int.from_bytes(digest[j * 4 : j * 4 + 4], "big"))
            if len(comp) >= n:
                break
        salt += 1
    return comp


def minhash_signature(tokens: list[str], n: int = MINHASH_N) -> list[int]:
    sh = shingles(tokens)
    if not sh:
        return [0] * n
    sig: list[int | None] = [None] * n
    for s in sh:
        comp = _hash_components(s, n)
        for i in range(n):
            if sig[i] is None or comp[i] < sig[i]:
                sig[i] = comp[i]
    return [v or 0 for v in sig]


def jaccard_estimate(a: list[int], b: list[int]) -> float:
    if not a or not b:
        return 0.0
    eq = sum(1 for x, y in zip(a, b) if x == y)
    return eq / len(a)


def _canon_id(sig: list[int]) -> str:
    return sha256(repr(sig).encode("utf-8")).hexdigest()[:16]


# ----------------------------------------------------------------- grouping


def _dup_groups(signatures: list[list[int]]) -> list[list[int]]:
    """Representative-based clustering (single-linkage via rep). Returns
    a list of groups, each a list of block indexes."""
    groups: list[list[list[int]]] = []  # members are [rep_sig, [indices]]
    out: list[list[int]] = []
    for idx, sig in enumerate(signatures):
        placed = False
        for rep_sig, members in groups:
            if jaccard_estimate(sig, rep_sig) >= DUP_THRESHOLD:
                members.append(idx)
                placed = True
                break
        if not placed:
            groups.append([sig, [idx]])
    for _, members in groups:
        out.append(members)
    return out


# ----------------------------------------------------------------- rule edges


def _block_node(b: Block) -> GraphNode:
    preview = b.text.strip().splitlines()[0][:60] if b.text.strip() else b.kind.value
    return GraphNode(node_id=str(b.block_id), kind="block", label=preview, ref_block_id=b.block_id)


def _edges_contains(blocks: list[Block]) -> list[GraphEdge]:
    edges: list[GraphEdge] = []
    for b in blocks:
        if b.parent_block_id is not None:
            edges.append(GraphEdge(src=str(b.parent_block_id), dst=str(b.block_id), label="contains"))
    return edges


def _edges_ref_to(blocks: list[Block]) -> list[GraphEdge]:
    """``[[wikilink]]`` → enclosing block links to the named heading block."""
    edges: list[GraphEdge] = []
    heading_by_text: dict[str, str] = {}
    for b in blocks:
        if b.kind is BlockKind.HEADING:
            heading_by_text[b.text.strip().lower()] = str(b.block_id)
    for b in blocks:
        for target in WIKI_RE.findall(b.text):
            bid = heading_by_text.get(target.strip().lower())
            if bid:  # skip self-reference
                if bid == str(b.block_id):
                    continue
                edges.append(GraphEdge(src=str(b.block_id), dst=bid, label="ref-to"))
    return edges


def _dedup_edges(edges: list[GraphEdge]) -> list[GraphEdge]:
    """Drop duplicate ``(src, dst, label)`` edges, preserving first-seen order.

    Intra-file a linker rule can emit the same edge twice (e.g. a code block
    referencing two names defined in the same target block → two ``code-ref``
    edges with identical endpoints). The store's edge PK would then
    ``IntegrityError``, so dedup here.
    """
    seen: set[tuple[str, str, str]] = set()
    out: list[GraphEdge] = []
    for e in edges:
        key = (e.src, e.dst, e.label)
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def _edges_code_ref(blocks: list[Block]) -> list[GraphEdge]:
    """Within-file code cross-links: a block that imports/calls a name
    defined in another code block gets a ``code-ref`` edge to it."""
    edges: list[GraphEdge] = []
    code = [b for b in blocks if b.kind is BlockKind.CODE]
    defs: dict[str, str] = {}  # name → block_id
    for b in code:
        for m in DEF_RE.finditer(b.text):
            name = m.group(1) or m.group(2)
            if name:
                defs.setdefault(name, str(b.block_id))
    for b in code:
        refs: list[str] = []
        for m in IMPORT_RE.finditer(b.text):
            refs.append(m.group(1) or m.group(2) or "")
        for m in CALL_RE.finditer(b.text):
            refs.append(m.group(1))
        for name in {r for r in refs if r}:
            tgt = defs.get(name)
            if tgt and tgt != str(b.block_id):
                edges.append(GraphEdge(src=str(b.block_id), dst=tgt, label="code-ref"))
    return edges


def _edges_sim_vocab(blocks: list[Block]) -> list[GraphEdge]:
    """Shared-vocabulary link (ROADMAP '공유 어휘'): token-set Jaccard ≥ θ."""
    tokenized = []
    for b in blocks:
        toks = tokenize(b.text)
        if len(toks) >= VOCAB_MIN_TOKENS and b.kind is not BlockKind.CODE:
            tokenized.append((b, set(toks)))
        else:
            tokenized.append((b, None))
    edges: list[GraphEdge] = []
    for i in range(len(tokenized)):
        bi, si = tokenized[i]
        if not si:
            continue
        for j in range(i + 1, len(tokenized)):
            bj, sj = tokenized[j]
            if not sj or bi.block_id == bj.block_id:
                continue
            inter = len(si & sj)
            if inter == 0:
                continue
            jac = inter / len(si | sj)
            if jac >= VOCAB_THRESHOLD:
                edges.append(GraphEdge(src=str(bi.block_id), dst=str(bj.block_id), label="sim-vocab"))
    return edges


def _edges_token(blocks: list[Block], adapter) -> tuple[list[GraphNode], list[GraphEdge], int, int]:
    """Per-token canonical nodes (Phase 2 §2.6).

    POS-attach each block's tokens and collapse every ``(word, pos)`` onto ONE
    ``canonical_id(word, pos)`` node (kind ``"token"``); each block gets a
    ``token`` edge to each *distinct* token it contains. The savings =
    occurrences minus distinct tokens: repeats reuse the shared node instead
    of re-emitting, exactly mirroring the block-level dedup but at token
    granularity.

    Returns ``(token_nodes, token_edges, n_occurrences, n_distinct)``.
    """
    node_by_id: dict[str, GraphNode] = {}
    edges: list[GraphEdge] = []
    raw = 0
    for b in blocks:
        toks = adapter.tag(b.text)
        raw += len(toks)
        local: set[str] = set()  # distinct token ids *within* this block
        for t in toks:
            nid = canonical_id(t.text, t.pos)
            local.add(nid)
            if nid not in node_by_id:
                # ``nid`` is a function of (text, pos), so every occurrence that
                # lands on this node carries the same lemma — recording it once
                # is well-defined.
                node_by_id[nid] = GraphNode(
                    node_id=nid, kind="token", label=t.text,
                    ref_block_id=b.block_id, lemma=t.lemma,
                )
        for nid in local:
            edges.append(GraphEdge(src=str(b.block_id), dst=nid, label="token"))
    return list(node_by_id.values()), edges, raw, len(node_by_id)


# ----------------------------------------------------------------- parser layer (Phase 2)
#
# parse=True consumes both CKY (constituency) and arc-eager (dependency) output:
#   * CKY trees  -> phrase nodes (kind="phrase", canonical_id(surface,label)) +
#     `contains` edges (block -> top phrase, parent phrase -> child phrase) +
#     `head`     edges (phrase -> its head token's canonical node)
#   * arc-eager arcs -> `dep:<rel>` edges (head token -> dep token canonical node)
# Phrase canonical ids mirror the token scheme (ARCHITECTURE §2.3:
# SHA256(label+POS)), so identical phrases across blocks/files collapse onto a
# single surfaced phrase node — the graphify token-saving idea, lifted from
# per-token to per-phrase granularity. Sentences too long for the O(n^3) CKY
# chart are skipped (parsers return None, §3.1): the graph simply falls back to
# block + token structure for them — graceful, never a crash.


def _emit_phrase_tree(node, block_id, node_by_id, phrase_nodes, edges, head_leaf):
    """Walk a CKY subtree, emitting phrase (non-leaf) nodes + their
    ``contains``/``head`` edges. Returns that node's canonical id.

    Leaves and binarisation synthetics (``<L~k>``) are transparent: we recurse
    into them but don't surface a phrase node, so artificial labels never reach
    the graph (§3.2). """
    if node.is_leaf:
        return None
    if node.label.startswith("<"):        # synthetic: transparent passthrough
        last = None
        for c in node.children:
            last = _emit_phrase_tree(c, block_id, node_by_id, phrase_nodes,
                                     edges, head_leaf)
        return last
    nid = canonical_id(node.surface, node.label)
    if nid not in node_by_id:
        node_by_id[nid] = GraphNode(
            node_id=nid, kind="phrase", label=node.label, ref_block_id=block_id
        )
        phrase_nodes.append(node_by_id[nid])
    head = head_leaf(node)
    if head is not None and head.token is not None:
        edges.append(GraphEdge(
            src=nid, dst=canonical_id(head.token.text, head.token.pos), label="head"
        ))
    child_ids = []
    for c in node.children:
        cid = _emit_phrase_tree(c, block_id, node_by_id, phrase_nodes, edges,
                                head_leaf)
        if cid is not None:
            child_ids.append(cid)
    for cid in child_ids:
        edges.append(GraphEdge(src=nid, dst=cid, label="contains"))
    return nid


def _edges_parse(blocks, adapter, grammar):
    """Run constituency + dependency parsers over each prose block; return
    ``(phrase_nodes, edges, parsed, skipped, n_dep, skipped_lang, cky_trees)``.

    CODE blocks are not English prose and are left to the rule edges. Sentences
    over ``MAX_TOKENS`` have the parsers return None and are counted as skipped.

    Both parsers are English-only: ``grammar.cfg`` is an EN CFG, and the
    arc-eager rule tables key off EN POS classes. The rule tagger falls back to
    ``NN`` for anything it doesn't recognise, so on Korean *every* token comes
    back a noun and ``("N","N") -> compound`` then fires between every adjacent
    pair — yielding a chain of fabricated relations that looks exactly like a
    real parse. Gating per *sentence* (not per file) keeps an English quote in a
    Korean note parseable, and vice versa; non-EN sentences are reported
    separately as ``skipped_lang`` so the coverage numbers stay honest rather
    than being padded with garbage.
    """
    from . import cky, transition_parser
    from .preprocess import detect_lang

    phrase_nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    node_by_id: dict[str, GraphNode] = {}
    parsed = 0
    skipped = 0
    skipped_lang = 0
    cky_trees = 0
    n_dep = 0
    for b in blocks:
        if b.kind is BlockKind.CODE:
            continue
        for raw_sent in SENT_SPLIT_RE.split(b.text):
            sent = raw_sent.strip()
            if not sent:
                continue
            if detect_lang(sent) != "en":
                skipped_lang += 1
                continue
            toks = adapter.tag(sent)
            if not toks:
                continue
            tree = cky.parse(toks, grammar)
            arcs = transition_parser.parse(toks)
            if tree is None and arcs is None:
                skipped += 1
                continue
            parsed += 1
            if tree is not None:
                cky_trees += 1
                top = _emit_phrase_tree(tree, b.block_id, node_by_id,
                                        phrase_nodes, edges, cky.head_leaf)
                if top is not None:
                    # the block contains its sentence-level top phrase
                    edges.append(GraphEdge(
                        src=str(b.block_id), dst=top, label="contains"
                    ))
            if arcs:
                for arc in arcs:
                    if arc.head == 0:        # virtual ROOT: no token node to link
                        continue
                    h = toks[arc.head - 1]
                    d = toks[arc.dep - 1]
                    edges.append(GraphEdge(
                        src=canonical_id(h.text, h.pos),
                        dst=canonical_id(d.text, d.pos),
                        label=f"dep:{arc.label}",
                    ))
                    n_dep += 1
    return phrase_nodes, edges, parsed, skipped, n_dep, skipped_lang, cky_trees


# ----------------------------------------------------------------- entry point


def link(
    blocks: list[Block],
    file_uuid: UUID,
    *,
    pos: bool = False,
    adapter=None,
    parse: bool = False,
) -> tuple[list[GraphNode], list[GraphEdge], dict]:
    """Build the graph for one file's blocks.

    ``pos=False`` (default) reproduces Phase 1: block nodes + block-canonical
    dedup nodes + rule edges; ``stats`` reports only the block-level token
    savings (§2.6): ``raw_total_tokens, saved_tokens, ratio, dup_groups,
    n_blocks``.

    ``pos=True`` adds the Phase 2 per-token canonical layer (§2.6): each block
    is POS-tagged via ``adapter`` (default :func:`src.postagger.get_adapter`)
    and every ``(word, pos)`` collapses onto one ``canonical_id(word, pos)``
    node of kind ``"token"``, blocks linking to their distinct tokens with
    ``token`` edges. Extra stats: ``token_raw, token_unique, token_saved,
    ratio_token, n_token_nodes``. Token ids live in a disjoint space from
    block/canonical ids (a defensive dedup still keeps the node list id-clean
    for the store PK and an honest ``n_nodes``).
    """
    signatures = [minhash_signature(tokenize(b.text)) for b in blocks]
    groups = _dup_groups(signatures)

    nodes: list[GraphNode] = [_block_node(b) for b in blocks]
    edges = _dedup_edges([
        *_edges_contains(blocks),
        *_edges_ref_to(blocks),
        *_edges_code_ref(blocks),
        *_edges_sim_vocab(blocks),
    ])

    # Block-canonical (dedup) nodes for near-duplicate groups ≥ 2 members.
    # Metric (§2.6): raw_total counts every block's tokens; each duplicate
    # (non-representative) block's tokens are saved because they collapse onto
    # the shared canonical node and are not re-emitted.
    raw_total = sum(len(tokenize(b.text)) for b in blocks)
    saved = 0
    dup_group_count = 0
    for members in groups:
        if len(members) < 2:
            continue
        rep = blocks[members[0]]
        dup_group_count += 1
        for idx in members[1:]:  # duplicates
            saved += len(tokenize(blocks[idx].text))
        canon = _canon_id(signatures[members[0]])
        nodes.append(
            GraphNode(
                node_id=canon,
                kind="canonical",
                label=f"dup-group:{len(members)}",
                ref_block_id=rep.block_id,
            )
        )
        for idx in members:
            edges.append(GraphEdge(src=str(blocks[idx].block_id), dst=canon, label="rep"))

    # Final dedup so a file can never emit two identical (src,dst,label) edges
    # (defensive — rep edges are already unique, but wikilinks/code-refs above
    # can repeat; the store's edge PK would otherwise IntegrityError).
    edges = _dedup_edges(edges)

    ratio = saved / raw_total if raw_total else 0.0
    stats: dict = {
        "raw_total_tokens": raw_total,
        "saved_tokens": saved,
        "ratio": ratio,
        "dup_groups": dup_group_count,
        "n_blocks": len(blocks),
        "n_nodes": len(nodes),
        "n_edges": len(edges),
        "pos": False,
    }

    do_tokens = pos or parse
    if do_tokens:
        if adapter is None:
            adapter = _get_morph_adapter()
        tnodes, tedges, traw, tuniq = _edges_token(blocks, adapter)
        existing = {n.node_id for n in nodes}
        for n in tnodes:
            if n.node_id not in existing:
                nodes.append(n)
                existing.add(n.node_id)
        edges = _dedup_edges(edges + tedges)
        token_saved = traw - tuniq
        stats.update({
            "token_raw": traw,
            "token_unique": tuniq,
            "token_saved": token_saved,
            "ratio_token": token_saved / traw if traw else 0.0,
            "n_token_nodes": tuniq,
            "n_nodes": len(nodes),
            "n_edges": len(edges),
            # parse attaches POS too (phase2_parsers.md §2.6), so a parse run
            # reports the token layer on and bumps schema_version to "0.2".
            "pos": True,
        })

    if parse:
        from . import cky as _cky
        grammar = _cky.Grammar.load()
        (pnodes, pedges, parsed, skipped, n_dep,
         skipped_lang, cky_trees) = _edges_parse(blocks, adapter, grammar)
        existing = {n.node_id for n in nodes}
        for n in pnodes:
            if n.node_id not in existing:
                nodes.append(n)
                existing.add(n.node_id)
        edges = _dedup_edges(edges + pedges)
        # ``parsed_sentences`` counts a sentence where *either* parser produced
        # something, and arc-eager almost never fails — so it reads ~100% even
        # when CKY found no constituent at all. It is kept for contract
        # stability; ``cky_trees``/``cky_coverage`` are the honest constituency
        # numbers, and ``skipped_lang`` accounts for the non-EN sentences the
        # parsers were never run on.
        attempted = parsed + skipped
        stats.update({
            "parsed_sentences": parsed,
            "skipped_sentences": skipped,
            "skipped_lang": skipped_lang,
            "cky_trees": cky_trees,
            "cky_coverage": cky_trees / attempted if attempted else 0.0,
            "n_nodes": len(nodes),
            "n_edges": len(edges),
        })
        # Count dep + phrase from the FINAL deduped graph, not the pre-dedup
        # emission counters. Across sentences the same canonical dep edge
        # (e.g. ``dep:det`` between the shared "the"/"dog" token nodes) recurs
        # and is collapsed by ``_dedup_edges``; ``len(pnodes)``/``n_dep`` would
        # then overstate what the store actually persists. Mirror ``n_nodes``/
        # ``n_edges`` (already post-dedup) so every count lines up with the store.
        stats["n_dep_edges"] = sum(
            1 for e in edges if e.label.startswith("dep:")
        )
        stats["n_phrase_nodes"] = sum(1 for n in nodes if n.kind == "phrase")

    return nodes, edges, stats
