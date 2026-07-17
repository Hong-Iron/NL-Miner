// NL-Miner Obsidian plugin entry. Wires commands, the graph view, the query
// side panel, the markdown post-processor (phrase chips + badge), and an
// opt-in debounced vault-on-modify reindex. Holds a short-lived cache of
// `view --json` snapshots so the graph view, query panel, and decorations
// share one parse per file per cache window.

import {
  Plugin,
  TFile,
  TAbstractFile,
  Notice,
  type WorkspaceLeaf,
} from "obsidian";

import { Backend } from "./backend";
import { NLMinerSettingTab } from "./settings";
import { Decorator } from "./decorations";
import { NLMinerPanel, VIEW_TYPE_PANEL, type TabId } from "./views/panel";
import { resolveLang, setLang, t } from "./i18n";
import {
  DEFAULT_SETTINGS,
  type NLMinerSettings,
  type ViewJson,
} from "./types";
import {
  debounce,
  vaultRoot,
  absFor,
  isIndexable,
  type Debounced,
} from "./util";

const CACHE_TTL_MS = 5000;

/** View types this plugin used to register, kept only so their stale leaves can
 *  be cleaned up on upgrade. Never re-add one of these names to a live view. */
const RETIRED_VIEW_TYPES = [
  "nl-miner-query",
  "nl-miner-graph",
  "nl-miner-vault",
];

export default class NLMinerPlugin extends Plugin {
  settings: NLMinerSettings = DEFAULT_SETTINGS;
  backend!: Backend;
  decorator!: Decorator;

  private applyReindex?: Debounced<(f: TFile) => void>;
  private viewCache = new Map<string, { ts: number; snap: ViewJson }>();
  /** pending listeners, to dedupe concurrent view() calls per file. */
  private viewWaiters = new Map<string, Array<(v: ViewJson | null) => void>>();
  private viewInflight = new Set<string>();

  async onload(): Promise<void> {
    await this.loadSettings();
    this.applyLanguage();
    this.backend = new Backend({
      settings: this.settings,
      app: this.app,
      pluginId: this.manifest.id,
    });
    this.decorator = new Decorator(this);

    this.registerView(VIEW_TYPE_PANEL, (leaf) => new NLMinerPanel(leaf, this));
    this.app.workspace.onLayoutReady(() => this.dropRetiredViews());

    this.addRibbonIcon("network", "NL-Miner", () => this.showTab("note"));

    this.addCommand({
      id: "nl-miner-index-vault",
      name: "Index vault",
      callback: () => this.indexVault(),
    });
    this.addCommand({
      id: "nl-miner-reindex-note",
      name: "Reindex current note",
      editorCallback: () => this.reindexCurrent(),
    });
    // One command per tab: the panel is one window, but the palette should
    // still take you straight to the thing you asked for.
    this.addCommand({
      id: "nl-miner-open-panel",
      name: "Open panel",
      callback: () => this.showTab("note"),
    });
    this.addCommand({
      id: "nl-miner-open-graph",
      name: "Open note graph",
      callback: () => this.showTab("graph"),
    });
    this.addCommand({
      id: "nl-miner-open-vault-graph",
      name: "Open vault graph",
      callback: () => this.showTab("vault"),
    });
    this.addCommand({
      id: "nl-miner-audit-vault",
      name: "Audit vault (orphans + unlinked notes)",
      callback: () => this.showTab("audit"),
    });

    this.registerMarkdownPostProcessor((el, ctx) =>
      this.decorator.process(el, ctx),
    );
    this.applyAutoReindex();
    // Register the modify listener exactly once; applyAutoReindex then only
    // swaps the (debounced) handler it calls — avoids stacking duplicate
    // listeners when settings toggles rebuild the debounce.
    this.registerEvent(
      this.app.vault.on("modify", (f: TAbstractFile) => {
        if (f instanceof TFile) this.applyReindex?.(f);
      }),
    );
    // One note-switch listener (refreshes open graph + query views), debounced
    // so a rapid pane click doesn't pile concurrent async rebuilds.
    this.registerEvent(
      this.app.workspace.on("active-leaf-change", () => this.refreshOnActive()),
    );

    this.addSettingTab(new NLMinerSettingTab(this.app, this));
  }

  /** Detach leaves left by view types that no longer exist.
   *
   *  Obsidian persists open leaves by view type in workspace.json. Before 0.2.0
   *  this plugin registered three; they are now tabs inside one panel. Without
   *  this, updating leaves the old tabs behind as dead "No view of type
   *  nl-miner-graph" panes that the user has to hunt down and close — and they
   *  come back every restart, because the workspace file still names them. */
  private dropRetiredViews(): void {
    for (const type of RETIRED_VIEW_TYPES) {
      for (const leaf of this.app.workspace.getLeavesOfType(type)) leaf.detach();
    }
  }

  onunload(): void {
    this.viewCache.clear();
    this.viewInflight.clear();
    this.viewWaiters.clear();
  }

  async loadSettings(): Promise<void> {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
  }

  async saveSettings(): Promise<void> {
    await this.saveData(this.settings);
    this.backend.setSettings(this.settings);
  }

  /** Resolve the language setting and repaint. Language is module state in
   *  ./i18n rather than threaded through every call, so switching it is: set,
   *  then re-render — no pane can be left half-translated. */
  applyLanguage(): void {
    setLang(resolveLang(this.settings.language));
    this.refreshViews();
    this.refreshVaultViews();
  }

  // ---- vault-wide index -------------------------------------------------

  async indexVault(): Promise<void> {
    const root = vaultRoot(this.app);
    if (!root) {
      new Notice(t("notice.needLocalVault"), 8000);
      return;
    }
    const status = new Notice(t("notice.indexing"), 0);
    try {
      const r = await this.backend.index(root, { parse: this.settings.parse });
      status.hide();
      const edges = r.files.reduce((a, f) => a + f.n_edges, 0);
      // Report the token dedup, not totals.ratio: that one measures
      // byte-identical duplicate blocks and so reads "saved 0%" on every real
      // vault. Flag low constituency coverage instead of hiding it — the EN
      // grammar finds nothing on most prose, and a silent 0 phrases looks like
      // a broken index rather than a known limit.
      const tot = r.totals;
      const parts = [
        t("notice.indexed", { files: tot.files, edges }),
        t("notice.dedup", { pct: (tot.ratio_token * 100).toFixed(0) }),
      ];
      if (this.settings.parse && tot.cky_attempted > 0) {
        parts.push(t("notice.cky", { pct: (tot.cky_coverage * 100).toFixed(0) }));
      }
      if (tot.skipped_lang > 0) {
        parts.push(t("notice.skippedLang", { n: tot.skipped_lang }));
      }
      new Notice(`NL-Miner: ${parts.join(" · ")}`, 8000);
      this.decorator.invalidate();
      this.refreshViews();
      this.refreshVaultViews();
    } catch (e) {
      status.hide();
      console.error("[nl-miner] index vault failed", e);
    }
  }

  async reindexCurrent(): Promise<void> {
    const file = this.app.workspace.getActiveFile();
    if (!file) return;
    // Reachable from the command palette and the view buttons while an image
    // or PDF is focused; the backend has no text stream for those.
    if (!isIndexable(file.path)) {
      new Notice(
        t("err.notTextNotice", { name: file.name }),
        5000,
      );
      return;
    }
    const abs = absFor(this.app, file.path);
    try {
      await this.backend.index(abs, { parse: this.settings.parse });
      this.invalidateView(file.path);
      this.decorator.invalidate(file.path);
      this.refreshViews();
      this.refreshVaultViews();
      new Notice(t("notice.reindexed", { name: file.name }), 3000);
    } catch (e) {
      console.error("[nl-miner] reindex failed", e);
    }
  }

  // ---- auto reindex on save ---------------------------------------------

  applyAutoReindex(): void {
    if (this.applyReindex) {
      this.applyReindex.cancel();
      this.applyReindex = undefined;
    }
    const fn = debounce((file: TFile) => {
      if (!this.settings.autoReindex) return;
      if (!isIndexable(file.path)) return;
      const abs = absFor(this.app, file.path);
      this.backend
        .index(abs, { parse: this.settings.parse })
        .then(() => {
          this.invalidateView(file.path);
          this.decorator.invalidate(file.path);
          this.refreshViews();
          this.refreshVaultViews();
        })
        .catch((e) => console.error("[nl-miner] auto-reindex failed", e));
    }, this.settings.reindexDebounceMs);
    this.applyReindex = fn;
  }

  // ---- views ------------------------------------------------------------

  /** Every open panel (normally one). */
  private panels(): NLMinerPanel[] {
    return this.app.workspace
      .getLeavesOfType(VIEW_TYPE_PANEL)
      .map((l) => l.view as NLMinerPanel);
  }

  /** The active note changed. */
  refreshViews(): void {
    for (const p of this.panels()) p.refresh().catch(() => {});
  }

  /** The index changed — tabs must drop cached backend results, not just
   *  repaint. Kept separate from refreshViews, which fires on every pane click
   *  and must stay cheap. */
  refreshVaultViews(): void {
    for (const p of this.panels()) p.refreshAfterReindex().catch(() => {});
  }

  private refreshOnActive = debounce((): void => this.refreshViews(), 150);

  /** Reveal a view, opening it in the right sidebar if it isn't open yet.
   *
   *  Every NL-Miner view lives alongside the note rather than taking over a
   *  main-area tab — they are all things you consult *while* reading, the way
   *  Obsidian's own outline and backlinks panes do.
   *
   *  An already-open leaf is reused wherever it currently is, so a view the
   *  user dragged out to the main area stays there; and repeated clicks reveal
   *  the one view instead of stacking duplicate tabs. */
  private async revealView(type: string): Promise<WorkspaceLeaf | null> {
    const existing = this.app.workspace.getLeavesOfType(type);
    const leaf = existing[0] ?? this.app.workspace.getRightLeaf(false);
    if (!leaf) return null;
    if (existing.length === 0) {
      await leaf.setViewState({ type, active: true });
    }
    this.app.workspace.revealLeaf(leaf);
    return leaf;
  }

  /** Reveal the panel on a given tab, optionally handing the tab an argument
   *  (a block to focus, a concept to scope to). This is the one entry point:
   *  commands, the ribbon, and one tab linking to another all go through it. */
  async showTab(id: TabId, arg?: unknown): Promise<void> {
    const leaf = await this.revealView(VIEW_TYPE_PANEL);
    if (!leaf) return;
    await (leaf.view as NLMinerPanel).show(id, arg);
  }

  /** Cached `view --json` lookup keyed by absolute path; de-dupes concurrent
   *  calls. Throws propagate (callers catch). */
  async getView(absPath: string): Promise<ViewJson | null> {
    const hit = this.viewCache.get(absPath);
    if (hit && Date.now() - hit.ts < CACHE_TTL_MS) return hit.snap;

    if (this.viewInflight.has(absPath)) {
      return new Promise<ViewJson | null>((resolve) => {
        const list = this.viewWaiters.get(absPath) ?? [];
        list.push(resolve);
        this.viewWaiters.set(absPath, list);
      });
    }
    this.viewInflight.add(absPath);
    try {
      const snap = await this.backend.view(absPath, {
        parse: this.settings.parse,
      });
      this.viewCache.set(absPath, { ts: Date.now(), snap });
      const waiters = this.viewWaiters.get(absPath) ?? [];
      for (const w of waiters) w(snap);
      return snap;
    } catch (e) {
      const waiters = this.viewWaiters.get(absPath) ?? [];
      for (const w of waiters) w(null);
      throw e;
    } finally {
      this.viewWaiters.delete(absPath);
      this.viewInflight.delete(absPath);
    }
  }

  invalidateView(relPath?: string): void {
    if (relPath) this.viewCache.delete(absFor(this.app, relPath));
    else this.viewCache.clear();
  }

  /** Like getView but tolerant — never throws (used by decorations). */
  async getViewSafe(absPath: string): Promise<ViewJson | null> {
    try {
      return await this.getView(absPath);
    } catch {
      return null;
    }
  }
}
