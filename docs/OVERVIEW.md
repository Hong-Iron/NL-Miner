# NL-Miner — what it is, and what it's actually worth

An honest introduction. If you want to install it, read [USAGE](USAGE.md); if you
want the internals, read [ARCHITECTURE](ARCHITECTURE.md). This document is for
deciding whether you care.

---

## 1. The problem

You have a vault. You wrote 80 notes over a year, each one made sense when you
wrote it, and now:

- **You can't find the connections.** Two notes are about the same thing. You
  don't remember writing the second one. Nothing tells you.
- **Obsidian's own tools can't help much.** Its graph is a force-directed cloud
  where position means nothing and only links you *already made* are drawn — it
  shows you the connections you don't need reminding of. Its "unlinked mentions"
  matches literal strings, so `문어는` and `문어를` never line up, and neither do
  `tool` and `tools`.
- **An agent reading it burns context.** "What do my notes say about X" means
  loading whole files to find three paragraphs.

NL-Miner attacks exactly that: **make the vault's latent structure queryable**,
without a language model, without sending your notes anywhere.

## 2. What it does

Markdown → a graph in SQLite, in four layers. They are not equally good, and
knowing which is which is the point of §3.

```
  note.md
     │  preprocess     BOM, line endings, front-matter, language detect
     ▼
  ┌──────────┐  blocks       heading / para / sentence / list / quote / code / table
  │  BLOCKS  │               nested, with char spans back into the source
  └────┬─────┘
       │  POS tag + canonicalise
       ▼
  ┌──────────┐  concepts     every (word, pos) collapses onto ONE shared node,
  │ CONCEPTS │               so a word links every note that uses it
  └────┬─────┘
       │  resolve [[wikilinks]] across files
       ▼
  ┌──────────┐  links        what you already connected
  │  LINKS   │
  └────┬─────┘
       │  (optional) --parse
       ▼
  ┌──────────┐  grammar      CKY phrases + arc-eager dependencies (English only)
  │ GRAMMAR  │
  └──────────┘
```

Two surfaces sit on one Python core and one SQLite store:

- **An Obsidian plugin** — one panel in the right sidebar with four tabs (Note /
  Graph / Vault / Audit). English or Korean.
- **A Claude Code skill** — an agent indexes once, then fetches only the blocks it
  needs instead of reading files whole.

Both share the same database, so whichever indexes first, the other reuses.

## 3. What each layer is actually worth

This is the section that matters, and the one most projects don't write.
**Measured on real corpora, not asserted:**

| layer | verdict | evidence |
|---|---|---|
| **blocks** | **works** | language-independent; the entire UI renders off this alone |
| **concepts** | **works** | 21–48% dedup on docs, 55% on a real 80-note vault; powers everything below |
| **links** | **works** | resolves `[[Note]]`, `[[Note\|alias]]`, `[[Note#heading]]`, `[[folder/Note]]` |
| **grammar (phrases)** | **~2% coverage** | 6 of 298 sentences across this repo's own `docs/`; 1 of 15 on `octopus.md` |
| **grammar (dependencies)** | works on English | skipped on non-English rather than guessed |

The phrase parser is a real CKY implementation over a real CFG and it is **almost
always empty on real prose**, because one word outside its vocabulary fails the
whole sentence. That's not a bug to file; it's what a hand-written grammar does
outside its lexicon.

So: **the value is in blocks and concepts, not in the NLP.** Every feature worth
using is built on the two layers that work.

### Numbers that look like wins but aren't

Kept only so existing contracts don't break. Prefer the ones on the right:

| don't read | reason | read instead |
|---|---|---|
| `saved_tokens`, `ratio` | counts byte-identical duplicate *blocks* — real prose has none | `token_saved`, `ratio_token` |
| `top_canonical` | ranks those same duplicate-block groups | `top_concepts` |
| `parsed_sentences` | satisfied by the dependency parser alone, so it reads ~100% while CKY found nothing | `cky_trees`, `cky_coverage` |

## 4. The features that matter

### Concept bridges — "these two notes should probably link"

The one thing Obsidian can't do. It matches strings; this matches **concepts**.

```
rollout.md ↔ blue-green-deployment.md   relatedness 0.29
   green, blue, canary-deployment, Canary, Switch
```

Three things make it work, and all three are necessary:

1. **Folding.** `tool`/`tools`/`Tools` are one concept. `문어는`/`문어가`/`문어를`
   are one concept. `agent가` is `agent`.
2. **Document frequency decides what counts.** Sharing a word is not evidence —
   it depends entirely on *which* word. Two notes both saying "use" are not
   related; two both saying "hemocyanin" are. A stoplist cannot express this,
   because the uninformative words aren't a fixed list — they're whatever *your*
   vault repeats. "plugin" is noise in a plugin repo and signal anywhere else.
   So a concept in every note scores **0**, and past a third of the vault it's
   dropped as vault-wide vocabulary.
3. **Length normalisation.** Score is cosine over idf-weighted concepts (0–1), not
   a sum — otherwise two long notes always beat a short relevant one, and the
   threshold couldn't be portable across vaults.

**Tuned for precision, deliberately.** On a mixed-topic fixture (biology /
cooking / git): **100% precision, 84% recall**. The trade is intentional and
asymmetric — a missed suggestion is invisible, while one junk suggestion makes
you distrust every suggestion beside it.

### Vault audit — "what's orphaned, what should connect"

On a real 80-note vault: **73 of 81 notes had no link at all**, 47 of them had
candidates waiting. That's the finding people actually act on.

### Vault graph — hierarchical and scoped

Unlike Obsidian's flat cloud:

- **hierarchy** — notes sit under their folders, indented. Computed, not
  simulated, so nothing drifts between renders.
- **scope** — narrow to a folder subtree, or to the notes that actually discuss
  one concept.
- **suggestions stay distinct** — real links solid, bridges dashed, and only for
  the selected note. A suggestion must never look like a link.

### Note graph — the note's shape

Sections, not blocks. Prose collapses to one node (eight paragraphs are *one*
fact about a section); quotes, code and tables stay individual, because those are
what you scan a document for. A 54-block note becomes 18 sections and you can see
at a glance that it's code-heavy.

### Scoped retrieval for agents

`search` / `query --block` / `view` return the blocks you asked about, not the
file. The saving is real and large — but note it comes from **retrieval scope**,
not from the dedup that `saved_tokens` measures. Different axis entirely.

## 5. Korean

Works on a default install — no config, no extra dependency.

- **조사 are split**, which is the only reason concept linking can fire on a
  Korean vault at all: without it 문어는/문어가/문어를 are three unrelated nodes.
  Latin stems too — `agent가` → `agent` — because that's how Korean technical
  writing is actually written.
- **The English-only parsers are gated off per sentence**, not per file, so an
  English quote inside a Korean note still parses. Non-English sentences are
  *counted* (`skipped_lang`), never guessed at. Before this gate, every Korean
  token was tagged a noun, which made a rule fire between every adjacent pair —
  a Korean note produced dozens of confident-looking relations, one of them
  attached to a list bullet. Confident nonsense is worse than nothing.
- **어미 are out of scope.** Verb endings need a real morphological analyser
  (KoNLPy/MeCab, opt-in via `NLM_MORPH`). Korean gives reliable **noun** concepts
  — which is what concept linking runs on — and nothing more.

## 6. What it cannot do

Stated plainly, because a tool that hides its limits wastes your time:

- **No semantics.** This is morphological matching. Two notes about the same idea
  in different words will never be found. There is no model; there never will be
  one in this design.
- **Phrase parsing is ~2% on real prose.** Treat an empty phrase list as normal.
- **Korean verbs are not analysed.** See §5.
- **Notes sharing a basename across folders resolve arbitrarily.** Obsidian's
  path-proximity rule isn't modelled.
- **Desktop only.** The plugin spawns a Python child process.

## 7. Design principles

The rules that produced the decisions above:

1. **Measure the layer, don't trust the test suite.** "347 tests green" means the
   contracts hold. It does not mean a layer is useful. The 2% number only exists
   because someone counted.
2. **A metric that can't fire is worse than no metric.** `saved_tokens` reported
   0.000 on every real document while a working 48% dedup went unreported.
3. **Never fabricate.** Skip and count (`skipped_lang`) rather than guess.
4. **Precision over recall for suggestions.** Asymmetric costs.
5. **Self-calibrating over hand-listed.** Document frequency adapts per vault; a
   stoplist can't.
6. **Plain language in the UI, symbols in the tooltip.** `NP` means nothing to
   most readers; "naming chunk" does. Nothing is lost — the symbol is one hover
   away.
7. **Computed layouts, not simulated.** A node that drifts between renders can't
   be recognised.

## 8. Verification

**347 tests.** The ones worth knowing about pin behaviours that would regress
silently:

| test | what it defends |
|---|---|
| `test_lang_gate.py` | the EN parsers never run on non-EN text |
| `test_honest_stats.py` | *which* number tells the truth |
| `test_concept_relevance.py` | no suggestion crosses unrelated topics |
| `test_adapters_ko.py` | 조사 split — and what must **not** split (`사과` is not `사`+`과`) |
| `test_lemma_concepts.py` | `octopus` must not become `octopu` |
| `test_vault_graph.py` | links and suggestions stay disjoint |

## 9. Where to go next

- [USAGE](USAGE.md) — install and use, both surfaces
- [ARCHITECTURE](ARCHITECTURE.md) — layers, store schema, invariants
- [DEPLOYMENT](DEPLOYMENT.md) — packaging and distribution
- [README](../README.md) — CLI quickstart
