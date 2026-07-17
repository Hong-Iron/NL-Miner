"""Phase 2 — CKY constituency parser over an NF2 CFG (constituency trees).

Design refs: phase2_parsers.md §2.1–§2.2, CONSTITUENCY_DEPENDENCY §1/§4,
ARCHITECTURE §2.2.1. CKY is the chosen constituency parser: bottom-up chart
DP, restrictive NF2 (RHS ≤ 2) grammar to keep the chart small, and hand
weights that rank ambiguous cells — a ``score(rule, span)`` interface that a
learned PCFG can later replace without touching the algorithm (§2.2).

Property held across all of this:
* No external dependency (stdlib + ``src.postagger.Token``). "no-LM".
* Deterministic: same ``tokens`` → same ``Tree`` (canonical hashes elsewhere
  rely on exactly this — a different tree would change shared node ids).
* Graceful degradation: empty sentences, unknown structures, or sentences
  beyond :data:`MAX_TOKENS` return ``None``; the caller treats ``None`` as
  "no phrase structure for this sentence" and skips, never crashes (§3.1).

The grammar (:data:`GRAMMAR_PATH`) is loaded once and binarised on load so
every in-memory rule is NF2 even if the file lists a trinary right-hand side
(the binariser introduces synthetic ``<L~k>`` non-terminals, §3.2).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .postagger import Token

# ----------------------------------------------------------------- config

GRAMMAR_PATH = Path(__file__).resolve().parent.parent / "grammar.cfg"
START = "S"
# §3.1: CKY is O(n³); cap input length so a long line / CJK-char-tokenised
# sentence cannot blow the chart. Over the cap we return None (no phrase
# nodes) — the graph just falls back to block/token structure for it.
MAX_TOKENS = 30

ScoreFn = Callable[["Rule", tuple[int, int]], float]


# ----------------------------------------------------------------- models


@dataclass(frozen=True)
class Rule:
    lhs: str
    rhs: tuple[str, ...]
    weight: float = 1.0

    @property
    def arity(self) -> int:
        return len(self.rhs)


@dataclass
class Tree:
    """A parse tree node.

    ``span`` is a *token-index* half-open range ``[i, j)`` (0-based over the
    input token list), independent of char offsets in the source document.
    Preterminal leaves carry the originating :class:`~src.postagger.Token`;
    internal phrase nodes leave ``token`` as ``None`` and hold children.
    """

    label: str
    span: tuple[int, int]
    children: list["Tree"] = field(default_factory=list)
    token: Token | None = None

    @property
    def is_leaf(self) -> bool:
        return not self.children

    @property
    def surface(self) -> str:
        """Space-joined surface text of this constituent."""
        if self.is_leaf:
            return self.token.text if self.token is not None else ""
        return " ".join(c.surface for c in self.children).strip()


# ----------------------------------------------------------------- binarise


def binarize(rule: Rule) -> list[Rule]:
    """Split a rule whose RHS has >2 symbols into NF2 (left-merge binarisation).

    ``A -> B C D E`` becomes::

        A    -> B  <A~0>
        <A~0> -> C  <A~1>
        <A~1> -> D  E

    Synthetic ``<A~k>`` names mark expanded non-terminals as internal-only:
    :func:`linker._emit_phrase_tree` skips any label beginning ``<`` so they
    never surface as phrase nodes. The name is unique *within one rule's*
    expansion loop; two trinary expansions of the **same** lhs (the grammar's
    ``S -> NP VP PP`` and ``S -> S CC S`` both mint ``<S~0>``) therefore share a
    prefix, but their RHS tails (``VP PP`` vs ``CC S``) only fire under matching
    tokens and weight-rank settles the tie back to the canonical tree — so no
    synthetic changes the surfaced S structure for the tested sentences (§3.2).
    """
    syms = rule.rhs
    if len(syms) <= 2:
        return [rule]
    out: list[Rule] = []
    parent = rule.lhs
    for i in range(len(syms) - 2):
        nxt = f"<{rule.lhs}~{i}>"
        out.append(Rule(parent, (syms[i], nxt), rule.weight))
        parent = nxt
    out.append(Rule(parent, (syms[-2], syms[-1]), rule.weight))
    return out


# ----------------------------------------------------------------- grammar

_FLOAT_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


def _is_float(tok: str) -> bool:
    return bool(_FLOAT_RE.match(tok))


class Grammar:
    """An NF2 CFG: terminal-free phrase rules, plus a start symbol."""

    def __init__(self, rules: list[Rule], start: str = START) -> None:
        self.rules = rules
        self.start = start
        self.binary: dict[str, list[Rule]] = {}   # keyed by rhs[0]
        self.unary: list[Rule] = []
        for r in rules:
            if r.arity == 1:
                self.unary.append(r)
            elif r.arity == 2:
                self.binary.setdefault(r.rhs[0], []).append(r)
        # sanity: NF2 after binarisation
        assert all(r.arity <= 2 for r in rules), "grammar not NF2 after binarise"

    @classmethod
    def load(cls, path: str | Path = GRAMMAR_PATH, *, source: str | None = None) -> "Grammar":
        text = source if source is not None else Path(path).read_text(encoding="utf-8")
        rules: list[Rule] = []
        start = START
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            if line.upper().startswith("START "):
                start = line.split(None, 1)[1].strip().split()[0]
                continue
            if "->" not in line:
                continue
            lhs, rhs = line.split("->", 1)
            lhs = lhs.strip()
            sym = rhs.split()
            weight = 1.0
            if sym and _is_float(sym[-1]):
                weight = float(sym.pop())
            if not lhs or not sym:
                continue
            for r in binarize(Rule(lhs, tuple(sym), weight)):
                rules.append(r)
        return cls(rules, start)

    # a scorer default: sum-of-rule-weights (§2.2 — hand constants, spans ignored
    # by default; a PCFG can use span/coverage features here later)
    @staticmethod
    def default_score(rule: "Rule", _span: tuple[int, int]) -> float:
        return rule.weight


# ----------------------------------------------------------------- CKY core


def _add(cell, label: str, score: float, kind: str, *extra) -> bool:
    """Insert/replace a chart entry under best-score-wins.

    Returns True whenever a (better) entry was stored — fresh insert *or*
    score improvement — so the unary closure keeps propagating until a genuine
    fixpoint. (Cycles would diverge; the grammar keeps unary rules acyclic, so
    each label's best is monotonically reached in finitely many passes.)
    """
    cur = cell.get(label)
    if cur is None or score > cur[0] + 1e-12:
        cell[label] = (score, (kind, *extra))
        return True
    return False


def _unary_close(
    cell: dict, grammar: Grammar, score: ScoreFn, span: tuple[int, int]
) -> None:
    """Project unary rules (A -> B) inside one cell until the best per label is
    found. ``span`` is the cell's ``(i, j)`` so a span-aware scorer could later
    weight, e.g., shorter projections higher (PCFG swap-point, §2.2)."""
    changed = True
    while changed:
        changed = False
        for label, (sc, _) in list(cell.items()):
            for rule in grammar.unary:
                if rule.rhs[0] != label:
                    continue
                if _add(cell, rule.lhs, sc + score(rule, span), "unary", rule, label):
                    changed = True


def parse(
    tokens: list[Token],
    grammar: Grammar,
    *,
    max_tokens: int = MAX_TOKENS,
    score: ScoreFn | None = None,
) -> Tree | None:
    """Best-score CKY parse of ``tokens`` over ``grammar``.

    Returns the highest-scoring :class:`Tree` spanning ``[0, n)`` rooted at
    ``grammar.start`` (usually ``S``), or ``None`` when the input is empty,
    too long (§3.1), or no start-label derivation covers the full span.
    """
    n = len(tokens)
    if n == 0 or n > max_tokens:
        return None
    score = score or grammar.default_score

    # chart: (i, j) -> {label: (best_score, back)}
    cell: dict[tuple[int, int], dict[str, tuple[float, tuple]]] = {}

    # width 1: each token's POS seeds its cell, then unary-closed
    for i in range(n):
        c: dict[str, tuple[float, tuple]] = {}
        _add(c, tokens[i].pos, 0.0, "lex", i)
        cell[(i, i + 1)] = c
        _unary_close(c, grammar, score, (i, i + 1))

    # widths 2..n
    for w in range(2, n + 1):
        for i in range(0, n - w + 1):
            j = i + w
            c: dict[str, tuple[float, tuple]] = {}
            for k in range(i + 1, j):
                left = cell.get((i, k), {})
                right = cell.get((k, j), {})
                if not left or not right:
                    continue
                for b_label, (lb_score, _) in left.items():
                    for rule in grammar.binary.get(b_label, ()):
                        c_label = rule.rhs[1]
                        if c_label not in right:
                            continue
                        rc = right[c_label]
                        ns = lb_score + rc[0] + score(rule, (i, j))
                        _add(c, rule.lhs, ns, "binary", rule, k, b_label, c_label)
            _unary_close(c, grammar, score, (i, j))
            cell[(i, j)] = c

    top = cell.get((0, n), {})
    if grammar.start not in top:
        return None

    # backtrack ------------------------------------------------------------
    def build(i: int, j: int, label: str) -> Tree:
        _, back = cell[(i, j)][label]
        if back[0] == "lex":
            return Tree(label, (i, j), [], tokens[back[1]])
        if back[0] == "unary":
            rule, child = back[1], back[2]
            return Tree(label, (i, j), [build(i, j, child)])
        # binary
        rule, k, b_label, c_label = back[1], back[2], back[3], back[4]
        return Tree(
            label, (i, j), [build(i, k, b_label), build(k, j, c_label)]
        )

    return build(0, n, grammar.start)


# ----------------------------------------------------------- tree traversal


def leaf_tokens(tree: Tree | None) -> list[Token]:
    """Preterminal tokens in left-to-right order (``None`` tree → empty)."""
    if tree is None:
        return []
    if tree.is_leaf:
        return [tree.token] if tree.token is not None else []
    out: list[Token] = []
    for c in tree.children:
        out.extend(leaf_tokens(c))
    return out


def phrases(tree: Tree | None) -> list["Tree"]:
    """Internal (non-leaf) phrase nodes, pre-order. The root is included.

    Leaves (preterminals carrying a :class:`Token`) are excluded — they are
    POS tags, not phrases the linker surfaces as ``kind="phrase"`` nodes.
    """
    if tree is None:
        return []
    if tree.is_leaf:
        return []
    out: list[Tree] = [tree]
    for c in tree.children:
        out.extend(phrases(c))
    return out


def head_leaf(tree: Tree) -> Tree | None:
    """Approximate *head* preterminal leaf of a phrase (no-LM head walk).

    Percolation rules (a subset of standard head tables, good enough to pair a
    phrase node with one head token for the ``head`` edge):

    * **NP** — the head noun is the *rightmost* noun. Nested NPs (built by the
      ``NP -> DT NP`` / ``NP -> JJ NP`` chains) recurse into their rightmost NP
      child; a rightmost **PP** adjunct is skipped (its head is not the NP's).
    * **S** — the main verb: descend the VP child (``S -> NP VP``).
    * **VP / PP / SBAR** — the first lexical child (verb / preposition / head
      verb); otherwise recurse into the first non-leaf child.

    Coordination children (``CC``) and binarisation synthetics (``<L~k>``) are
    skipped. Returns ``None`` for an empty/None tree or a headless phrase.
    """
    if tree is None or tree.is_leaf:
        return None
    kids = [c for c in tree.children if not c.label.startswith("<")]

    if tree.label == "NP":
        # rightmost NP child carries the head (recurse), skipping PP adjuncts
        for c in reversed(kids):
            if c.label == "NP" or c.label in ("NP",):
                return head_leaf(c)
        # else rightmost lexical child (the head noun / proper noun)
        for c in reversed(kids):
            if c.is_leaf:
                return c
        return None

    if tree.label == "S":
        vp = next((c for c in kids if c.label == "VP"), None)
        if vp is not None:
            return head_leaf(vp)

    if tree.label in ("VP", "PP", "SBAR", "S"):
        # first lexical child is the head (verb / head verb / preposition)
        for c in kids:
            if c.is_leaf:
                return c
        for c in kids:
            if not c.label.startswith("CC"):
                return head_leaf(c)
        return None

    # default: rightmost lexical, else descend rightmost child
    for c in reversed(kids):
        if c.is_leaf:
            return c
    return head_leaf(kids[-1]) if kids else None
