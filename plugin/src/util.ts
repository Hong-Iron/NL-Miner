// Small helpers: vault-root discovery, default paths, debounce.
// Obsidian's local-fs adapter exposes getBasePath(); we cast to any because the
// typed adapter interface is filesystem-agnostic and doesn't promise it.

import type { App } from "obsidian";

export const PLUGIN_ID = "nl-miner";

/** The only extensions the backend indexes — must stay in lock-step with
 *  src/filestructure.py DEFAULT_EXTS, which is what ``discover()`` walks.
 *
 *  Every entry point (views, commands, auto-reindex, the markdown post-
 *  processor) gates on this rather than trusting whatever file happens to be
 *  active. Two reasons it has to be an allowlist:
 *    * ``view`` does *not* filter by extension — handed a PNG it decodes the
 *      bytes as lossy UTF-8 and returns a block of replacement characters, so
 *      without this gate the panel would render binary garbage.
 *    * ``index`` *does* filter, so a file we accept here but ``discover()``
 *      skips would leave the panel stuck on "index first" forever. That is why
 *      ``.markdown`` is absent: the backend doesn't index it, and Obsidian
 *      doesn't open it as a note either. */
const INDEXABLE_RE = /\.(md|txt)$/i;

export function isIndexable(path: string | null | undefined): boolean {
  return !!path && INDEXABLE_RE.test(path);
}

export function vaultRoot(app: App): string | null {
  const adapter = app.vault.adapter as unknown as {
    getBasePath?: () => string;
  };
  return typeof adapter.getBasePath === "function"
    ? adapter.getBasePath()
    : null;
}

export function joinPath(root: string | null, rel: string): string {
  return root ? `${root}/${rel}` : rel;
}

/** Absolute vault path for a TFile relative path (returns rel if no fs root). */
export function absFor(app: App, rel: string): string {
  const root = vaultRoot(app);
  return root ? `${root}/${rel}` : rel;
}

export function defaultDbPath(app: App): string {
  const root = vaultRoot(app);
  return root
    ? `${root}/.nl-miner/nlp_graph.db`
    : "nlp_graph.db";
}

export function defaultBackendDir(app: App): string {
  const root = vaultRoot(app);
  return root
    ? `${root}/.obsidian/plugins/${PLUGIN_ID}/backend`
    : `backend`;
}

export interface Debounced<T extends (...args: never[]) => void> {
  (...args: Parameters<T>): void;
  cancel(): void;
}

export function debounce<T extends (...args: never[]) => void>(
  fn: T,
  ms: number,
): Debounced<T> {
  let timer: ReturnType<typeof setTimeout> | null = null;
  const wrapped = ((...args: Parameters<T>) => {
    if (timer !== null) clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  }) as Debounced<T>;
  wrapped.cancel = () => {
    if (timer !== null) clearTimeout(timer);
    timer = null;
  };
  return wrapped;
}
