// The single NL-Miner window.
//
// Everything used to be three ItemViews plus a modal, so reaching the vault
// graph meant finding its tab and the audit meant remembering a command. They
// are all the same job — inspecting what the index knows — so they belong in one
// pane you switch inside.
//
// The host owns the chrome (title, actions, tab bar) and one long-lived
// instance of each tab. Tabs keep their own state (selection, caches) across
// switches, which is why they are instances rather than functions: flipping to
// Vault and back must not lose the block you had selected, or re-fetch what was
// already fetched.

import { ItemView, WorkspaceLeaf } from "obsidian";

import type NLMinerPlugin from "../main";
import { t, type StringKey } from "../i18n";
import { NoteTab } from "./noteTab";
import { GraphTab } from "./graphTab";
import { VaultTab } from "./vaultTab";
import { AuditTab } from "./auditTab";

export const VIEW_TYPE_PANEL = "nl-miner-panel";

/** What the host needs from a tab. */
export interface Tab {
  render(container: HTMLElement): void | Promise<void>;
  /** pane resized — repaint from cached data, never refetch */
  onResize?(): void;
  /** the active note changed */
  onNoteChange?(): void;
  /** the index changed — drop caches */
  onReindex?(): void;
}

export type TabId = "note" | "graph" | "vault" | "audit";

const TABS: Array<{ id: TabId; key: StringKey }> = [
  { id: "note", key: "tab.note" },
  { id: "graph", key: "tab.graph" },
  { id: "vault", key: "tab.vault" },
  { id: "audit", key: "tab.audit" },
];

export class NLMinerPanel extends ItemView {
  private plugin: NLMinerPlugin;
  private active: TabId = "note";
  private tabs: Record<TabId, Tab>;
  private body!: HTMLElement;

  constructor(leaf: WorkspaceLeaf, plugin: NLMinerPlugin) {
    super(leaf);
    this.plugin = plugin;
    this.tabs = {
      note: new NoteTab(plugin),
      graph: new GraphTab(plugin),
      vault: new VaultTab(plugin),
      audit: new AuditTab(plugin),
    };
  }

  getViewType(): string {
    return VIEW_TYPE_PANEL;
  }
  getDisplayText(): string {
    return t("app.name");
  }
  getIcon(): string {
    return "network";
  }

  async onOpen(): Promise<void> {
    await this.render();
  }

  onResize(): void {
    this.tabs[this.active].onResize?.();
  }

  /** Show a tab, optionally with an argument for it (a block to focus, a
   *  concept to scope to). Public so the plugin's commands and one tab's
   *  actions can drive another — a concept chip in Note jumps to Vault. */
  async show(id: TabId, arg?: unknown): Promise<void> {
    this.active = id;
    if (arg !== undefined) {
      const tab = this.tabs[id] as { accept?: (a: unknown) => void };
      tab.accept?.(arg);
    }
    await this.render();
  }

  get current(): TabId {
    return this.active;
  }

  /** Repaint the active tab. Note-scoped tabs also drop per-note state. */
  async refresh(): Promise<void> {
    for (const tab of Object.values(this.tabs)) tab.onNoteChange?.();
    await this.render();
  }

  async refreshAfterReindex(): Promise<void> {
    for (const tab of Object.values(this.tabs)) tab.onReindex?.();
    await this.render();
  }

  private async render(): Promise<void> {
    const c = this.contentEl;
    c.empty();
    c.addClass("nl-miner-panel");

    this.renderActions(c);
    this.renderTabBar(c);
    this.body = c.createDiv({ cls: "nl-panel-body" });
    await this.tabs[this.active].render(this.body);
  }

  /** Actions that apply whatever tab you're on. Navigation is the tab bar's
   *  job, so only real verbs live here. */
  private renderActions(c: HTMLElement): void {
    const bar = c.createDiv({ cls: "nl-actions" });
    const reindex = bar.createEl("button", {
      cls: "clickable-icon",
      text: t("btn.reindex"),
    });
    reindex.addEventListener("click", () => this.plugin.reindexCurrent());
    const all = bar.createEl("button", {
      cls: "clickable-icon",
      text: t("btn.indexVault"),
    });
    all.addEventListener("click", () => this.plugin.indexVault());
  }

  private renderTabBar(c: HTMLElement): void {
    const bar = c.createDiv({ cls: "nl-tabbar" });
    for (const { id, key } of TABS) {
      const b = bar.createEl("button", {
        cls: "nl-tab" + (id === this.active ? " active" : ""),
        text: t(key),
      });
      b.addEventListener("click", () => {
        if (id === this.active) return;
        this.active = id;
        this.render().catch(() => {});
      });
    }
  }
}
