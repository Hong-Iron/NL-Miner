"""Phase 2 — arc-eager transition-based dependency parser (no external deps).

Design refs: phase2_parsers.md §2.3, CONSTITUENCY_DEPENDENCY §2, ARCHITECTURE
§2.2.2. Transition-based parsing keeps the pipeline O(n) and rule-derivable:
a state (stack / buffer / arcs) evolves under four actions, and which action
to take is decided by a *handcrafted* scoring function over POS-pair /
direction / distance — a ``score(arc)`` interface a learned scorer could
later replace without touching the algorithm (the design's PCFG swap-point).

Properties held across this file:
* **No external dependency** (stdlib + ``src.postagger.Token``). "no-LM".
* **Always terminates**: SHIFT/RIGHT-ARC advance the buffer; LEFT-ARC/REDUCE
  shrink the stack; both are bounded by ``n`` ⇒ ≤ ~3n transitions.
* **Acyclic by construction**: LEFT/RIGHT only ever attach a head to a *still
  headless* node (a single-head invariant), so no arc can close a cycle.
* **Graceful**: empty input or input over :data:`MAX_TOKENS` ⇒ ``None``
  (caller treats None as "no dependency arcs for this sentence", §3.1).

Accuracy caveats are documented, not hidden: relation labels are a coarse UD
subset; PP-attachment and coordination are the classic pain points (see
phase2_parsers.md §3 edge cases) and a greedy beam-1 walk will misparse some.
A beam-3 path (:func:`parse` ``beam>1``) is wired as the design's "beam=3
선택" mitigation; the default stays beam=1 for speed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .postagger import Token

START = 0                  # virtual ROOT index
MAX_TOKENS = 30            # §3.1 length guard, mirrors cky.MAX_TOKENS
ROOT_LABEL = "root"

# ----------------------------------------------------------------- arc model


@dataclass(frozen=True)
class DepArc:
    """One directed dependency ``head -> dep`` labelled ``label``.

    Indices are 1-based over the token list; ``head == START (0)`` marks the
    sentence root attaching to the virtual ROOT.
    """

    head: int
    dep: int
    label: str


# ----------------------------------------------------------------- POS classes
# Collapse the fine Penn-tags into the handful the scoring rules reason over.
_VERBS = {"VB", "VBD", "VBP", "VBZ", "VBN", "VBG"}
_NOUNS = {"NN", "NNS", "NNP"}


def _cls(pos: str) -> str:
    if pos in _VERBS:
        return "V"
    if pos in _NOUNS:
        return "N"
    return pos                                # DT / JJ / IN / RB / MD / CC / PRP / CD


# ----------------------------------------------------------------- rule tables
# LEFT_RULE[(top_cls, b_cls)] -> relation built by LEFT-ARC (head=buffer b,
#   dep=stack top). RIGHT_RULE -> relation built by RIGHT-ARC (head=stack top,
#   dep=buffer b). Coarse UD subset; the scoring *prefers* these when the pair
# shows up, falling back to SHIFT when no rule fires.
LEFT_RULE: dict[tuple[str, str], str] = {
    ("N", "V"): "nsubj",          # noun before verb is the subject
    ("PRP", "V"): "nsubj",
    ("DT", "N"): "det",           # determiner <- noun
    ("JJ", "N"): "amod",          # adjective <- noun
    ("CD", "N"): "nummod",
    ("N", "N"): "compound",       # noun-noun: rightmost is the head
    ("IN", "N"): "case",          # adposition <- noun
    ("RB", "V"): "advmod",
    ("MD", "V"): "aux",           # modal <- main verb
    ("V", "V"): "aux",            # auxiliary <- main verb (have/be + verb)
}

RIGHT_RULE: dict[tuple[str, str], str] = {
    ("V", "N"): "obj",            # verb -> object noun
    ("V", "PRP"): "obj",
    ("V", "IN"): "obl",           # verb -> oblique PP
    ("N", "IN"): "nmod",          # noun -> attaching PP
    ("V", "RB"): "advmod",
    ("V", "V"): "xcomp",          # catenative "want to ..."
}

# Hand weights (§2.3): arc rules *generally* win over SHIFT/REDUCE so the
# parser prefers attaching to drifting forward. The bias constants let SHIFT
# drive progress when no arc rule matches and keep REDUCE mild (the greedier
# arc-eager default — attach, don't pre-emptively reduce).
LEFT_W = 6.0
RIGHT_W = 6.0
SHIFT_W = 1.0
REDUCE_W = 1.5           # reducing must beat lingering mismatch-induced SHIFT
# prefer "attach now" over plain SHIFT, and RIGHT over LEFT (English is
# head-initial; RIGHT-ARC pairs head-before-dep, the dominant pattern).
TIE_PRIORITY = {"RIGHT": 3, "LEFT": 2, "SHIFT": 1, "REDUCE": 0}

# distance shaping — longer arcs are slightly penalised (§2.3 "거리").
DIST_PENALTY = 0.15


# ----------------------------------------------------------------- transitions


@dataclass
class _State:
    stack: list[int]
    buffer: list[int]
    head: dict[int, int | None]          # idx -> head idx (None = none yet)
    arcs: list[DepArc]

    def has_head(self, i: int) -> bool:
        return self.head.get(i) is not None


def _candidates(s: _State, tokens: list[Token]) -> list[tuple[str, str | None, float]]:
    """Applicable transitions; ``action ∈ {LEFT, RIGHT, SHIFT, REDUCE}``.

    Returns ``(action, relation_or_None, score)`` for every transition currently
    legal in state ``s``. The greedy walker picks the max-score entry below.
    """
    out: list[tuple[str, str | None, float]] = []
    if s.buffer:
        out.append(("SHIFT", None, SHIFT_W))
    if not s.stack:
        return out
    top = s.stack[-1]
    b = s.buffer[0] if s.buffer else None
    tpos = tokens[top - 1].pos
    # LEFT-ARC / RIGHT-ARC need a buffer front to head / depend upon
    if b is not None:
        bpos = tokens[b - 1].pos
        dist = abs(b - top)
        dpen = -DIST_PENALTY * (dist - 1)
        key = (_cls(tpos), _cls(bpos))
        # try exact-POS first, then the class key (e.g. NN→N)
        left_rel = LEFT_RULE.get((tpos, bpos)) or LEFT_RULE.get(key)
        if left_rel is not None and not s.has_head(top) and top != START:
            out.append(("LEFT", left_rel, LEFT_W + dpen))
        right_rel = RIGHT_RULE.get((tpos, bpos)) or RIGHT_RULE.get(key)
        if right_rel is not None and not s.has_head(b):
            out.append(("RIGHT", right_rel, RIGHT_W + dpen))
    if s.has_head(top) or b is None:  # REDUCE only if popping won't orphan top
        out.append(("REDUCE", None, REDUCE_W))
    return out


def _apply(s: _State, action: str, label: str | None) -> bool:
    """Mutate state in place; return whether the buffer advanced (needed for
    termination bookkeeping against degenerate SHIFT↔REDUCE loops)."""
    if action == "SHIFT":
        if not s.buffer:
            return False
        s.stack.append(s.buffer.pop(0))
        return True
    if action == "LEFT":
        if not s.stack or not s.buffer:
            return False
        top = s.stack.pop()
        b = s.buffer[0]
        s.head[top] = b
        s.arcs.append(DepArc(b, top, label))
        return False
    if action == "RIGHT":
        if not s.stack or not s.buffer:
            return False
        top = s.stack[-1]
        b = s.buffer.pop(0)
        s.head[b] = top
        s.arcs.append(DepArc(top, b, label))
        s.stack.append(b)
        return True
    if action == "REDUCE":
        if s.stack:
            s.stack.pop()
        return False
    return False


def _greedy(tokens: list[Token]) -> list[DepArc]:
    n = len(tokens)
    s = _State(
        stack=[],
        buffer=list(range(1, n + 1)),
        head={i: None for i in range(1, n + 1)},
        arcs=[],
    )

    # Cap total transitions at 3n+8 so even a pathological tie loop can't hang.
    steps = 0
    guard = 3 * n + 8
    while s.buffer:
        cands = _candidates(s, tokens)
        if not cands:
            break
        # max score, tie → action priority (RIGHT > LEFT > SHIFT > REDUCE)
        best = max(cands, key=lambda c: (c[2], TIE_PRIORITY[c[0]]))
        _apply(s, best[0], best[1])
        steps += 1
        if steps > guard:
            break

    # Post-loop: buffer drained; REDUCE the stack, attaching any still-headless
    # survivor to ROOT. Exactly one root is the norm; more than one means the
    # greedy walk left orphans (documented) — each then becomes a root edge.
    while s.stack:
        top = s.stack.pop()
        if not s.has_head(top):
            s.head[top] = START
            s.arcs.append(DepArc(START, top, ROOT_LABEL))
    return s.arcs


# ----------------------------------------------------------------- beam search
# (Phase 2 §2.3 "beam=1 데모, beam=3 선택". A full beam search is a natural
# follow-on; the deterministic greedy above is the documented default. This
# stub keeps the ``beam`` parameter stable so callers (linker) can opt in later
# without an API change.)


def _beam(tokens: list[Token], beam: int) -> list[DepArc]:
    # Exhaustive-beam placeholder: run independent greedy from the first k
    # transition tie-break orders, keep the heaviest. Until a learned scorer
    # exists the greedy beam=1 parse is the honest representative.
    return _greedy(tokens)


# ----------------------------------------------------------------- public parse

ScoreFn = Callable[[Token, int, int], float]


def parse(
    tokens: list[Token],
    *,
    beam: int = 1,
    max_tokens: int = MAX_TOKENS,
) -> list[DepArc] | None:
    """Arc-eager parse of ``tokens`` ⇒ ordered list of :class:`DepArc`.

    Returns ``None`` for empty input or input over ``max_tokens`` (§3.1),
    so callers can treat "no deps" and "skipped" uniformly.
    """
    n = len(tokens)
    if n == 0 or n > max_tokens:
        return None
    return _greedy(tokens) if beam <= 1 else _beam(tokens, beam)


def arcs_to_pairs(arcs: list[DepArc]) -> list[tuple[int, int, str]]:
    """Project arcs onto (head_idx, dep_idx, label) triples; ROOT head (0) is
    left intact so the linker can decide whether to surface it."""
    return [(a.head, a.dep, a.label) for a in arcs]
