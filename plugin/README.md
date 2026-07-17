# NL-Miner Obsidian Plugin (Hybrid)

A TypeScript Obsidian plugin that drives the **NL-Miner** Python pipeline
(block → linked graph, with Phase 2 CKY constituency + arc-eager dependency
parsing) from inside Obsidian:

- **Index vault** → the bundled Python CLI builds/refreshes a SQLite graph at
  `<vault>/.nl-miner/nlp_graph.db`.
- **Graph view** → a force-directed visual of the current note's parse graph
  (blocks → phrase nodes → head tokens → dependency edges).
- **Query panel** → browse files → blocks → phrase/dependency breakdown and
  neighbour edges.
- **Inline annotations** → phrase chips and a `{n_phrases}p {n_deps}d` badge
  on rendered headings/paragraphs; click a chip to inspect the block.

## Architecture

Hybrid: the **UI is TypeScript** (Obsidian plugin); the **compute is Python**
(the verified NL-Miner core, vendored under `plugin/backend/`). They talk over
`python -m interface.cli ... --json` (one JSON object on stdout). No native
Node deps — only `d3-force` (bundled into `main.js`).

## Requirements

- Obsidian ≥ 1.4 (desktop; the plugin spawns a child process, so not mobile).
- A **Python 3** interpreter on your PATH (default `python3`), with `pydantic`:
  ```sh
  pip install -r backend/requirements.txt
  ```

## Build (dev)

The TS shell + a vendored Python backend:

```sh
cd plugin
npm install
npm run build        # sync-backend.mjs then esbuild -> main.js
```

`npm run sync` re-syncs `plugin/backend/` from the repo without rebuilding.

## Install into a vault

Copy the built plugin into your vault (or symlink for dev):

```sh
VAULT=~/Vault
mkdir -p "$VAULT/.obsidian/plugins/nl-miner"
cp main.js manifest.json styles.css -t "$VAULT/.obsidian/plugins/nl-miner/"
cp -r backend "$VAULT/.obsidian/plugins/nl-miner/"
pip install -r "$VAULT/.obsidian/plugins/nl-miner/backend/requirements.txt"
```

Then enable **NL-Miner** in Obsidian → Settings → Community plugins, and open
Settings → NL-Miner to point at your Python interpreter (default `python3`).

## Settings

- **Python interpreter** — path to `python`/`python3` (default `python3`).
- **Graph database** — SQLite path (default `<vault>/.nl-miner/nlp_graph.db`).
- **Parse (CKY + arc-eager)** — on by default; off falls back to block+POS only.
- **Auto-reindex on save** — off by default; debounced reindex of the saved note.
- **Advanced: use repo path for backend** — dev override; point at a checkout
  instead of the bundled `backend/`.

## Notes / limitations

- Phrase decorations are heading/paragraph-level for v1 (full per-token DOM
  mapping is a future v2).
- The rule parser is no-LM: irregular pasts and complex PP/coordination remain
  documented weak spots; such sentences simply don't surface phrases (graceful).
