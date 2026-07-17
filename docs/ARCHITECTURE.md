# NL-Miner — Architecture & Implementation

NL-Miner is a **rule-based (no-LM) pipeline** that turns Markdown / plain text
into a queryable graph — docs → blocks → canonical concepts → links between
notes, with an optional English phrase/dependency parse on top — and exposes it
through two surfaces sharing **one Python core and one SQLite store**:

1. **An Obsidian plugin** (TypeScript shell, hybrid) — index a vault, read a
   note's shape, see which notes should link, audit what's orphaned.
2. **A Claude Code skill** — an agent indexes a path once, then issues scoped
   queries to fetch only the blocks it needs (saving context tokens).

The layer order above is deliberate: blocks and concepts carry the value, the
parse is a bonus that mostly isn't there. [OVERVIEW.md](OVERVIEW.md) has the
measured numbers; §5 here has the limitations.

This document describes the system end-to-end. For what it's for and what it's
worth see [OVERVIEW.md](OVERVIEW.md); for install/usage see [USAGE.md](USAGE.md);
for packaging & shipping see [DEPLOYMENT.md](DEPLOYMENT.md).

## 1. The hybrid model in one picture

```
                                              one JSON object per call
   ┌──────────────────────────┐   `python -m interface.cli ... --json`   ┌──────────────────────┐
   │  Obsidian plugin (TS)    │  ───────────────────────────────────────▶│  Python core         │
   │  ├─ main.ts  (orchestr.) │                                          │  ├─ preprocess.py    │
   │  ├─ backend.ts (bridge)   │◀─── single JSON object on stdout ────────│  ├─ markdown_blocker │
   │  ├─ views  (one panel)   │                                          │  ├─ linker (+ parser)│
   │  └─ decorations.ts       │                                          │  └─ store.py (SQLite)│
   └────────────┬─────────────┘                                          └──────────┬───────────┘
                │                                                                   │
                │  Claude Code skill reuses the SAME DB + CLI (no re-index):        │
   ┌────────────▼─────────────┐                                                     │
   │  .claude/skills/nl-miner │───── scoped queries (search/view/query) ◀──────────┘
   └──────────────────────────┘
```

The two surfaces are **thin clients**. All NLP live in the verified Python core;
the TS shell and the skill only call the CLI and project the JSON. The store is
the shared rendezvous — if the plugin already indexed a vault into
`<vault>/.nl-miner/nlp_graph.db`, the skill reuses it verbatim.

## 2. Layered model

### Layer A — the verified Python core (`src/`, `payload.py`)

`src/context.py:process(fm, *, pos=False, parse=False) -> (Context, stats)` is
the orchestrator. Its pipeline is additive — every stage is reusable in
isolation:

1. **Preprocess** (`src/preprocess.py`): BOM strip, `\r\n`→`\n`, regex
   front-matter parser (`authors/author/creator` → `FileMeta.creator`),
   script-ratio language detection (ko / han→zh / kana→ja / en fallback). Produces
   the cleaned body + enriched `FileMeta`.
2. **Blockify** (`src/markdown_blocker.py`): a regex CommonMark subset → a block
   tree (heading / para / sentence / list / quote / code / table) with
   `[span_start, span_end)` half-open char offsets into the body. `.txt` →
   paragraph → sentence split.
3. **Link** (`src/linker.py`): emits edges and — critically for token saving —
   canonical-block dedup:
   - `contains` (parent→child block), `ref-to` (`[[X]]`→heading),
     `code-ref` (definition/use across code blocks), `sim-vocab`
     (Jaccard ≥ 0.5), and MinHash (shingle k=5, sig N=128, θ=0.8) near-dup,
     which yields `raw_total_tokens` vs `saved_tokens`.
   - When `pos or parse`: per-token canonical nodes (`sha256(label+"\x00"+pos)[:16]`,
     content-deterministic → cross-file sharing via `INSERT OR IGNORE`).
   - When `parse`: per-sentence **CKY constituency** (`src/cky.py`, NF2 grammar, binarised, `score(rule,span)` hook, `MAX_TOKENS=30` guard) →
     `kind="phrase"` nodes + `head` (phrase→head token) + `contains`
     (block→top S / parent→child phrase); **arc-eager dependency**
     (`src/transition_parser.py`, SHIFT/REDUCE/LEFT/RIGHT, beam=1 default,
     hand POS-pair scoring) → `dep:<rel>` edges (head→dependent, virtual
     ROOT=0 excluded).
4. The `Context` (file + blocks + nodes + edges + `schema_version`) and a `stats`
   dict return to the caller.

#### Identity & versions

- Canonical node id = `sha256(label + "\x00" + pos).hexdigest()[:16]`
  (deterministic; `pos="NA"` placeholder before POS attach).
- File / Block id = `uuid4`.
- `schema_version`: **0.1** (no POS, Phase 1) → **0.2** (POS or parse attached).

#### Serialization

- **JSONL** (`payload.save`, Phase 1 default): one `Payload` line per file —
  zero deps, diff-friendly, append-only.
- **SQLite** (`src/store.GraphStore`, Phase 3): adjacency-list store, the
  plugin/skill data plane (next section).

### Layer B — the CLI bridge (`interface/cli.py`)

A stdlib argparse surface — **14 subcommands**, each keeping human **text output
by default** and emitting **one JSON object on stdout** under `--json`:

| Subcommand | Purpose | `--json` shape (keys only) |
|---|---|---|
| `index <path> [--parse] [--pos] [--force]` | preprocess→block→link→store+payload; **incremental** — skips files whose mtime matches the store (`skipped_unchanged`), so a no-op reindex is ~0.1s vs 4s. `--force` after upgrading, since the tokenizer can change under an unchanged file | `ok, db, parse, pos, files[], skipped_unchanged, totals` |
| `query <doc> [--block <id>]` | list blocks, or a block's neighbours | `doc, file_uuid, blocks[]` / `doc, block, out[], in[]` |
| `graph [--top-canonical N]` | store summary + concepts shared across notes | `db, files[], top_canonical[], top_concepts[]` |
| `bridge <doc> [--min-shared N]` | notes sharing concepts with `<doc>` but not linked | `doc, file_uuid, bridges[{name,shared[],n_shared}]` |
| `audit [--min-shared N]` | vault health: orphans + unlinked pairs | `n_files, n_links, orphans[], unlinked_pairs[]` |
| `vault [--root R] [--folder F] [--concept C]` | scoped hierarchical graph | `scope, root, folders[], notes[], links[], bridges[]` |
| `watch <dir>` | poll for changes → live re-index | (text; long-running) |
| `purge <doc>` | confirm-deleted removal | `ok, doc, purged, db` |
| `view <file> [--parse]` | parse-graph snapshot for one file, **in-memory** (no store) | `doc, file_uuid, schema_version, parse, pos, stats, blocks[{phrases[],deps[]}], nodes[], edges[]` |
| `cards [--keywords N]` | one card per note — headings + distinctive keywords, no body (the cheap map of an unread vault) | `db, n, cards[]` |
| `outline <file>` | a document's sections + **line ranges**, no body, no store — read before reading the file | `doc, n_chars, n_lines, sections[]` |
| `search <pattern> [--kind K] [--limit N]` | scoped text search over stored blocks (`--limit` defaults to **20**: each match carries its whole block, so the response scales with the limit — 100 saved only 20% over reading the files) | `db, pattern, kind, limit, count, matches[]` |

`bridge`, `audit`, `vault` and `graph`'s `top_concepts` read the **token layer**,
which only `--pos` (or `--parse`, which implies it) writes. Against a store
indexed without it they **exit 1** with the fix, rather than returning an empty
result that reads as "these notes share nothing".

> **argparse contract:** `--db` belongs to the *parent* parser, so it must
> **precede** the subcommand: `nlm --db <db> <sub> …` ✅ — `nlm <sub> … --db <db>` ❌.

`view` is **JSON-native** (the Obsidian graph view + decorations' data source);
`search` is the **token-saving lever** for the skill (an agent fetches only
matching rows, not every block's text). The `view` block carries `span:[s,e]` (a
tuple); the `query` block carries `span_start`/`span_end` (split keys) — both
shapes are pinned by tests.

### Layer C — the Obsidian plugin (`plugin/src/`, bundled to `plugin/main.js`)

An Obsidian `Plugin` (runs in Obsidian's Electron, `platform: node` under esbuild
so Node built-ins the bridge spawns via stay external):

| Module | Role |
|---|---|
| `main.ts` | `NLMinerPlugin` — wires ribbon/commands/views/post-processor, a 5s `view --json` cache shared by all three consumers (with concurrent-call de-dup), auto/debounced reindex, the single `active-leaf-change` refresh listener. |
| `backend.ts` | `Backend` — `execFile(<python>, ["-m","interface.cli",...])`, `cwd=<plugin>/backend` (or a dev repo path), `PYTHONIOENCODING=utf-8`; parses the last `{`-line; friendly Notice on missing-Python. |
| `settings.ts` | `NLMinerSettingTab` — Python interpreter (+ Detect), db path, parse toggle, auto-reindex + debounce, backend-path override, Index-vault button. |
| `views/panel.ts` | the **one** `ItemView`. Owns the chrome (actions, tab bar) and one long-lived instance of each tab; `show(id, arg)` is the single entry point. Tabs keep their own state across switches — flipping to Vault and back must not lose your selection or refetch. |
| `views/noteTab.ts` | the note as one plain-language tree, plus "notes worth linking" with an insert-link action. |
| `views/graphTab.ts` | the note's **sections**: a heading and what's under it as a box; prose collapses to one node, quotes/code/tables stay individual satellites. |
| `views/vaultTab.ts` | folders → notes, scoped by folder or concept; real links solid, suggested bridges dashed (selected note only). |
| `views/auditTab.ts` | orphans + notes worth linking. |
| `views/svgutil.ts` | `svgEl`, `clip`, `stripExt`, and `setViewBox` — the width-aware viewBox that keeps the graphs legible in a narrow sidebar. |
| `i18n.ts` | `t(key, params)` over en/ko dicts. English is always the fallback, so a missing translation degrades to readable English, never a raw key. Language is module state: views repaint wholesale, so no pane is ever half-translated. |
| `tree.ts` | pure, Obsidian-free: `nestBlocks`/`flattenBlocks`/`flatten`/`guideFor`. One source of truth for the document's shape — and testable without a vault. |
| `labels.ts` | plain-language names, per language, for every grammar symbol the backend emits. Nothing renders a bare `NP`/`nsubj`; the symbol lives in the tooltip. |
| `decorations.ts` | `MarkdownPostProcessor` — concept chips + a counts badge; click a chip → the Note tab focused on that block. |
| `types.ts` | TS mirrors of the JSON contract + `DEFAULT_SETTINGS`. |
| `util.ts` | vault-root discovery, default paths, `debounce`/`Debounced`, `isIndexable`. |

**No bundled runtime deps.** `d3-force` was dropped with the force layout; the
graphs are deterministic. `obsidian` and `electron` are external (Obsidian
provides them); `obsidian` is a devDependency so `tsc` can actually check the
plugin.

Three cross-cutting rules:

- **One window.** There used to be three views and a modal, so reaching the vault
  graph meant hunting for its tab. They are all the same job — inspecting what
  the index knows — so they are tabs inside one pane, opened in the right sidebar
  (`main.revealView`) because they are consulted *while* reading. An already-open
  leaf is reused wherever it is, so a panel dragged to the main area stays there.
- **`t` is imported as `tr` in view files.** They already use `t` as a local for
  a `Term` or an SVG `<text>`; importing i18n's `t` unrenamed would shadow
  silently and resolve to the wrong thing.
- **`isIndexable` gates every entry point.** `view` does *not* check extensions —
  handed a PNG it decodes the bytes as lossy UTF-8 and returns replacement
  characters with exit 0 — so the plugin must never point it at a binary. The
  allowlist mirrors `filestructure.DEFAULT_EXTS`; a file we accepted but
  `discover()` skipped would strand the panel on "index first" forever.

### Layer D — the Claude Code skill (`.claude/skills/nl-miner/SKILL.md`)

A project-scoped skill. Its body is an instruction set an agent follows when the
request matches the description ("index documents/notes, then query scoped"):
it tells the agent to **auto-detect** the store (`<root>/.nl-miner/nlp_graph.db`,
reuse if present — the plugin may have warmed it — else `index` into a tmp db),
then map the user's question to the cheapest scoped query (`search` for
"mentions X", `view` for "structure of Y", `query --block` for "what links
here"), and only `Read` full note content for the 1–2 blocks surfaced. The
skill depends on the repo's Python backend being importable (`interface.cli` +
`src/` + pydantic), which holds when running in this repo.

## 3. The store (`src/store.py`) — the shared data plane

```sql
files(file_uuid PK, file_path, file_name, creator, lang, mtime)
blocks(block_id PK, file_uuid FK, kind, level, text, span_start, span_end, parent_block_id)
nodes(node_id PK, kind, label, ref_block_id, file_uuid FK, lemma)
edges(src, dst, label, file_uuid,  PK(src,dst,label,file_uuid))
```

WAL journal mode; `INSERT OR IGNORE` for canonical nodes → **idempotent reindex**
(`upsert_file` deletes the file's rows then re-inserts). Deletions are soft
(`purge_file` hard-removes; orphans auto-pruned). Query API: `neighbors`,
`top_concepts`, `top_canonical`, `bridges`, `audit`, `vault_graph`,
`file_links`, `orphans`, `has_concepts`, `file_uuid_for`, `block_text_for`,
`search_blocks`.

`CREATE TABLE IF NOT EXISTS` leaves an existing table untouched, so added columns
need `_migrate_locked()` (an additive `ALTER TABLE`, guarded by
`PRAGMA table_info`). Old stores stay readable with the column NULL until a
reindex; every reader treats NULL as "not known" rather than assuming it's set.

**Cross-file link resolution lives here, not in the linker.** `linker.link` sees
one file at a time, so its `ref-to` rule could only match `[[target]]` against
headings *within the same file* — meaning a vault's actual `[[Note]]` links
produced no edges at all. Only the store knows what other notes exist, so
`file_links()` resolves them (alias/anchor/path forms normalised), re-extracting
from `blocks.text` at query time so there is no migration and nothing goes stale
against an edit.

**Concept identity vs node identity.** Nodes key on the surface `(text, pos)`, so
the graph says what the note says. Concept *queries* fold via `_concept_key` —
the lemma when known (which the tagger lowercases, so one key covers both
inflection and sentence-initial capitals), else the case-folded label. That is
why `Camouflage`/`camouflage` and `Tools`/`tool` are one concept while the nodes
stay distinct, and why 문어는/문어를 unify once 조사 are split.

## 4. Key invariants (all phases)

- External dependency = **`pydantic` only**; everything else stdlib. Heavier
  language adapters (spaCy/KoNLPy/MeCab/jieba) are opt-in via `NLM_MORPH`, never
  default. The default `AutoAdapter` routes by script — EN rule tagger for latin,
  `adapters_ko` 조사 splitter for Hangul — and is still zero-dependency; it
  delegates latin text to the very `RulePOSTagger` that used to be the default,
  so English output is byte-identical.
- **No-LM identity**: the POS tagger, CKY grammar, and arc-eager scorer are all
  hand rules. UAS ≈ 0.906 on a 9-sentence gold — but see §5 for what that does
  *not* mean on real prose.
- **English-only parsers are gated per sentence**, not per file, so an English
  quote inside a Korean note still parses. Non-EN sentences are counted
  (`skipped_lang`), never guessed at.
- Canonical nodes are content-deterministic → cross-file sharing is automatic.
- **Environment is checked, not assumed.** `src/preflight.py` runs before any
  model is built and reports a wrong Python (< 3.10) or pydantic (not 2) as a
  sentence — both otherwise surface as errors from *inside* pydantic that don't
  name the cause.
- **Least privilege on what it writes.** The store holds every block's text, so
  a fresh db is created `0600` (owner-only) before any data is written; `search`
  escapes LIKE wildcards so a user's term is never a pattern; the CLI never
  writes notes — it reports, the agent acts.

## 5. Known limitations (measured)

- **CKY phrase coverage is ~2% on real prose** (6/298 sentences across this
  repo's `docs/`; 1/15 on `octopus.md`) — one out-of-vocabulary word fails the
  whole sentence. An empty phrase list is the *normal* case. The gold UAS above
  measures the dependency parser on crafted sentences and says nothing about
  this. **`parsed_sentences` hides it** (it is satisfied by the dep parser alone,
  so it reads ~100%); `cky_trees`/`cky_coverage` are the honest counters.
- **Two metrics are structurally always zero** on real documents, kept only for
  contract stability: `saved_tokens`/`ratio` and `top_canonical` both count
  byte-identical duplicate *blocks*, which real prose has none of. Use
  `token_saved`/`ratio_token` and `top_concepts`.
- **Korean is 조사-only.** Verb/adjective endings (어미: 높고, 뛰어나다) are left
  alone; real 어미 handling needs an opt-in morphological analyzer. Korean gives
  reliable *noun* concepts, which is what concept linking runs on.
- **Concept folding is morphological, not semantic** — no synonyms, no spelling
  variants. It also conflates a proper noun with its common-noun homograph
  (Apple/apple), an accepted cost for *suggesting* links.
- Notes sharing a basename across folders resolve arbitrarily (`file_uuid_for`
  and link `_stem` matching are name-keyed; deterministic via `ORDER BY`, but
  Obsidian's path-proximity rule is not modelled).
- Decorations are heading/para-level (per-token DOM-offset mapping is future v2).
- The plugin is **desktop-only** (`isDesktopOnly: true`) — it spawns a child
  process; not mobile.
- The long-running `watch` subcommand is not wired into the plugin — Obsidian
  vault events (`vault.on("modify")`) re-trigger `index` through a debounce.

## 6. Verification surface

- **27 test files, 347 passed** (`.venv/bin/python -m pytest test/ -q`): Phase 0
  schema, Phase 1 blocks/links/MinHash, Phase 2 CKY/arc-eager/accuracy/
  parse-linker, Phase 3 preprocess/store/watchdog/CLI, the JSON-bridge tests
  (`test_phase3_cli_json.py`, `test_cli_view_command.py`,
  `test_cli_search_command.py`), and the behaviours it would be expensive to
  regress silently:
  - `test_lang_gate.py` — the EN parsers must never run on non-EN text. Regression
    for a silent-fabrication bug: every Korean token tagged `NN` made
    `("N","N") → compound` fire between adjacent words, so a Korean note produced
    a chain of confident-looking relations, one attached to a list bullet.
  - `test_honest_stats.py` — `cky_trees < parsed_sentences` on real prose, and
    `saved_tokens == 0` while `token_saved > 0`. Pins *which* number tells the
    truth.
  - `test_adapters_ko.py` — 조사 split, and what must **not** split (`사과` is not
    `사`+`과`).
  - `test_lemma_concepts.py` — inflection/case folding, the `-is/-us` guard
    (`octopus` must not become `octopu`), sentence-initial plurals, and that the
    sentence split never changes the token stream (node ids would move).
  - `test_top_concepts.py` / `test_bridges_audit.py` / `test_vault_graph.py` —
    concept linking across notes, already-linked exclusion, scope filters, and
    that links and suggestions stay disjoint.
  - `test_concept_relevance.py` — no suggestion crosses unrelated topics, a note
    about nothing suggests nothing, and the idf floor scales with vault size.
    Sharing a word is not evidence; this pins *which* words count.
- The 77-file Vault corpus ingests `--parse` with **0 crashes** (60,314 stored
  edges, 21,942 dep).
- Plugin build: `cd plugin && npm install && npm run build` → `plugin/main.js`
  (~72 KB, no bundled runtime deps) + synced `plugin/backend/`.

Cross-references: pipeline pipeline-design lives in the separate Obsidian vault
`~/Vault/Desinging Harness` (`ARCHITECTURE.md`, `ROADMAP.md`, phase docs).
