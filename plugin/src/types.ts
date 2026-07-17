// TypeScript mirrors of the Python CLI ``--json`` contract (see
// interface/cli.py). The plugin only consumes these; the shapes are pinned by
// test/test_phase3_cli_json.py and test/test_cli_view_command.py on the
// Python side. Keep these in lock-step with those tests — the bridge is a
// single stdout JSON object per command.

import type { LangSetting } from "./i18n";

export interface IndexJsonFile {
  name: string;
  n_blocks: number;
  n_nodes: number;
  n_edges: number;
  lang: string;
  raw_total_tokens: number;
  /** dup-block dedup — reads 0 on real prose (no two blocks are identical) */
  saved_tokens: number;
  token_raw: number;
  token_unique: number;
  /** canonical token collapse — the dedup that actually fires */
  token_saved: number;
  cky_trees: number;
  cky_coverage: number;
  skipped_lang: number;
  n_phrase_nodes: number;
  n_dep_edges: number;
}

export interface IndexJsonTotals {
  files: number;
  raw_tokens: number;
  saved_tokens: number;
  ratio: number;
  token_raw: number;
  token_saved: number;
  ratio_token: number;
  cky_trees: number;
  cky_attempted: number;
  cky_coverage: number;
  skipped_lang: number;
}

/** ``index --json`` → one object: ok/db/parse/pos + per-file rows + totals. */
export interface IndexJson {
  ok: boolean;
  db: string;
  parse: boolean;
  pos: boolean;
  files: IndexJsonFile[];
  totals: IndexJsonTotals;
}

/** ``query <doc> --json`` (no --block) → block list for one file. */
export interface BlockRow {
  block_id: string;
  kind: string;
  level: number | null;
  text: string;
  span_start: number;
  span_end: number;
  parent_block_id: string | null;
}
export interface QueryBlocksJson {
  doc: string;
  file_uuid: string;
  blocks: BlockRow[];
}

/** ``query <doc> --block <id> --json`` → neighbour edges for one block. */
export interface NbEdge {
  node: string;
  label: string;
}
export interface QueryBlockJson {
  doc: string;
  block: string;
  out: NbEdge[];
  in: NbEdge[];
}

/** ``graph --json`` → store summary + top canonical nodes. */
export interface GraphFile {
  name: string;
  file_uuid: string;
}
/** Duplicate-block groups — empty on real prose. Prefer `top_concepts`. */
export interface GraphTopCanonical {
  node_id: string;
  label: string;
  refs: number;
}
/** A word shared by several notes — the cross-note concept view. */
export interface GraphConcept {
  node_id: string;
  label: string;
  n_files: number;
  n_blocks: number;
}
export interface GraphJson {
  db: string;
  files: GraphFile[];
  top_canonical: GraphTopCanonical[];
  top_concepts: GraphConcept[];
}

/** ``view <doc> --json [--parse]`` → parse-graph snapshot (graph view + chips). */
export interface ViewPhrase {
  id: string;
  label: string;
  // ``head`` edge points at a token; surface text of the head is proxied so
  // the UI never needs to resolve token nodes separately.
  head_text: string;
}
export interface ViewDep {
  src_text: string;
  dst_text: string;
  label: string; // ``dep:<rel>``
}
export interface ViewBlock {
  block_id: string;
  kind: string; // heading | para | sentence | list | quote | code | table
  level: number | null;
  text: string;
  span: [number, number];
  parent_block_id: string | null;
  n_phrase: number;
  n_dep: number;
  phrases: ViewPhrase[];
  deps: ViewDep[];
}
export interface ViewNode {
  id: string;
  kind: string; // block | phrase | token
  label: string;
  ref_block_id: string | null;
}
export interface ViewEdge {
  src: string;
  dst: string;
  label: string; // token | contains | head | dep:<rel>
}
export interface ViewStats {
  n_blocks: number;
  n_nodes: number;
  n_edges: number;
  n_phrase_nodes: number;
  n_dep_edges: number;
  /** satisfied by the dependency parser alone — not constituency coverage */
  parsed_sentences: number;
  skipped_sentences: number;
  /** sentences where CKY actually produced a tree */
  cky_trees: number;
  cky_coverage: number;
  /** sentences the EN-only parsers were not run on */
  skipped_lang: number;
  token_raw: number;
  token_unique: number;
  token_saved: number;
  ratio_token: number;
  lang: string;
}
export interface ViewJson {
  doc: string;
  file_path: string;
  file_uuid: string;
  schema_version: string;
  parse: boolean;
  pos: boolean;
  stats: ViewStats;
  blocks: ViewBlock[];
  nodes: ViewNode[];
  edges: ViewEdge[];
}

/** ``bridge <doc> --json`` → notes sharing concepts with doc but not linked. */
export interface BridgeRow {
  file_uuid: string;
  name: string;
  /** informative concepts both notes use, most distinctive first */
  shared: string[];
  n_shared: number;
  /** summed idf — how much evidence there is, not how many words overlap */
  score: number;
}
export interface BridgeJson {
  doc: string;
  file_uuid: string;
  bridges: BridgeRow[];
}

/** ``audit --json`` → vault health. */
export interface AuditOrphan {
  name: string;
  file_uuid: string;
}
export interface AuditPair {
  a: string;
  b: string;
  shared: string[];
  n_shared: number;
  score: number;
}
export interface AuditJson {
  n_files: number;
  n_links: number;
  orphans: AuditOrphan[];
  unlinked_pairs: AuditPair[];
}

/** ``vault --json`` → scoped, hierarchical vault graph. */
export interface VaultNote {
  file_uuid: string;
  name: string;
  path: string;
  /** vault-relative folder, "" at the root — the hierarchy Obsidian's graph drops */
  folder: string;
  lang: string;
  n_blocks: number;
  concepts: string[];
}
export interface VaultLink {
  src: string;
  dst: string;
}
export interface VaultBridge {
  a: string;
  b: string;
  shared: string[];
  n_shared: number;
  score: number;
}
export interface VaultJson {
  scope: { kind: "vault" | "folder" | "concept"; value: string };
  root: string;
  folders: string[];
  notes: VaultNote[];
  /** real wikilinks */
  links: VaultLink[];
  /** suggestions only — never render these as if they were links */
  bridges: VaultBridge[];
}

/** Plugin settings; persisted via Plugin.loadData/saveData. */
export interface NLMinerSettings {
  /** UI language for NL-Miner's panels; ``auto`` follows Obsidian's. */
  language: LangSetting;
  pythonPath: string; // default ``python3``
  dbPath: string; // ``""`` → default <vault>/.nl-miner/nlp_graph.db
  parse: boolean; // CKY + arc-eager attach; off → block + POS only
  autoReindex: boolean; // debounced reindex of the saved note
  reindexDebounceMs: number;
  backendPath: string; // ``""`` → bundled <pluginDir>/backend
}

export const DEFAULT_SETTINGS: NLMinerSettings = {
  language: "auto",
  pythonPath: "python3",
  dbPath: "",
  parse: true,
  autoReindex: false,
  reindexDebounceMs: 1500,
  backendPath: "",
};
