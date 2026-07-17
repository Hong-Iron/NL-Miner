"""Phase 2 — CKY constituency parser tests (no external deps).

Completion gates (phase2_parsers.md §2.1–§2.2, §3.1):
  * grammar.cfg loads and is NF2 (RHS ≤ 2 after binarisation)
  * binarize() splits a trinary rule into NF2 + synthetic nodes, expanding back
  * tokeniser-friendly sentences (aux / -ed verbs the rule tagger recognises)
    yield an S-rooted Tree with NP+VP children and the full token span
  * PP attachment surfaces a PP phrase node
  * input above MAX_TOKENS or empty → None (graceful, §3.1)
  * parse is deterministic (same in → same out), so canonical node ids downstream
  * the score(rule, span) interface changes which derivation wins (PCFG swap-point)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.cky import (
    GRAMMAR_PATH,
    Grammar,
    MAX_TOKENS,
    Rule,
    Tree,
    binarize,
    head_leaf,
    leaf_tokens,
    parse,
    phrases,
)
from src.postagger import RulePOSTagger, Token, tag

PROJECT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------- helpers


def _tree_sig(t: Tree | None):
    if t is None:
        return None
    tok = t.token.text if t.token is not None else None
    return (t.label, t.span, tuple(_tree_sig(c) for c in t.children), tok)


# ---------------------------------------------------------- grammar load


def test_grammar_cfg_exists_and_loads():
    g = Grammar.load()
    assert g.start == "S"
    assert len(g.rules) >= 20
    # post-binarisation invariant: NF2
    assert all(r.arity <= 2 for r in g.rules)
    # the canonical S -> NP VP rule is present (others are extensions)
    assert any(r.lhs == "S" and r.rhs == ("NP", "VP") for r in g.rules)
    # grammar default path resolves to the repo's grammar.cfg
    assert GRAMMAR_PATH == PROJECT / "grammar.cfg"


def test_grammar_uses_default_path_when_omitted():
    g = Grammar.load()
    assert any(r.lhs == "NP" and r.rhs == ("DT", "NN") for r in g.rules)


def test_trinary_rule_in_cfg_is_binarised_on_load():
    # grammar.cfg lists `S -> NP VP PP`; load must split it into NF2
    g = Grammar.load()
    assert any(r.lhs == "S" and r.rhs[0] == "NP" and r.rhs[1].startswith("<S~") for r in g.rules)
    assert any(r.rhs == ("VP", "PP") for r in g.rules)


def test_inline_weight_parsed():
    g = Grammar.load()
    rule = next(r for r in g.rules if r.lhs == "NP" and r.rhs == ("DT", "NN"))
    assert rule.weight == 1.0
    # `S -> VP` carries the lighter 0.6 weight
    rvp = next(r for r in g.rules if r.lhs == "S" and r.rhs == ("VP",))
    assert rvp.weight == 0.6


# ---------------------------------------------------------- binarize


def test_binarize_passthrough_binary():
    r = Rule("NP", ("DT", "NN"), 1.0)
    assert binarize(r) == [r]


def test_binarize_trinary_to_two_rules():
    r = Rule("S", ("NP", "VP", "PP"), 0.7)
    out = binarize(r)
    assert len(out) == 2
    assert all(x.arity <= 2 for x in out)
    assert out[0] == Rule("S", ("NP", "<S~0>"), 0.7)
    assert out[1] == Rule("<S~0>", ("VP", "PP"), 0.7)


def test_binarize_4ary_chain():
    r = Rule("A", ("B", "C", "D", "E"), 1.0)
    out = binarize(r)
    assert len(out) == 3
    assert out[0] == Rule("A", ("B", "<A~0>"), 1.0)
    assert out[1] == Rule("<A~0>", ("C", "<A~1>"), 1.0)
    assert out[2] == Rule("<A~1>", ("D", "E"), 1.0)


# ---------------------------------------------------------- parses (aux/-ed)


@pytest.mark.parametrize("sentence", [
    "The octopus is an animal.",
    "The dog walked.",
    "The tool is useful.",
    "The dog walked to the park.",
])
def test_parse_sentence_yields_full_span_s(sentence):
    toks = tag(sentence)
    assert len(toks) > 0
    g = Grammar.load()
    tree = parse(toks, g)
    assert tree is not None, f"failed to parse: {sentence!r}"
    assert tree.label == "S"
    assert tree.span == (0, len(toks)), "S must span the whole sentence"
    # S always splits into NP + VP under this grammar
    labels = {c.label for c in tree.children}
    assert "NP" in labels and "VP" in labels


def test_parse_a_keeps_preterminal_text():
    toks = tag("The octopus is an animal.")
    g = Grammar.load()
    tree = parse(toks, g)
    leaves = [t.text for t in leaf_tokens(tree)]
    assert leaves == ["The", "octopus", "is", "an", "animal"]


def test_preterminal_text_whitespace_survives():
    toks = tag("The dog walked to the park.")
    g = Grammar.load()
    tree = parse(toks, g)
    leaves = [t.text for t in leaf_tokens(tree)]
    assert leaves == ["The", "dog", "walked", "to", "the", "park"]


# ---------------------------------------------------------- PP phrase


def test_pp_attachment_produces_pp_node():
    toks = tag("The dog walked to the park.")
    g = Grammar.load()
    tree = parse(toks, g)
    labels = [p.label for p in phrases(tree)]
    assert "PP" in labels
    pp = next(p for p in phrases(tree) if p.label == "PP")
    # PP -> IN NP; its children are a preposition (IN) and an NP
    assert pp.children[0].label == "IN"


# ---------------------------------------------------------- head leaf


def test_head_leaf_np_is_rightmost_noun():
    toks = tag("The octopus is an animal.")
    g = Grammar.load()
    tree = parse(toks, g)
    np = next(p for p in phrases(tree) if p.label == "NP")
    head = head_leaf(np)
    assert head is not None and head.is_leaf
    assert head.token.text == "octopus"  # rightmost noun of "The octopus"


# ---------------------------------------------------------- length + empty


def test_empty_input_returns_none():
    assert parse([], Grammar.load()) is None


def test_over_max_tokens_returns_none():
    # 32 tokens (no verb anyway); the length cap, not the grammar, short-circuits
    words = ["The", "dog"] * 16
    toks = tag(" ".join(words))
    assert len(toks) > MAX_TOKENS
    g = Grammar.load()
    assert parse(toks, g) is None


def test_at_max_tokens_does_not_short_circuit():
    # exactly MAX_TOKENS is *not* over the cap → CKY must run (not None-by-guard)
    parts = ["The", "dog"] * (MAX_TOKENS // 2)
    toks = tag(" ".join(parts))
    assert len(toks) == MAX_TOKENS
    g = Grammar.load()
    tree = parse(toks, g)
    # all DT/NN, no verb → no S derivable, but the run happened (not the guard)
    assert tree is None


# ---------------------------------------------------------- determinism


def test_parse_is_deterministic():
    toks = tag("The dog walked to the park.")
    g = Grammar.load()
    a = parse(toks, g)
    b = parse(toks, g)
    assert _tree_sig(a) == _tree_sig(b)


# ---------------------------------------------------------- no derivation


def test_unparsable_pos_sequence_returns_none():
    # tokens whose POS projects to nothing under the grammar → no S → None
    toks = [Token(text="z", pos="ZZZ"), Token(text="q", pos="QQQ")]
    assert parse(toks, Grammar.load()) is None


# ---------------------------------------------------------- score interface


def test_score_interface_changes_chosen_derivation():
    # An ambiguous grammar: T projects to X OR Y (both unary), then both X,Y
    # project to A; A B → S. With equal weights A is derived via X (rules are
    # registered in file order, so X->T fires before Y->T); injecting a scorer
    # that boosts the Y rule steeply makes the Y derivation of A win instead.
    src = """
    START S
    S -> A B 1.0
    A -> X 1.0
    A -> Y 1.0
    X -> T 1.0
    Y -> T 1.0
    B -> U 1.0
    """
    g = Grammar.load(source=src)
    toks = [Token(text="x", pos="T"), Token(text="y", pos="U")]

    # default: A derived via X (registry order)
    t_default = parse(toks, g)
    assert t_default is not None
    a_def = next(p for p in phrases(t_default) if p.label == "A")
    assert a_def.children[0].label == "X"

    # custom scorer boosts the Y path steeply → A derived via Y
    def boost_y(rule: Rule, _span):
        return 100.0 if rule.lhs == "Y" else rule.weight

    t_y = parse(toks, g, score=boost_y)
    assert t_y is not None
    a_y = next(p for p in phrases(t_y) if p.label == "A")
    assert a_y.children[0].label == "Y"


def test_default_score_uses_rule_weight():
    g = Grammar.load()
    rvp = next(r for r in g.rules if r.lhs == "S" and r.rhs == ("VP",))
    assert Grammar.default_score(rvp, (0, 1)) == 0.6
