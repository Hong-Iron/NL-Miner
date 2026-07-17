# NL-Miner

A rule-based (no-LM) pipeline: Markdown / plain text → **block tree → canonical
concepts → links between notes**, with an optional English phrase/dependency
parse on top. Shipped as two surfaces that share one Python core and one SQLite
store:

- **An Obsidian plugin** (TypeScript shell + vendored Python backend) — index a
  vault, read a note's structure as an outline, see which notes *should* link to
  each other, and audit what's orphaned.
- **A Claude Code skill** — an agent indexes a path once, then fetches only the
  blocks it needs (scoped queries, not whole files).

**347 tests green**, and the 77-file Vault corpus indexes with 0 crashes. Read
that as "the contracts hold and nothing falls over" — not as "every layer works
well". Those are different claims, and
[What the layers are actually worth](#what-the-layers-are-actually-worth) gives
the measured numbers, including the one that is 2%.

## Quick links

- **What it is & what it's worth** — [`docs/OVERVIEW.md`](docs/OVERVIEW.md) ← start here
- **Architecture & implementation** — [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- **Usage & installation** (Obsidian + Claude) — [`docs/USAGE.md`](docs/USAGE.md)
- **Deployment & distribution** — [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md)
- **Obsidian plugin** details — [`plugin/README.md`](plugin/README.md)
- **Claude Code skill** — [`.claude/skills/nl-miner/SKILL.md`](.claude/skills/nl-miner/SKILL.md)

## CLI quickstart (the bridge)

```sh
.venv/bin/python -m pytest test/ -q                       # 347 passed
# index a path — --pos builds the concept (token) layer; --parse adds CKY +
# arc-eager. Note: --db goes BEFORE the subcommand.
.venv/bin/python -m interface.cli --db /tmp/g.db index ./vault --pos --json
.venv/bin/python -m interface.cli --db /tmp/g.db search octopus --json      # scoped hits
.venv/bin/python -m interface.cli --db /tmp/g.db view ./octopus.md --parse --json   # per-file parse
# connectivity — what should link to what
.venv/bin/python -m interface.cli --db /tmp/g.db bridge octopus.md         # unlinked notes sharing concepts
.venv/bin/python -m interface.cli --db /tmp/g.db audit                     # orphans + unlinked pairs
.venv/bin/python -m interface.cli --db /tmp/g.db vault --folder 생물         # scoped hierarchical graph
.venv/bin/python -m interface.cli --db /tmp/g.db vault --concept 문어        # notes discussing one concept
```

Subcommands: `index` · `query` · `graph` · `watch` · `purge` · `view` · `search`
· `bridge` · `audit` · `vault` — each keeps human text by default and emits one
JSON object on stdout with `--json`. (`interface/cli.py`)

## How the vault graph differs from Obsidian's

Obsidian's graph is a flat force-directed cloud of every note: position means
nothing, the folder tree is discarded, and only links that already exist are
drawn. `vault` gives the three things it can't:

- **위계** — notes sit under their folders, indented. Layout is computed from
  document/folder order, not simulated, so nothing drifts between renders.
- **범위** — scope to a folder subtree (`--folder`) or to the notes that
  actually discuss one concept (`--concept`), instead of the whole vault at once.
- **suggestions, kept distinct** — real wikilinks render solid; *bridges*
  (shared concepts, no link yet) render dashed and only for the selected note. A
  suggestion must never look like a link.

## What counts as "related"

Sharing a word is not evidence — it depends entirely on *which* word. Two notes
both saying "use" or "note" are not related; two both saying "hemocyanin" are.
So a suggestion is weighted by **document frequency** (`ln(n_files / df)`):

- a concept in every note scores **0** and contributes nothing;
- past `MAX_DF_RATIO` of the vault it is dropped as vault-wide vocabulary;
- pairs rank by **summed idf**, not overlap size — one rare term beats a dozen
  ordinary ones;
- **code blocks contribute no concepts**, and a concept must be a *word* (not
  `--json`, `$DEST` or `plugin/main`).

This is self-calibrating, which a stoplist cannot be: the uninformative words
aren't a fixed list, they're whatever *your* vault repeats. On a mixed-topic
fixture (biology / cooking / git) this is **100% precision at 84% recall** — see
[OVERVIEW](docs/OVERVIEW.md#4-the-features-that-matter) for why that trade is the
right way round.

## What the layers are actually worth

Measured, not assumed — the numbers decide where the value is:

| layer | reality |
|---|---|
| blocks | works, language-independent; the whole UI renders off this alone |
| tokens (`--pos`) | works; 21–48% canonical dedup, and it powers `bridge`/`audit`/`top_concepts` |
| CKY phrases (`--parse`) | **~2% coverage on real prose** (6/298 sentences across `docs/`); EN-only |
| dep relations (`--parse`) | works on EN; skipped on non-EN (`skipped_lang`) rather than fabricated |

Two metrics look like headline wins but measure something that cannot fire on a
real corpus, and are kept only for contract stability — prefer the ones beside
them:

- `saved_tokens` / `ratio` count **byte-identical duplicate blocks** → always 0
  on real prose. Use `token_saved` / `ratio_token`.
- `top_canonical` ranks those same duplicate-block groups → always empty. Use
  `top_concepts`.
- `parsed_sentences` is satisfied by the dependency parser alone, so it reads
  ~100% while CKY finds nothing. Use `cky_trees` / `cky_coverage`.

## Korean

Works on a default install, no `NLM_MORPH` needed. `AutoAdapter` routes by
script; `src/adapters_ko.py` strips 조사 so 문어는/문어가/문어를 collapse onto one
`(문어, NN)` node — without which cross-note concept linking cannot fire on a
Korean vault at all. The EN-only parsers are gated off per sentence, so Korean
yields blocks + concepts but no (fabricated) grammar. 어미 are out of scope: real
verb morphology needs an opt-in KoNLPy/MeCab adapter.

## Layout

```
src/            verified core: preprocess, markdown_blocker, linker, cky,
                postagger, adapters_ko, transition_parser, store, context,
                schema, filestructure
interface/cli.py   the 10-subcommand bridge (JSON over stdout)
observer/       stat-poll watcher (stdlib, no watchdog pkg)
payload.py      JSONL serialization
grammar.cfg     EN NF2 CFG (CKY)
test/           27 test files, 347 passed
plugin/         Obsidian plugin (TS shell + vendored backend/)  →  main.js
.claude/skills/nl-miner/   the Claude Code skill (SKILL.md)
docs/           OVERVIEW / ARCHITECTURE / USAGE / DEPLOYMENT
```

## Requirements

- **Python ≥ 3.10** (the models use `str | None`) with **pydantic 2** — the only
  external dependency (`pip install -r requirements.txt` installs `pydantic>=2,<3`).
  A preflight names either problem in one sentence if the environment is wrong,
  rather than failing deep inside pydantic. Korean works out of the box
  (`src/adapters_ko.py`, zero deps); the *heavier* analyzers
  (spaCy/KoNLPy/MeCab/jieba) are opt-in via `NLM_MORPH`, never default.
- (plugin build) Node.js only — no bundled runtime deps, no native deps.
