// Audit tab — the whole-vault counterpart to the per-note bridge list.
//
// Two findings, both about connectivity rather than grammar:
//   orphans        notes no wikilink reaches or leaves — written and lost
//   unlinked pairs notes that share concepts but aren't linked, strongest first

import { Notice, TFile } from "obsidian";

import type NLMinerPlugin from "../main";
import type { AuditJson } from "../types";
import { t as tr } from "../i18n";
import type { Tab } from "./panel";

function stripExt(name: string): string {
  return name.replace(/\.(md|markdown|txt)$/i, "");
}

export class AuditTab implements Tab {
  private plugin: NLMinerPlugin;
  private data: AuditJson | null = null;
  private error = "";
  private c!: HTMLElement;

  constructor(plugin: NLMinerPlugin) {
    this.plugin = plugin;
  }

  private get app() {
    return this.plugin.app;
  }

  onReindex(): void {
    this.data = null;
  }

  async render(container: HTMLElement): Promise<void> {
    this.c = container;
    container.empty();
    container.addClass("nl-miner-audit");
    if (this.data) {
      this.paint();
      return;
    }
    container.createDiv({ cls: "nl-miner-meta", text: tr("empty.loading") });
    try {
      this.data = await this.plugin.backend.audit();
      this.error = "";
    } catch (e) {
      // most likely a store indexed without the token layer
      this.error = tr("err.audit");
      console.error("[nl-miner] audit failed", e);
    }
    this.paint();
  }

  private paint(): void {
    const c = this.c;
    c.empty();
    if (this.error) {
      c.createDiv({ cls: "nl-miner-empty-text", text: this.error });
      return;
    }
    const d = this.data;
    if (!d) return;

    c.createDiv({
      cls: "nl-miner-meta",
      text: tr("audit.meta", { files: d.n_files, links: d.n_links }),
    });

    this.renderOrphans(c, d);
    this.renderPairs(c, d);
  }

  private renderOrphans(c: HTMLElement, d: AuditJson): void {
    const sec = c.createDiv({ cls: "nl-section" });
    sec.createEl("div", {
      cls: "nl-section-title",
      text: tr("audit.orphans", { n: d.orphans.length }),
    });
    sec.createEl("div", {
      cls: "nl-miner-meta",
      text: tr("audit.orphans.sub"),
    });
    if (d.orphans.length === 0) {
      sec.createDiv({ cls: "nl-miner-meta", text: tr("audit.orphans.none") });
      return;
    }
    for (const o of d.orphans) {
      const row = sec.createDiv({ cls: "nl-tree-row clickable" });
      row.createEl("span", { cls: "nl-label", text: o.name });
      row.addEventListener("click", () => this.openNote(o.name));
    }
  }

  private renderPairs(c: HTMLElement, d: AuditJson): void {
    const sec = c.createDiv({ cls: "nl-section" });
    sec.createEl("div", {
      cls: "nl-section-title",
      text: tr("audit.pairs", { n: d.unlinked_pairs.length }),
    });
    sec.createEl("div", {
      cls: "nl-miner-meta",
      text: tr("audit.pairs.sub"),
    });
    if (d.unlinked_pairs.length === 0) {
      sec.createDiv({ cls: "nl-miner-meta", text: tr("audit.pairs.none") });
      return;
    }
    for (const p of d.unlinked_pairs) {
      const row = sec.createDiv({ cls: "nl-bridge-row" });
      const main = row.createDiv({ cls: "nl-bridge-main" });
      const tag = main.createEl("span", {
        cls: "nl-tag",
        text:
          p.score >= 0.35
            ? tr("strength.strong")
            : p.score >= 0.22
              ? tr("strength.medium")
              : tr("strength.weak"),
      });
      tag.title = tr("audit.pairTip", { score: p.score, n: p.n_shared });
      const a = main.createEl("span", { cls: "nl-label clickable", text: p.a });
      a.addEventListener("click", () => this.openNote(p.a));
      main.createEl("span", { cls: "nl-note", text: "↔" });
      const b = main.createEl("span", { cls: "nl-label clickable", text: p.b });
      b.addEventListener("click", () => this.openNote(p.b));

      const words = row.createDiv({ cls: "nl-bridge-words" });
      for (const w of p.shared.slice(0, 8)) {
        words.createEl("span", { cls: "nl-miner-chip", text: w });
      }
    }
  }

  private async openNote(name: string): Promise<void> {
    const f = this.app.metadataCache.getFirstLinkpathDest(stripExt(name), "");
    if (f instanceof TFile) {
      await this.app.workspace.getLeaf(false).openFile(f);
    } else {
      new Notice(tr("err.noteNotFound", { name }));
    }
  }
}
