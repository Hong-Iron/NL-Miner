// Note tab — the open note as one tree.
//
// A single outline: blocks nest by parent_block_id in document order, and the
// selected block expands in place to show what the parser found inside it
// (phrase chunks → their head words, word-to-word relations, and links to other
// notes). Everything is named in plain language via ../labels — a reader who has
// never seen "NP" or "nsubj" should still be able to read every line — with the
// technical symbol kept in the tooltip.

import { Notice } from "obsidian";

import type NLMinerPlugin from "../main";
import type { BridgeJson, QueryBlockJson, ViewBlock, ViewJson } from "../types";
import { absFor, isIndexable } from "../util";
import {
  blockTerm,
  blockTitle,
  depTerm,
  displayPhrases,
  phraseTerm,
} from "../labels";
import { flatten, nestBlocks, type Nested, type TNode } from "../tree";
import { t as tr } from "../i18n";
import type { Tab } from "./panel";

type NbState = "loading" | QueryBlockJson | "error";
type BridgeState = "loading" | BridgeJson | "error";

/** ``Note.md`` → ``Note`` — wikilinks name the note, not the file. */
function stripExt(name: string): string {
  return name.replace(/\.(md|markdown|txt)$/i, "");
}

/** The score is a cosine in 0..1 over idf-weighted concepts. A bare number
 *  means nothing to a reader, so bucket it — the honest claim is "these look
 *  strongly / possibly related", not a figure to two decimals. Thresholds match
 *  what the backend's MIN_SCORE (0.15) lets through: everything shown cleared
 *  the bar, so the buckets grade what's left rather than re-filtering. The exact
 *  score stays in the tooltip. */
function strengthLabel(score: number): string {
  if (score >= 0.35) return tr("strength.strong");
  if (score >= 0.22) return tr("strength.medium");
  return tr("strength.weak");
}

export class NoteTab implements Tab {
  private plugin: NLMinerPlugin;
  private c!: HTMLElement;
  private snap: ViewJson | null = null;
  private currentAbs = "";
  private currentDoc = "";
  private expanded: string | null = null;
  private nb = new Map<string, NbState>();
  /** keyed by doc name (what `bridge` takes), survives note switches */
  private bridges = new Map<string, BridgeState>();
  private scrollToOpen = false;

  constructor(plugin: NLMinerPlugin) {
    this.plugin = plugin;
  }

  private get app() {
    return this.plugin.app;
  }

  /** Focus a block — the graph tab hands one over when it can't show detail. */
  accept(blockId: unknown): void {
    if (typeof blockId !== "string") return;
    this.expanded = blockId;
    this.scrollToOpen = true;
  }

  onNoteChange(): void {
    this.expanded = null;
    this.nb.clear();
  }

  onReindex(): void {
    this.bridges.clear();
    this.nb.clear();
  }

  async render(container: HTMLElement): Promise<void> {
    this.c = container;
    const c = container;
    c.empty();
    c.addClass("nl-miner-query");

    const file = this.app.workspace.getActiveFile();
    if (!file) {
      this.empty(tr("empty.openNote"));
      return;
    }
    // Binary attachments (images, PDFs, …) have no text to analyse.
    if (!isIndexable(file.path)) {
      this.empty(tr("empty.notText", { name: file.name }));
      return;
    }
    const abs = absFor(this.app, file.path);
    if (abs !== this.currentAbs) {
      // switching notes drops per-block state; block ids are file-scoped
      this.expanded = null;
      this.nb.clear();
    }
    this.currentAbs = abs;
    this.currentDoc = file.name;


    try {
      this.snap = await this.plugin.getView(this.currentAbs);
    } catch (e) {
      console.error("[nl-miner] query panel load failed", e);
      this.snap = null;
    }
    if (!this.snap || this.snap.blocks.length === 0) {
      this.empty(tr("empty.notIndexed"));
      return;
    }

    const s = this.snap;
    c.createEl("div", { cls: "nl-section-title nl-doc-title", text: file.name });
    const meta = c.createDiv({ cls: "nl-miner-meta" });
    meta.textContent =
      tr("note.meta", {
        blocks: s.blocks.length,
        phrases: s.stats.n_phrase_nodes,
        deps: s.stats.n_dep_edges,
      }) + (s.parse ? "" : tr("note.parseOff"));
    // Say why the grammar layer is empty instead of leaving zeros that read as
    // a broken index: non-EN notes are gated out, and the EN grammar itself
    // only covers a small slice of ordinary prose.
    const note = this.limitNote(s);
    if (note) c.createDiv({ cls: "nl-miner-meta nl-limit", text: note });

    this.renderBridges(c, file.name);

    const tree = c.createDiv({ cls: "nl-tree" });
    this.renderNodes(tree, this.outline(s));
  }

  // ---- concept bridges (unlinked notes that share concepts) --------------

  /** Notes this one probably ought to link to. Rendered from a cached
   *  `bridge --json`; the fetch is fired once per note and repaints on arrival
   *  so the panel never blocks on it. */
  private renderBridges(c: HTMLElement, docName: string): void {
    const state = this.bridges.get(docName);
    if (state === undefined) {
      this.loadBridges(docName);
      return;
    }
    if (state === "loading" || state === "error") return;
    if (state.bridges.length === 0) return;

    const sec = c.createDiv({ cls: "nl-section nl-bridges" });
    sec.createEl("div", {
      cls: "nl-section-title",
      text: tr("bridge.title", { n: state.bridges.length }),
    });
    sec.createEl("div", {
      cls: "nl-miner-meta",
      text: tr("bridge.subtitle"),
    });
    for (const b of state.bridges.slice(0, 8)) {
      const row = sec.createDiv({ cls: "nl-bridge-row" });

      const open = row.createDiv({ cls: "nl-bridge-main clickable" });
      // Strength, not overlap size: two notes sharing one rare term are more
      // related than two sharing five ordinary ones, so a raw count would rank
      // and label them backwards.
      open.createEl("span", { cls: "nl-tag", text: strengthLabel(b.score) });
      open.createEl("span", { cls: "nl-label", text: b.name });
      open.title = tr("bridge.tooltip", {
        shared: b.shared.join(", "),
        score: b.score,
      });
      open.addEventListener("click", () => this.openNote(b.name));

      const words = row.createDiv({ cls: "nl-bridge-words" });
      for (const w of b.shared.slice(0, 6)) {
        const chip = words.createEl("span", { cls: "nl-miner-chip", text: w });
        chip.title = tr("bridge.chipTip", { word: w });
        chip.addEventListener("click", (e) => {
          e.stopPropagation();
          this.plugin.showTab("vault", w);
        });
      }

      const link = row.createEl("button", {
        cls: "clickable-icon nl-bridge-link",
        text: tr("btn.insertLink"),
      });
      link.title = tr("bridge.insertTip", { name: stripExt(b.name) });
      link.addEventListener("click", (e) => {
        e.stopPropagation();
        this.insertLink(b.name);
      });
    }
  }

  private loadBridges(docName: string): void {
    if (this.bridges.has(docName)) return;
    this.bridges.set(docName, "loading");
    this.plugin.backend
      .bridge(docName)
      .then((r) => {
        this.bridges.set(docName, r);
        if (this.currentDoc === docName) this.rerender();
      })
      .catch((e) => {
        // a store indexed without --pos has no concepts; not worth a notice
        console.error("[nl-miner] bridge failed", e);
        this.bridges.set(docName, "error");
      });
  }

  private async openNote(name: string): Promise<void> {
    const file = this.app.metadataCache.getFirstLinkpathDest(
      stripExt(name),
      this.currentAbs,
    );
    if (file) await this.app.workspace.getLeaf(false).openFile(file);
  }

  /** Insert ``[[Note]]`` at the cursor. This is the plugin's only write to note
   *  content, so it is strictly an explicit user action — never automatic — and
   *  it goes through the editor (undoable with ctrl+z) rather than the vault. */
  private insertLink(name: string): void {
    const editor = this.app.workspace.activeEditor?.editor;
    if (!editor) {
      new Notice(tr("err.needEditor"));
      return;
    }
    editor.replaceSelection(`[[${stripExt(name)}]]`);
    new Notice(tr("bridge.inserted", { name: stripExt(name) }));
    // the link now exists, so this suggestion is stale
    this.bridges.delete(this.currentDoc);
    this.rerender();
  }

  /** Explains an empty grammar layer, which is a limit rather than a failure:
   *  the CKY grammar and the dependency rules are English-only, and even on
   *  English the grammar covers only a fraction of ordinary prose. */
  private limitNote(s: ViewJson): string {
    if (!s.parse) return "";
    if (s.stats.skipped_lang > 0 && s.stats.n_dep_edges === 0) {
      return tr("note.limit.nonEnglish", { n: s.stats.skipped_lang });
    }
    if (s.stats.n_phrase_nodes === 0 && s.stats.cky_trees === 0) {
      return tr("note.limit.noPhrases");
    }
    return "";
  }

  // ---- tree model -------------------------------------------------------

  /** The note as one tree: nested blocks, with the expanded block's analysis
   *  spliced in ahead of its child blocks. */
  private outline(snap: ViewJson): TNode[] {
    const toNode = (n: Nested<ViewBlock>): TNode => {
      const children: TNode[] = [];
      if (this.expanded === n.block.block_id) {
        children.push(...this.analysis(n.block));
      }
      children.push(...n.children.map(toNode));
      return this.blockNode(n.block, children);
    };
    return nestBlocks(snap.blocks).map(toNode);
  }

  private blockNode(b: ViewBlock, children: TNode[]): TNode {
    const term = blockTerm(b.kind);
    const open = this.expanded === b.block_id;
    const text = (b.text || "").trim();
    const nPhrase = displayPhrases(b.phrases).length;
    return {
      tag: blockTitle(b.kind, b.level),
      label: text || tr("note.empty"),
      note: nPhrase || b.n_dep ? tr("note.badge", { p: nPhrase, d: b.n_dep }) : "",
      // rows clip to one line, so the tooltip carries the full text
      hint: text ? `${term.hint}\n\n${text}` : term.hint,
      cls: `nl-block ${b.kind}` + (open ? " open" : ""),
      onClick: () => {
        this.expanded = open ? null : b.block_id;
        if (!open) this.loadNeighbours(b);
        this.rerender();
      },
      children,
    };
  }

  /** The three things the parser knows about one block. */
  private analysis(b: ViewBlock): TNode[] {
    const out: TNode[] = [];

    const phrases = displayPhrases(b.phrases);
    if (phrases.length > 0) {
      out.push({
        label: tr("group.phrases", { n: phrases.length }),
        hint: tr("group.phrases.hint"),
        cls: "nl-group",
        children: phrases.map((p) => {
          const t = phraseTerm(p.label);
          return {
            tag: t.plain,
            label: p.head_text || "?",
            note: tr("phrase.head"),
            hint: t.hint,
            cls: "nl-leaf phrase",
          };
        }),
      });
    }

    if (b.deps.length > 0) {
      out.push({
        label: tr("group.deps", { n: b.deps.length }),
        hint: tr("group.deps.hint"),
        cls: "nl-group",
        children: b.deps.map((d) => {
          const t = depTerm(d.label);
          return {
            tag: t.plain,
            label: `${d.src_text} → ${d.dst_text}`,
            hint: t.hint,
            cls: "nl-leaf dep",
          };
        }),
      });
    }

    out.push(this.neighbourNode(b));
    return out;
  }

  private neighbourNode(b: ViewBlock): TNode {
    const state = this.nb.get(b.block_id);
    if (state === undefined || state === "loading") {
      return { label: tr("group.links"), note: tr("group.links.loading"), cls: "nl-group" };
    }
    if (state === "error") {
      return { label: tr("group.links"), note: tr("group.links.error"), cls: "nl-group" };
    }
    const mk = (dir: "out" | "in", edges: QueryBlockJson["out"]): TNode[] => {
      if (edges.length === 0) return [];
      const shown = edges.slice(0, 50);
      const kids: TNode[] = shown.map((e) => ({
        tag: e.label,
        label: e.node,
        cls: "nl-leaf nb",
      }));
      if (edges.length > shown.length) {
        kids.push({
          label: tr("group.more", { n: edges.length - shown.length }),
          cls: "nl-leaf muted",
        });
      }
      return [
        {
          label:
            dir === "out"
              ? tr("group.out", { n: edges.length })
              : tr("group.in", { n: edges.length }),
          hint: dir === "out" ? tr("group.out.hint") : tr("group.in.hint"),
          cls: "nl-group",
          children: kids,
        },
      ];
    };
    const children = [...mk("out", state.out), ...mk("in", state.in)];
    if (children.length === 0) {
      return { label: tr("group.links"), note: tr("group.links.none"), cls: "nl-group" };
    }
    return { label: tr("group.links"), cls: "nl-group", children };
  }

  private loadNeighbours(b: ViewBlock): void {
    if (this.nb.has(b.block_id)) return;
    this.nb.set(b.block_id, "loading");
    const doc = this.snap?.doc;
    if (!doc) return;
    this.plugin.backend
      .queryBlock(doc, b.block_id)
      .then((r) => {
        this.nb.set(b.block_id, r);
        // only repaint if this block is still the expanded one
        if (this.expanded === b.block_id) this.rerender();
      })
      .catch((e) => {
        console.error("[nl-miner] neighbours failed", e);
        this.nb.set(b.block_id, "error");
        if (this.expanded === b.block_id) this.rerender();
      });
  }

  // ---- tree rendering ---------------------------------------------------

  /** Draw the tree as flat rows carrying box-drawing guides, so nesting is
   *  legible as text:
   *
   *      Paragraph   This document …
   *      ├─ Phrase chunks (3)
   *      │  └─ naming chunk   document
   *      └─ Linked from here
   */
  private renderNodes(parent: HTMLElement, nodes: TNode[]): void {
    for (const row of flatten(nodes)) this.renderRow(parent, row.node, row.guide);
  }

  private renderRow(parent: HTMLElement, n: TNode, guide: string): void {
    const row = parent.createDiv({ cls: `nl-tree-row ${n.cls ?? ""}` });
    if (n.hint) row.title = n.hint;

    if (guide) row.createEl("span", { cls: "nl-guide", text: guide });

    const body = row.createDiv({ cls: "nl-tree-body" });
    if (n.tag) body.createEl("span", { cls: "nl-tag", text: n.tag });
    body.createEl("span", { cls: "nl-label", text: n.label });
    if (n.note) body.createEl("span", { cls: "nl-note", text: n.note });

    if (n.onClick) {
      row.addClass("clickable");
      row.addEventListener("click", (e) => {
        e.stopPropagation();
        n.onClick!();
      });
    }
  }

  // ---- plumbing ---------------------------------------------------------

  /** Repaint into the container we were last given. The host owns the DOM, so a
   *  tab re-renders itself rather than asking the host to rebuild everything —
   *  that would also rebuild the tab bar and lose focus. */
  private rerender(): void {
    if (!this.c) return;
    this.render(this.c)
      .then(() => {
        if (!this.scrollToOpen) return;
        this.scrollToOpen = false;
        const el = this.c.querySelector(".nl-tree-row.nl-block.open");
        if (el) (el as HTMLElement).scrollIntoView({ block: "center" });
      })
      .catch(() => {});
  }

  private empty(msg: string): void {
    const c = this.c;
    c.empty();
    c.addClass("nl-miner-query");
    c.createEl("div", { cls: "nl-miner-empty-text", text: msg });
  }
}
