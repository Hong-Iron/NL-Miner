// MarkdownPostProcessor → concept chips + a counts badge on rendered
// headings/paragraphs. Look up the matching block by trimmed text in the cached
// ``view`` snapshot; read-only — never edits note content. Clicking a chip opens
// the panel's Note tab focused on that block.

import { MarkdownPostProcessorContext } from "obsidian";

import type NLMinerPlugin from "./main";
import type { ViewJson, ViewBlock } from "./types";
import { absFor, isIndexable } from "./util";
import { phraseTerm } from "./labels";
import { t as tr } from "./i18n";

export class Decorator {
  private plugin: NLMinerPlugin;
  private cache = new Map<string, { ts: number; snap: ViewJson | null }>();
  private ttlMs = 5000;

  constructor(plugin: NLMinerPlugin) {
    this.plugin = plugin;
  }

  invalidate(relPath?: string): void {
    if (relPath) this.cache.delete(absFor(this.plugin.app, relPath));
    else this.cache.clear();
  }

  async process(
    el: HTMLElement,
    ctx: MarkdownPostProcessorContext,
  ): Promise<void> {
    const file = ctx.sourcePath;
    if (!isIndexable(file)) return;

    const snap = await this.snapshotFor(file);
    if (!snap || snap.blocks.length === 0) return;

    for (const h of Array.from(el.querySelectorAll("h1,h2,h3,h4,h5,h6"))) {
      this.inject(h as HTMLElement, snap, ["heading"]);
    }
    for (const p of Array.from(el.querySelectorAll("p"))) {
      // paragraphs may carry phrases at the para OR sentence block level, so
      // aggregate across both.
      this.inject(p as HTMLElement, snap, ["para", "sentence"]);
    }
  }

  /** Cheap per-file snapshot; falls back to the plugin's getViewSafe so all
   *  three consumers share the same backend call+cache. */
  private async snapshotFor(rel: string): Promise<ViewJson | null> {
    const abs = absFor(this.plugin.app, rel);
    const hit = this.cache.get(abs);
    if (hit && Date.now() - hit.ts < this.ttlMs) return hit.snap;
    const snap = await this.plugin.getViewSafe(abs);
    this.cache.set(abs, { ts: Date.now(), snap });
    return snap;
  }

  private inject(el: HTMLElement, snap: ViewJson, kinds: string[]): void {
    if (el.querySelector(".nl-miner-badge")) return; // already decorated
    const needle = (el.textContent ?? "").trim();
    if (needle.length < 2) return;
    const matched = this.findBlocks(snap, kinds, needle);
    if (matched.length === 0) return;
    const n_phrase = matched.reduce((a, b) => a + b.n_phrase, 0);
    const n_dep = matched.reduce((a, b) => a + b.n_dep, 0);
    if (n_phrase === 0 && n_dep === 0) return;
    const badge = createEl("span", {
      cls: "nl-miner-badge",
      text: tr("note.badge", { p: n_phrase, d: n_dep }),
    });
    el.appendChild(badge);
    const phrases = matched.flatMap((b) => b.phrases).slice(0, 8);
    if (phrases.length > 0) {
      const cluster = createEl("span", { cls: "nl-miner-chips" });
      const blockId = matched[0].block_id;
      for (const p of phrases) {
        // chips read as plain names ("naming chunk"), never bare CKY symbols;
        // the symbol survives in the tooltip.
        const t = phraseTerm(p.label);
        const chip = createEl("span", {
          cls: "nl-miner-chip",
          text: t.plain,
        });
        chip.title = `${t.hint}\n${tr("phrase.head")}: ${p.head_text || "?"}`;
        chip.addEventListener("click", () => this.plugin.showTab("note", blockId));
        cluster.appendChild(chip);
      }
      el.appendChild(cluster);
    }
  }

  /** Find blocks of the given kinds matching the rendered text: exact trimmed
   *  match first, then substring (a paragraph contains its sentence blocks; a
   *  heading may be a sentinel whose stored text equals the rendered text). */
  private findBlocks(
    snap: ViewJson,
    kinds: string[],
    needle: string,
  ): ViewBlock[] {
    const ns = needle.replace(/\s+/g, " ").toLowerCase();
    const exact: ViewBlock[] = [];
    const loose: ViewBlock[] = [];
    for (const b of snap.blocks) {
      if (!kinds.includes(b.kind)) continue;
      const t = (b.text || "").trim().replace(/\s+/g, " ").toLowerCase();
      if (t.length < 2) continue;
      if (t === ns) exact.push(b);
      else if (t.includes(ns) || ns.includes(t)) loose.push(b);
    }
    return exact.length > 0 ? exact : loose;
  }
}
