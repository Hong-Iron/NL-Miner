"""Phase 2 — arc-eager transition dependency parser tests (no external deps).

Completion gates (phase2_parsers.md §2.3, CONSTITUENCY_DEPENDENCY §2):
  * empty / over-MAX_TOKENS input → None (graceful, §3.1)
  * a parse yields exactly n arcs over an n-token sentence (single-head)
  * no self loop, and every token's head-chain reaches ROOT (acyclic)
  * a one-token sentence → exactly one root arc
  * short, traced sentences produce the expected relations + root
  * parse is deterministic (idempotent) — needed for stable canonical ids
  * an adjective modifier surfaces an `amod`, a verb-object an `obj`
"""
from __future__ import annotations

import pytest

from src.postagger import tag
from src.transition_parser import MAX_TOKENS, DepArc, parse


def _arcs(sentence: str) -> list[DepArc]:
    toks = tag(sentence)
    arcs = parse(toks)
    assert arcs is not None, "expected arcs, got None"
    return arcs


# --------------------------------------------------------- structural invariants


@pytest.mark.parametrize("sentence", [
    "The dog is.",
    "The useful tool is.",
    "The dog consumed food.",
    "The dog walked to the park.",
    "Octopus.",
])
def test_single_head_cover(sentence):
    arcs = _arcs(sentence)
    toks = tag(sentence)
    n = len(toks)
    # exactly one arc per token (a head per dependent), no self loops
    assert len(arcs) == n
    assert {a.dep for a in arcs} == set(range(1, n + 1))
    assert all(a.head != a.dep for a in arcs)


@pytest.mark.parametrize("sentence", [
    "The dog is.",
    "The useful tool is.",
    "The dog consumed food.",
    "The dog walked to the park.",
])
def test_head_chain_reaches_root(sentence):
    arcs = _arcs(sentence)
    head = {a.dep: a.head for a in arcs}
    toks = tag(sentence)
    # every index in 1..n must, via repeated head lookup, reach 0 (ROOT)
    for start in range(1, len(toks) + 1):
        cur = start
        for _ in range(len(toks) + 1):
            if cur == 0:
                break
            cur = head.get(cur, 0)
        assert cur == 0, f"head chain from {start} never reached ROOT: {sentence!r}"


def test_roots_head_to_zero():
    arcs = _arcs("The dog is.")
    root = [a for a in arcs if a.head == 0]
    assert len(root) == 1, "well-formed sentence attaches exactly one ROOT head"
    assert root[0].label == "root"


# --------------------------------------------------------- empties / length


def test_empty_returns_none():
    assert parse([]) is None


def test_over_max_tokens_returns_none():
    toks = tag(" ".join(["The", "dog"] * (MAX_TOKENS // 2 + 1)))
    assert len(toks) > MAX_TOKENS
    assert parse(toks) is None


# --------------------------------------------------------- single token


def test_single_token_sentence_is_one_root():
    arcs = _arcs("Octopus.")
    assert len(arcs) == 1
    assert arcs[0].head == 0
    assert arcs[0].dep == 1
    assert arcs[0].label == "root"


# --------------------------------------------------------- traced sentences


def test_the_dog_is_exact_arcs():
    arcs = _arcs("The dog is.")
    got = {(a.head, a.dep, a.label) for a in arcs}
    assert got == {(2, 1, "det"), (3, 2, "nsubj"), (0, 3, "root")}


def test_transitive_sentence_has_obj():
    arcs = _arcs("The dog consumed food.")
    got = {(a.head, a.dep, a.label) for a in arcs}
    # consumed is the verb (root); food is its object
    assert (3, 2, "nsubj") in got      # consumed -> dog (subject)
    assert (3, 4, "obj") in got        # consumed -> food (object)
    assert (0, 3, "root") in got


def test_adjective_modifier_surfaces_amod():
    arcs = _arcs("The useful tool is.")
    got = {(a.head, a.dep, a.label) for a in arcs}
    assert (3, 2, "amod") in got        # tool -> useful
    assert (3, 1, "det") in got         # tool -> the
    assert (0, 4, "root") in got


# --------------------------------------------------------- determinism


@pytest.mark.parametrize("sentence", [
    "The dog is.",
    "The dog walked to the park.",
    "The useful tool is.",
])
def test_parse_is_deterministic(sentence):
    a = parse(tag(sentence))
    b = parse(tag(sentence))
    assert a is not None and b is not None
    assert [(x.head, x.dep, x.label) for x in a] == [(x.head, x.dep, x.label) for x in b]
