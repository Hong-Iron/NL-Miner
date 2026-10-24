# NL-Miner — Usage & Installation

Two surfaces, one core. Pick the one you want and follow that section — most
people use **one** at a time.

- **In Obsidian** → [§A. Obsidian plugin](#a-obsidian-plugin)
- **In Claude Code** → [§B. Claude Code skill](#b-claude-code-skill)

For what the project is and what each layer is actually worth see
[OVERVIEW.md](OVERVIEW.md); for architecture see [ARCHITECTURE.md](ARCHITECTURE.md);
for packaging/shipping see [DEPLOYMENT.md](DEPLOYMENT.md).

---

## A. Obsidian plugin

A TypeScript plugin that indexes your vault into a connected NLP graph and shows
it in one panel with four tabs — **Note**, **Graph**, **Vault**, **Audit** — plus
inline concept chips on your notes. English or Korean.

### Prerequisites

- **Obsidian ≥ 1.4, desktop** (the plugin spawns a child process — not mobile).
- **Python ≥ 3.10 on your PATH**, with **pydantic 2**:
  ```sh
  python3 --version                                  # must be 3.10 or newer
  pip install -r plugin/backend/requirements.txt     # = pydantic>=2,<3
  ```
  Older Python or pydantic 1 is reported in plain language at startup (a
  preflight check) rather than as a stack trace — but avoiding it is better.
- (To build from source) **Node.js** for `npm install`.

### Install

1. Build the plugin (from the repo root):
   ```sh
   cd plugin && npm install && npm run build
   ```
   This runs `sync-backend.mjs` (copies `src/`, `interface/`, `observer/`,
   `payload.py`, `grammar.cfg`, `requirements.txt` → `plugin/backend/`) and then
   esbuilds `src/main.ts` → `plugin/main.js` (~72 KB, no bundled runtime deps).

2. Drop the artefacts into your vault's plugin folder:
   ```sh
   VAULT=~/Vault
   DEST="$VAULT/.obsidian/plugins/nl-miner"
   mkdir -p "$DEST"
   cp plugin/main.js plugin/manifest.json plugin/styles.css -t "$DEST/"
   cp -r plugin/backend "$DEST/"
   pip install -r "$DEST/backend/requirements.txt"
   ```
   *(For a dev setup without copying every build, see [DEPLOYMENT.md](DEPLOYMENT.md)
   §Dev-symlink workflow.)*

3. In Obsidian: **Settings → Community plugins** → disable "Safe mode" if needed
   → enable **NL-Miner**. Then **Settings → NL-Miner** and point **Python
   interpreter** at your `python`/`python3` (the **Detect** button finds it).

### Using it

**Ribbon icon (network)** — click → **Index vault** (one-time scan; re-runnable
after big changes). A notice reports files / edges / token-savings %.

**Commands** (Cmd/Ctrl-P) — each opens the one panel on the tab you asked for:
- *NL-Miner: Open panel* / *Open note graph* / *Open vault graph* / *Audit vault*
- *NL-Miner: Index vault* — full rescan.
- *NL-Miner: Reindex current note* — refresh just the open note.

Everything lives in **one window** in the right sidebar, with four tabs. Drag it
to the main area if you want it big — it stays there.

**Note tab** — the note as one tree: blocks nest by parent, and the selected
block expands in place. Everything is named in plain language — "naming chunk",
not `NP`; "who/what does it", not `nsubj` — with the symbol in the tooltip. It
also lists **notes worth linking**: notes sharing uncommon concepts with this one
that no link connects yet, with a button to insert `[[the link]]` at your cursor.

**Graph tab** — the note's **sections**. A heading and everything under it is a
box; prose collapses to one node (eight paragraphs are one fact about a section),
while quotes, code and tables stay individual satellites beside it, because those
are what you scan a document for. Drag to pan, Ctrl/Cmd + wheel to zoom. In a
narrow pane the parse column doesn't fit, so clicking a block hands off to the
Note tab instead.

**Vault tab** — folders → notes, indented, with real `[[wikilinks]]` as solid
arcs and *suggestions* (shared concepts, no link yet) as dashed ones, shown for
the selected note only. Scope to a folder subtree or to one concept. See
[README](../README.md#how-the-vault-graph-differs-from-obsidians).

**Audit tab** — orphan notes, and the pairs worth linking, strongest first.

**Inline decorations** — on rendered headings/paragraphs a counts badge plus
concept chips; click a chip → the Note tab focused on that block.

> The link-insert button is the plugin's **only** write to note content. It is
> always an explicit click, never automatic, and goes through the editor so
> Ctrl+Z undoes it. Everything else is read-only.

### Settings reference

| Setting | Default | Meaning |
|---|---|---|
| Language | Automatic | UI language for NL-Miner's own panels. **Automatic** follows Obsidian's UI language; English is the fallback. |
| Python interpreter | `python3` | Path to a Python with `interface.cli` importable + pydantic. **Detect** probes `python3`/`python`. |
| Graph database | `<vault>/.nl-miner/nlp_graph.db` | SQLite path. Shared with the Claude skill. |
| Parse (CKY + arc-eager) | on | Attach phrase + dependency structure. English-only and low-coverage on real prose (see below); turning it off costs you little and speeds indexing. The concept layer (`--pos`) is always built regardless, since bridges and the vault graph read it. |
| Auto-reindex on save | off | Debounced `vault.on("modify")` reindex of the saved note. |
| Reindex debounce (ms) | 1500 | Only editable when ≥ 100. |
| Advanced: backend path | (bundled) | Point at a repo checkout instead of `plugin/backend/` — handy for dev. |

### What you'll see — and what you won't

A crafted note like `# Notes\n\nThe octopus is an animal. The dog walked to the park.\n`
parses to **11 phrase nodes, 7 dep edges**: phrases include an `S` (head `is`)
nesting `NP`/`VP`; deps include `dep:det` (octopus → The), `dep:nsubj`, `dep:obj`.

Real prose is a different story, and the numbers say so plainly:

| | crafted note | `octopus.md` | this repo's `docs/` |
|---|---|---|---|
| CKY phrase coverage | 67% | **7%** (1/15 sentences) | **2%** (6/298) |
| dep edges | yes | yes | yes |
| concept dedup | — | 21% | 48% |

One out-of-vocabulary word fails a whole sentence's phrase parse, so **an empty
"뜻 덩어리" list is the normal case, not a broken index** — the panel says so
rather than leaving a bare `0`. Dependencies are far more robust (POS-rule based).
Build on **blocks and concepts**; treat phrases as a bonus when they appear.

**Korean** works on a default install: 조사 are split so 문어는/문어가/문어를 collapse
onto one concept, which is what lets concepts link Korean notes at all. The
copula and plural fold the same way (파장이다, 우체국들), and verb forms
(보냈다, 빠르게) are kept out of the concept list. The
English-only parsers are skipped per sentence rather than guessing, so a Korean
note yields blocks + concepts and **no** grammar — reported as `skipped_lang`,
not silently.

---

## B. Claude Code skill

A skill that teaches the agent to **index a doc set once, then query scoped**
instead of reading whole files — so big documents never enter context whole.

### Where it lives & how it's invoked

The skill is a project skill at `.claude/skills/nl-miner/SKILL.md` (committed to
this repo). Claude Code auto-routes to it when your request matches the
skill's description — phrasings like *"analyze/search/summarize these notes"*,
*"what does this doc reference"*, *"find the blocks that mention X"*. You can
also invoke it explicitly with `/nl-miner`.

It depends on the repo's Python backend being importable, so the agent runs the
CLI from the **NL-Miner repo root**: `.venv/bin/python -m interface.cli …`.

### The decision tree the skill follows

1. **Auto-detect the store.** If `<root>/.nl-miner/nlp_graph.db` exists (the
   plugin, or a previous run, warmed it) → **reuse it, skip indexing**. Only
   query from here on.
2. **Else index once**, into that location so a later plugin/skill run reuses it:
   ```sh
   .venv/bin/python -m interface.cli --db <root>/.nl-miner/nlp_graph.db \
       index <root> --pos --json
   ```
   (For a throwaway one-doc analysis, a `/tmp/nlm_*.db` is fine.)

   **`--pos`, not `--parse`.** `--pos` builds the concept layer: cheap,
   language-independent, and required by `bridge`/`audit`/`vault` and `graph`'s
   concept list — without it they exit 1 telling you to reindex. `--parse` is
   the expensive English-only grammar pass; add it only when the task is
   genuinely about grammar.

### Worked examples — map the question to the scoped query

| You ask | Agent runs | Loads into context |
|---|---|---|
| *"which notes mention octopus?"* | `--db <db> search octopus --json` | just the matching rows (doc + block + snippet) |
| *"what recurs across these notes?"* | `--db <db> graph --json` | files + `top_concepts` (with `n_files` each) |
| *"what should this note link to?"* | `--db <db> bridge <doc> --json` | candidate notes + the concepts they share |
| *"what's orphaned / vault health?"* | `--db <db> audit --json` | orphans + unlinked pairs |
| *"map the notes about X / this folder"* | `--db <db> vault --concept X --json` (or `--folder <path>`) | scoped notes + folders + links + suggestions |
| *"what links here / references this block?"* | `--db <db> query <doc> --json` → grab a `block_id` → `query <doc> --block <id> --json` | the block's neighbour edges |
| *"structure of this sentence/heading"* | `view <file> --parse --json` → take the matching block | one block's phrases + deps (no db needed) |
| *"re-index after I edited"* | `--db <db> index <path> --pos --json` | the per-file stats |
| *"this doc was deleted, drop it"* | `--db <db> purge <doc> --json` | `ok`/`purged` |

Every command takes `--json` and returns **one JSON object on stdout** — that
small object *is* the answer; the agent stops there and only `Read`s a full note
for the 1–2 blocks a query surfaced, if surrounding context is needed.

```sh
# end-to-end, from repo root:
.venv/bin/python -m interface.cli --db /tmp/nlm.db index ./octopus.md --pos --json
# → {"ok": true, "files": [{name, n_blocks, token_saved, cky_coverage, …}],
#    "totals": {ratio_token, cky_coverage, skipped_lang, …}}

.venv/bin/python -m interface.cli --db /tmp/nlm.db search "octopus" --json
# → {"db": "...", "pattern": "octopus", "count": 2, "matches": [{doc, block_id, kind, text, …}]}

.venv/bin/python -m interface.cli --db /tmp/nlm.db bridge octopus.md --json
# → {"doc": "...", "bridges": [{name, shared: [...], n_shared}]}

.venv/bin/python -m interface.cli view ./octopus.md --parse --json | head
# → parse graph: blocks[{phrases[], deps[]}], nodes[], edges[]   (no --db needed)
```

**Which numbers to read.** Prefer `token_saved`/`ratio_token` over
`saved_tokens`/`ratio`, and `top_concepts` over `top_canonical`: the latter of
each pair counts byte-identical duplicate *blocks*, which real prose never has,
so they read 0/empty always. Prefer `cky_trees`/`cky_coverage` over
`parsed_sentences` — that one is satisfied by the dependency parser alone, so it
reports ~100% while CKY found nothing. The pairs are kept only for contract
stability. See [README](../README.md#what-the-layers-are-actually-worth).

### Install the skill (if it isn't already where you want it)

- **For this repo**: it's already at `.claude/skills/nl-miner/` — nothing to do;
  it auto-loads when you use Claude Code here.
- **Globally (any repo)**: copy or symlink it into `~/.claude/skills/nl-miner/`
  and ensure a Python with `interface.cli` importable + pydantic is reachable.
  See [DEPLOYMENT.md](DEPLOYMENT.md) §Distribute the skill.
