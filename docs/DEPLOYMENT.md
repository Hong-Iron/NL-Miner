# NL-Miner — Deployment & Distribution

How **you** package, version, and ship NL-Miner. There are three shippable
artefacts and you usually distribute them independently:

| Artefact | For | Build step |
|---|---|---|
| The **Obsidian plugin** | Obsidian users | `cd plugin && npm run build` → `main.js` + vendored `backend/` |
| The **Claude Code skill** | Claude Code users | none — it's just `.claude/skills/nl-miner/` |
| The **Python core** | reuse as a library / CI | the `src/` + `interface/` + `payload.py` tree |

**Preliminaries — the environment every artefact needs:**

- **Python ≥ 3.10.** The data models annotate `str | None`, which pydantic
  evaluates at import; older Python raises a `TypeError` from inside pydantic.
- **pydantic 2** (`>=2,<3`). `payload.py` uses v2-only API; an unpinned install
  can resolve v1 and fail later in serialisation.
- Nothing else. No network, no model, no native extensions. `src/preflight.py`
  checks both at startup and reports a wrong environment as one sentence.
- Node.js is needed only to *build* the plugin, not to run it.

All paths below assume the repo root. For what the project is and what each
layer is worth see [OVERVIEW.md](OVERVIEW.md); for architecture see
[ARCHITECTURE.md](ARCHITECTURE.md); for usage see [USAGE.md](USAGE.md).

> 한국어판: [DEPLOYMENT.ko.md](DEPLOYMENT.ko.md). **This file is canonical** — if
> the two disagree, this one is right. Change both when a fact changes.

---

## 1. Build the plugin

```sh
cd plugin
npm install        # one-time; installs esbuild, typescript, obsidian types, @types/node
npm run build      # = sync-backend.mjs  →  esbuild (production) → plugin/main.js
```

What `npm run build` actually does:
1. `scripts/sync-backend.mjs` — copies `src/`, `interface/`, `observer/`,
   `payload.py`, `grammar.cfg`, `requirements.txt` → `plugin/backend/`
   (excludes `__pycache__`/`.venv`). **Re-runs on every build** so the vendored
   backend stays fresh with your repo edits.
2. `esbuild.config.mjs production` — bundles `src/main.ts` → `plugin/main.js`,
   leaving `obsidian`/`electron` external (no runtime deps to vendor)
   (`platform: node`, `format: cjs`, target `es2018`).

Build outputs: `plugin/main.js` (~89 KB). `plugin/backend/` is also a build
output but is intentionally committed (the plugin is self-contained).

> `npm run dev` runs sync + esbuild in **watch** mode for live dev.

---

## 2. Install into a vault (local distribution)

The minimal Obsidian plugin folder needs **`main.js` + `manifest.json` +
`styles.css` + `backend/`** (the Python core) — and a Python with pydantic on
PATH:

```sh
VAULT="$HOME/Vault"
DEST="$VAULT/.obsidian/plugins/nl-miner"
mkdir -p "$DEST"
cp plugin/main.js plugin/manifest.json plugin/styles.css -t "$DEST/"
cp -r plugin/backend "$DEST/"
pip install -r "$DEST/backend/requirements.txt"
```

Then in Obsidian → **Community plugins → enable NL-Miner**, and (once)
**Settings → NL-Miner → Python interpreter: Detect**.

> **The usual failure:** the system `python3` has no pydantic, so nothing works
> on the default setting. If Detect can't find one, point it at a virtualenv
> directly (e.g. `/path/to/repo/.venv/bin/python`).

### Dev-symlink workflow (no copy every build)

Symlink the plugin folder into your vault so `npm run build` (or `dev` watch)
is picked up live:

```sh
VAULT="$HOME/Vault"
DEST="$VAULT/.obsidian/plugins/nl-miner"
mkdir -p "$(dirname "$DEST")"
ln -s "$(pwd)/plugin" "$DEST"       # link the whole plugin dir
pip install -r plugin/backend/requirements.txt
```

Disable → enable the plugin in Obsidian (or the "Reload app without saving"
command) after each rebuild to pick up the new `main.js`; the `npm run dev`
watch re-emits `main.js` on save.

---

## 2b. Upgrading an existing install

Copying the new files over an old install is enough — with two caveats that are
handled for you, listed here so the behaviour isn't a surprise:

- **Retired view types.** Obsidian records open panes by view type in
  `workspace.json`. Before 0.2.0 the plugin registered three views
  (`nl-miner-query`, `nl-miner-graph`, `nl-miner-vault`); they are now tabs
  inside one `nl-miner-panel`. On load the plugin detaches leaves of the retired
  types (`RETIRED_VIEW_TYPES` in `main.ts`), so the user doesn't inherit dead
  "No view of type…" tabs that return on every restart. **Never reuse one of
  those names for a live view.**
- **Store schema.** `GraphStore._migrate_locked()` adds missing columns
  (`PRAGMA table_info` + `ALTER TABLE`) because `CREATE TABLE IF NOT EXISTS`
  leaves an existing table alone. An old database keeps working with the new
  column NULL; readers treat NULL as "not known".

**Tell users to re-run *Index vault* after upgrading.** Nothing breaks without
it, but a database built by an older tokenizer carries stale rows — no lemmas
(so `tool`/`tools` won't fold) and markdown/code tokens that newer versions
filter only at query time. A reindex is seconds per hundred notes.

*Index vault* is incremental — it skips notes whose mtime hasn't changed — so it
does not refresh those rows. After an upgrade that changes the tokenizer (the
Korean 조사/이다/들 rules did), reindex once from the CLI with `--force`:
`python -m interface.cli --db <db> index <vault> --pos --force`.

---

## 3. Versioning (keep three files in lock-step)

Bump the version in **all three** before a release — Obsidian's auto-updater
cross-checks them:

- `plugin/manifest.json` → `version` (e.g. `0.2.0`) — this is the canonical
  version Obsidian reads.
- `plugin/versions.json` → map `"<version>": "<minAppVersion>"` — tells
  Obsidian which minApp goes with each release (e.g. `"0.2.0": "1.4.0"`).
- `plugin/package.json` → `version` — keep it equal for tooling hygiene.

```sh
# bump example 0.1.0 → 0.2.0
sed -i 's/"version": "0.1.0"/"version": "0.2.0"/' plugin/manifest.json plugin/package.json
# versions.json: add the 0.2.0 → minAppVersion entry
```

`main.js` is gitignored (build output); everything else under `plugin/` is
committed (manifest, styles, src, scripts, backend vendoring, README).

---

## 4. Release a plugin version on GitHub (the update channel)

Obsidian updates a plugin from your repo's **GitHub Releases**, not from `main`.
For each release:

1. Build clean: `cd plugin && npm install && npm run build`.
2. Commit, tag, push:
   ```sh
   git tag -a v0.2.0 -m "v0.2.0"
   git push origin v0.2.0
   ```
3. Create a **GitHub Release** for the tag and attach these **three files** as
   release assets (Obsidian downloads `<…/releases/latest/download/<file>`):
   - `plugin/main.js`
   - `plugin/manifest.json`
   - `plugin/styles.css`

   *(Do not zip them — Obsidian fetches the files directly.)*
4. `versions.json` already maps the new version → minAppVersion so existing
   installs auto-update.

---

## 5. Publish to the Obsidian community plugin browser

To be discoverable via Obsidian → Community plugins → Browse:

1. Your repo is public and has the build files committed/Released as above.
2. Open a PR against `obsidianmd/obsidian-releases` adding an entry to
   `community-plugins.json`:
   ```jsonc
   {
     "id": "nl-miner",
     "name": "NL-Miner",
     "author": "NL-Miner",
     "description": "Find the notes that should link to each other. Indexes your vault into a block/concept graph locally (no LM, no network), suggests unlinked notes that share uncommon concepts, and audits what's orphaned. Korean and English.",
     "repo": "yourname/nl-miner"
   }
   ```
   Keep the `description` byte-identical to `manifest.json`'s — they are two
   copies of the same sentence and a reviewer will notice the drift.

   The review is manual (the Obsidian team checks for security / sandboxing /
   the plugin does what it claims). "Does what it claims" is why §8's rule about
   the description matters: claim the parse layer and a reviewer opening a real
   note sees an empty phrase list. Until merged, users install manually (§2).

### Beta distribution via BRAT

For faster beta cycles without store review, ship via the
**BRAT** (Beta Reviewers Auto-update Tool) plugin: add your repo URL to BRAT,
and it installs/updates from your GitHub Releases. BRAT requires the same
`main.js` + `manifest.json` + `styles.css` Release assets.

---

## 6. Distribute the Claude Code skill

The skill is plain files — no build.

- **Project-scoped (default):** it lives at `.claude/skills/nl-miner/SKILL.md`,
  committed to this repo. Anyone who opens the repo in Claude Code gets it
  automatically — nothing to ship beyond committing it.
- **Personal/global:** copy or symlink into the user-wide skills dir so it's
  available in any repo:
  ```sh
  mkdir -p ~/.claude/skills
  ln -s "$(pwd)/.claude/skills/nl-miner" ~/.claude/skills/nl-miner
  ```
  It still needs a Python with `interface.cli` importable (`interface/`,
  `src/`, `payload.py`) + pydantic — so either keep it inside this repo or set
  `PYTHONPATH` to the repo root. For a fully portable personal skill, vendor a
  `backend/` next to the skill and have the SKILL.md invoke that.

---

## 7. Release checklist

A copy-paste you can tick through per release:

```
[ ] all code committed & pushed to a version branch
[ ] plugin/manifest.json version bumped
[ ] plugin/versions.json  <new-version> -> <minAppVersion>  added
[ ] plugin/package.json version bumped (matches)
[ ] cd plugin && npm install && npm run build   → main.js produced, no errors
[ ] cd plugin && npx tsc --noEmit -p tsconfig.json   → no type errors
[ ] .venv/bin/python -m pytest test/ -q          → 424 passed (no regression)
[ ] manual smoke, exactly as the plugin invokes it (cwd = the vendored backend):
      (cd plugin/backend && python -m interface.cli view ../../octopus.md --parse --json | head -c 200)
[ ] manual smoke, the connectivity path (needs --pos):
      python -m interface.cli --db /tmp/smoke.db index ./docs --pos && \
      python -m interface.cli --db /tmp/smoke.db audit
[ ] manifest.json description still true? (§8)
[ ] LICENSE present at repo root (Obsidian store requires it)
[ ] fresh clone builds: git clone → cd plugin && npm i && npm run build → main.js, no errors
[ ] git tag -a v<x.y.z> && git push origin v<x.y.z>
[ ] GitHub Release for the tag, attach main.js + manifest.json + styles.css
[ ] (if community store) PR to obsidian-releases community-plugins.json
[ ] (claude skill) .claude/skills/nl-miner/SKILL.md committed; bump any
    changed CLI examples to match the current subcommands
```

---

## 8. Notes & gotchas

- **`main.js` is never committed** (gitignored build output); it only enters the
  world via §2 (local copy) or §4 (Release asset). The vendored `plugin/backend/`
  **is** committed so a fresh git clone + `npm run build` is self-contained.
- **`--db` before the subcommand** is a hard argparse contract — if you write
  install/hook scripts that call the CLI, respect it (the plugin's `Backend`
  does; the skill's examples do).
- The plugin is **desktop-only** (`isDesktopOnly: true`) — the store will reject
  a mobile claim, and the bridge can't spawn Python on mobile anyway.
- Don't bundle a Python interpreter for v1 (the store review is friendlier to
  "uses the user's PATH Python"); per-platform wheels are a future option.
- **A repo-root `LICENSE` is required** by the Obsidian community-plugin review
  (and by anyone reusing the code). This project is MIT; `package.json` declares
  it and `LICENSE` is the canonical text. Keep them in agreement.
- **No nested `.git` under `plugin/`.** An early `git init` there once made git
  treat `plugin/` as a submodule and silently refuse to commit its files. If
  `git add plugin/` errors with "does not have a commit checked out", that's it —
  `rm -rf plugin/.git`.
- **`manifest.json`'s `description` is the plugin browser's copy** — it is the
  only thing most people read before installing. Keep it about what the plugin
  *does for them* ("find the notes that should link"), not about the pipeline,
  and never let it advertise a layer that doesn't deliver: this project's parse
  layer covers ~2% of real prose (see [OVERVIEW.md](OVERVIEW.md#3-what-each-layer-is-actually-worth)),
  so a description leading with "phrase & dependency parse" would be a promise
  the plugin can't keep.
- **Privacy is a feature and belongs in the pitch**: no model, no network calls —
  the backend is a local child process and the store is a file in the vault.
