---
name: nl-miner
description: Answers questions about a set of Markdown notes that plain text search cannot — "which notes should link to each other", "what is orphaned", "what concepts recur across my vault", "map the notes about X". Indexes the notes into a block/concept graph (local, no LM) and compares every note pair by shared concepts weighted by rarity. Use for relationships and structure across many notes. For plain "find the notes mentioning X", use Grep instead — it is cheaper and needs no index.
---

# NL-Miner — index cheaply, query scoped

NL-Miner turns Markdown/plain text into a queryable graph in a SQLite db:

- **blocks** — the document split into headings/paragraphs/sentences/lists, with
  nesting and char spans. Language-independent, and the layer everything rests on.
- **concepts** — each `(word, pos)` collapses onto one canonical node shared by
  every block that uses it, so a word links the notes that discuss it.
- **links** — `[[wikilinks]]` resolved between notes.
- **parse** *(optional)* — CKY phrase + arc-eager dependency structure. English
  only, and low coverage on ordinary prose; see "What is worth trusting".

Querying only the blocks you need means the big documents never enter context
whole. The same CLI powers the Obsidian plugin backend and shares its database,
so a vault the plugin already indexed costs you almost nothing to refresh.

## The invoker — get this right first, it fails loudly otherwise

`interface.cli` must be importable. **You are usually not in the NL-Miner repo**
— you're in the user's vault — and running it there dies with
`ModuleNotFoundError: No module named 'interface'`. Pick whichever applies:

```sh
# 1. Vault that has the Obsidian plugin installed — the copy is already there.
#    Prefer this: it is the same backend the plugin uses, so no version skew.
cd "<vault>/.obsidian/plugins/nl-miner/backend" && \
  python -m interface.cli --db "<vault>/.nl-miner/nlp_graph.db" <sub> [args]

# 2. From anywhere, pointing at a repo checkout:
PYTHONPATH=<repo> <repo>/.venv/bin/python -m interface.cli --db <db> <sub> [args]

# 3. Inside the NL-Miner repo itself:
.venv/bin/python -m interface.cli --db <db> <sub> [args]
```

The interpreter needs **pydantic**. A system `python3` usually doesn't have it —
if you get `ModuleNotFoundError: pydantic`, use the repo's `.venv/bin/python`, or
read the plugin's `data.json` (`pythonPath`) for the one the user already
configured.

CRITICAL argparse rule: **`--db` must come BEFORE the subcommand** — the parent
parser owns it, not the subparsers. `nlm --db X index …` ✅; `nlm index … --db X` ❌.

## Index first, every time (do this before any query)

The shared, plugin-compatible location is `<root>/.nl-miner/nlp_graph.db` (for a
vault root) or any absolute path.

```sh
.venv/bin/python -m interface.cli --db <root>/.nl-miner/nlp_graph.db \
    index <root> --pos --json
```

For a throwaway analysis (no vault, one doc), a tmp db is fine:

```sh
.venv/bin/python -m interface.cli --db /tmp/nlm_<short>.db index <path> --pos --json
```

**Do this even when the database already exists.** `index` is incremental — it
compares each file's mtime to what the store recorded and reparses only what
changed, so a no-op reindex of an 80-note vault costs **~0.1s** against 4s for a
full one (`skipped_unchanged` in the JSON says how many it skipped). The cost of
skipping it is not "slightly stale": the user edits notes between your runs, and
`search` over a stale index returns **count 0 for text that is sitting in the
vault right now** — you would report "no notes mention X" with total confidence,
which is the worst answer a tool can produce. One cheap call removes that whole
class of failure.

Add `--force` after upgrading NL-Miner itself: mtime says a file is unchanged,
but the *tokenizer* may have changed under it, leaving rows built by old rules.

**Always pass `--pos`.** It builds the concept layer, it is cheap, and it is
language-independent. `search`/`query` work without it, but `bridge`, `audit`,
`vault` and `graph`'s concept list all need it and will **exit 1** telling you to
reindex. Add `--parse` *only* if the task is genuinely about grammar — it is
slower, English-only, and mostly finds nothing (below).

Verify with `graph --json` (files + concepts shared across notes).

## Use Grep for text. Use this for what Grep can't do.

Be honest about the tool you're holding. **Plain "find the notes mentioning X" is
Grep's job and Grep is cheaper**: measured on an 80-note vault, `grep -rn -C1`
cost ~1.9k tokens against ~4.2k for `index` + `search`, and Grep needs no index.
Reaching for this skill there spends tokens to lose.

What Grep **cannot** do at any price, because the answer isn't a string match:

- *"Which notes should link to each other?"* — that's every note pair (2,137 of
  them on 81 notes) compared on shared concepts, weighted by how rare each
  concept is in **this** vault. → `bridge`, `audit`
- *"What's orphaned?"* — needs resolved `[[wikilinks]]` across files. → `audit`
- *"What recurs across my notes?"* — needs concepts folded across case,
  inflection and 조사, not substrings. → `graph` → `top_concepts`
- *"Map the notes about X"* — → `vault --concept X`

On those, the alternative is reading the whole vault (~74k tokens) and still not
being able to compare 2,137 pairs by eye. `audit` answers it in ~7k. **That** is
the saving — not the search.

## Query catalogue — map the request to the right scoped command

| The user asks… | Run |
|---|---|
| "find blocks / notes mentioning X" | `search X --json` — add `--kind heading` to skim, `--limit N` (default 20) to widen/narrow |
| "what recurs across these notes / main themes" | `graph --json` → `top_concepts` (`n_files` = how many notes use it) |
| "what should this note link to" / "related notes" | `bridge <doc> --json` |
| "what's disconnected / vault health / orphans" | `audit --json` |
| "show me the notes about X" / "map this folder" | `vault --concept X --json` or `vault --folder <path> --json` |
| "what does this block reference / what links here" | `query <doc> --json` (get a block_id) → `query <doc> --block <id> --json` |
| "list the blocks in a file" | `query <doc> --json` |
| "phrase / dependency structure of this note" | `view <file> --parse --json` (needs no db) |
| "re-index / pick up my edits" | `index <path> --pos --json` (incremental + idempotent; `--force` after upgrading NL-Miner) |
| "remove a deleted doc from the index" | `purge <doc> --json` |

Every one returns a single JSON object on stdout (the CLI suppresses human text
under `--json`). Parse it; the answer is usually a few rows, not the document.

## Reading documents without loading them

This is the ladder. Take the cheapest rung that answers the question, and
**check the size first** — every rung has a fixed cost, so on a small file the
map exceeds the territory and you should just `Read` it.

| you want | run | cost on a real vault |
|---|---|---|
| "what's in this vault at all?" | `cards` | **2.6k** vs 74k to read 81 notes |
| "what's in this note?" | `outline <file>` | **0.6k** vs 20.5k to read it |
| "where does it say X?" | Grep | ~0.2k — cheaper than anything here |
| "read the part about X" | `outline` → `Read(file, offset, limit)` | ~1k |

```sh
outline <file>              # sections, sizes, line ranges — no body text
# then read only the section you want, by its line range:
#   Read(file, offset=<from_line>, limit=<to_line - from_line>)
```

`cards` gives one line per note: its headings and its **distinctive** keywords
(ranked by rarity in *this* vault, so they're what the note has and the others
don't). There is **no summary field** and there won't be — a summary needs
comprehension, i.e. an LLM this tool doesn't have, and generating one per note
would mean reading every note in full, spending the exact cost the card exists to
avoid. For a structured note the headings *are* the summary.

**Prefer the text output to `--json` for `cards`.** You are the consumer, not a
program: the JSON form costs 9.2k against 2.6k for the same information. Use
`--json` where you need to pick a field out (`search`, `bridge`, `audit`).

## Navigating a large note without loading it

`search` gives you pointers, not just text — use them. Each match carries a
`block_id` and a `[span_start, span_end)` into the source, so you can look a
block up, ask what it connects to, and read *only* that slice:

```sh
search <term> --json --limit 3          # → doc, block_id, span_start/span_end
query <doc> --block <block_id> --json   # → what that block links to (words summarised)
# then Read the file with offset/limit around the span — not the whole thing
```

Measured on a 20.5k-token note: 811 + 174 + 101 = **1.1k tokens, ~18× less than
reading it**. This is the pattern that pays, and it pays *per turn*: whatever you
read stays in the transcript and is re-sent for the rest of the conversation, so
a 20k-token file read on turn 3 of a 20-turn session is not a one-off cost.

**It only pays on big notes.** On a 400-token note the round-trip costs more than
just reading the file. Check the size first; small note → `Read` it.

`query --block` summarises word edges by default — a block links to every
distinct word it contains (~300 rows of content hashes, worth nothing to you).
`--tokens` opts into the full list; you almost never want it.

## Acting on what you find (the loop)

**This tool never writes notes.** It reports; you act with `Edit`/`Write`. That
split is deliberate — the user's vault is theirs, and a report that silently
rewrote it would be a different and much bigger promise.

When you apply a suggestion, **write the `link` field verbatim**:

```jsonc
{"name": "bluegreen.md", "link": "[[bluegreen]]", "score": 0.29, "shared": [...]}
```

Obsidian resolves `[[X]]` against the **file name**, not the note's heading. A
note titled `# 블루그린` in `bluegreen.md` is `[[bluegreen]]`; writing the obvious
`[[블루그린]]` links nothing and silently creates a **phantom note** — worse than
leaving it alone. `link` is that string already computed; don't derive your own.

Then close the loop, which is cheap and worth doing:

```sh
index <root> --pos          # ~0.1s, only your edit is reparsed
bridge <doc> --json         # the suggestion you applied should be gone
audit --json                # orphan count should have dropped
```

If a suggestion you applied still appears, the link didn't resolve — check it
against the file name before trying again.

## Token discipline (once you're here for the right reason)

- **Never read whole documents into context when a scoped query answers it.**
  `search`/`view`/`query --block` return exactly the blocks you asked about —
  that IS the answer; stop there.
- **But don't reach here for plain text search at all** — see the section above.
  Grep wins that one.
- **Only `Read` a full note** for the 1–2 blocks a query surfaced, and only if
  you need surrounding context. Use `span_start`/`span_end` from `view`/`search`
  to read a narrow slice of a huge note.
- **The default `--limit 20` is already the saving.** Each match carries its
  *whole* block (~770 chars on a real vault), so the response scales with the
  limit, not with the query. Measured on an 80-note vault against reading the 8
  matching files whole (35k tokens): limit 100 → 20% saved, 20 → 89%, 10 → 97%.
  Raise it only for a census, and prefer `--kind heading` to skim first.
- If `query --block` returns hundreds of neighbours, summarize counts, don't
  print all rows.
- Keep `--json` on every query so stdout is one parseable object.

## What is worth trusting (measured, not assumed)

Report these limits rather than presenting weak output as fact:

- **blocks + concepts are solid.** Language-independent; concept dedup runs
  21–48% on real corpora. Build answers on these.
- **CKY phrases are ~2% coverage on real prose** (6/298 sentences across this
  repo's own `docs/`). One out-of-vocabulary word fails the whole sentence, so
  `n_phrase_nodes == 0` is the *normal* case, not a bug. `cky_trees` /
  `cky_coverage` are the honest numbers — **`parsed_sentences` is not**: the
  dependency parser almost never fails, so it reads ~100% while CKY found
  nothing.
- **Dependencies are English-only.** Non-English sentences are skipped, not
  guessed, and counted in `skipped_lang`. A Korean note yields blocks and
  concepts but no `dep:*` edges — that is correct, not a failure.
- **Two metrics are structurally always zero** on real documents and are kept
  only for contract stability: `saved_tokens`/`ratio` (byte-identical duplicate
  *blocks*) and `top_canonical` (the same dup-block groups). Use `token_saved`/
  `ratio_token` and `top_concepts` instead.
- **Concept matching folds** case and inflection (`Tools`/`tools`/`tool` are one
  concept) and Korean 조사, 이다 and 들 (`문어는`/`문어를` → `문어`,
  `파장이다` → `파장`); Korean verb forms are not concepts (`처리하는` gives
  `처리`, `보냈다` gives nothing). It does **not** fold synonyms or spelling
  variants.
- **`bridge`/`audit` weight by document frequency, and are tuned for precision.**
  A shared word only counts if it's rare enough in *this* vault to mean
  something; code blocks contribute nothing; two informative concepts are the
  minimum. `score` is a **cosine in 0..1** (idf-weighted, length-normalised) and ranks the rows — use it, not `n_shared`: two notes sharing one rare term beat two sharing five ordinary ones. An empty
  bridge list means "nothing worth suggesting", not "query failed"; ~16% of
  genuinely related pairs are missed by design.

## When to fall back to plain Read

- The target isn't text NL-Miner handles: only `.md` and `.txt` are indexed
  (`src/filestructure.py` `DEFAULT_EXTS`). `view` does not check — handed a PDF
  or PNG it decodes the bytes as lossy UTF-8 and returns replacement characters
  with exit 0, so never point it at a binary.
- Indexing fails. If the first command prints "NL-Miner cannot run in this
  environment", it is telling you the interpreter is too old (< 3.10) or has the
  wrong pydantic (needs 2) — switch interpreters (the plugin's `data.json`
  `pythonPath`, or a repo `.venv`), don't retry the same one.
- The request is syntax-level on source code (use Grep/Glob directly).
