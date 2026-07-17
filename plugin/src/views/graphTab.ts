// Graph tab — the note as its sections, not its blocks.
//
// A row per block reads as a list, and a list is what the Note tab already is.
// What a graph can say that a list can't is *shape*: this note is four sections,
// that one is mostly quotes, this one carries all the code. So the unit here is
// the **section** — a heading and everything under it — drawn as a box:
//
//   ┌ Introduction ──────────┐
//   │ ● 3 paragraphs         │──❝ quote
//   └────────────────────────┘──{ } code
//
// Inside a box, prose collapses to one node: eight paragraphs of body text are
// one fact about the section ("it's prose"), and eight identical rows spend
// space saying it eight times. What does *not* collapse is anything that isn't
// prose — a quote, a code block, a table — because those are what you scan a
// document for. They hang off the section as satellites, individually
// clickable, so they stay findable instead of buried in a run of paragraphs.
//
// Layout is computed, not simulated: a section's position is a pure function of
// document order and heading depth, so the picture never drifts between renders.

import type NLMinerPlugin from "../main";
import type { ViewBlock, ViewJson } from "../types";
import { absFor, isIndexable } from "../util";
import {
  blockTerm,
  blockTitle,
  depTerm,
  displayPhrases,
  phraseTerm,
} from "../labels";
import { t as tr } from "../i18n";
import type { Tab } from "./panel";
import { clip, setViewBox, svgEl } from "./svgutil";

// Layout metrics (px, pre-zoom).
const PAD = 16;
const ROW_H = 22; // one line inside a section box
const INDENT = 14; // per heading level
const BOX_W = 210;
const SAT_GAP = 18; // box → satellite gutter
const SAT_W = 130;
const SECTION_GAP = 10; // between boxes
const HEAD_H = 20; // the box's title row

const GAP = 40; // spine → parse-detail gutter
const PHRASE_W = 150;
const DETAIL_ROW_H = 22;
const TOKEN_W = 130;
const ARC_BOW_MAX = 60;

/** Prose collapses; everything else stays individually visible. */
const PROSE = new Set(["para", "sentence"]);

/** A heading and the blocks beneath it, positioned. */
interface Section {
  /** null for blocks before the first heading */
  heading: ViewBlock | null;
  depth: number;
  prose: ViewBlock[];
  /** quotes, code, lists, tables — kept individual */
  satellites: ViewBlock[];
  x: number;
  y: number;
  h: number;
}

export class GraphTab implements Tab {
  private plugin: NLMinerPlugin;
  private c!: HTMLElement;
  private zoom = 1;
  private pan = { x: 0, y: 0 };
  private selected: string | null = null;
  private snap: ViewJson | null = null;
  private detailFits = true;

  constructor(plugin: NLMinerPlugin) {
    this.plugin = plugin;
  }

  private get app() {
    return this.plugin.app;
  }

  onNoteChange(): void {
    this.selected = null;
    this.snap = null;
  }

  onResize(): void {
    if (this.snap) this.draw(this.snap);
  }

  async render(container: HTMLElement): Promise<void> {
    this.c = container;
    const c = container;
    c.empty();
    c.addClass("nl-miner-graph");

    this.snap = null;
    const file = this.app.workspace.getActiveFile();
    if (!file) {
      this.renderEmpty(tr("empty.openNoteGraph"));
      return;
    }
    if (!isIndexable(file.path)) {
      this.renderEmpty(tr("empty.notText", { name: file.name }));
      return;
    }
    let snap: ViewJson | null = null;
    try {
      snap = await this.plugin.getView(absFor(this.app, file.path));
    } catch (e) {
      console.error("[nl-miner] graph tab load failed", e);
    }
    if (!snap || snap.blocks.length === 0) {
      this.renderEmpty(tr("empty.notIndexed"));
      return;
    }
    this.snap = snap;
    this.draw(snap);
  }

  private renderEmpty(msg: string): void {
    const c = this.c;
    c.empty();
    c.addClass("nl-miner-graph");
    c.appendChild(createDiv({ cls: "nl-miner-empty", text: msg }));
  }

  // ---- layout -----------------------------------------------------------

  /** Blocks grouped into sections, in reading order.
   *
   *  A heading opens a section; everything until the next heading belongs to it.
   *  Blocks before the first heading form a titleless section rather than being
   *  dropped — plenty of notes open with a paragraph. Depth comes from the
   *  heading *level*, not the parent chain, because that is what a reader means
   *  by "under this heading". */
  private layout(snap: ViewJson): Section[] {
    const ordered = [...snap.blocks].sort((a, b) => a.span[0] - b.span[0]);
    const sections: Section[] = [];
    let cur: Section | null = null;

    const open = (heading: ViewBlock | null, depth: number): Section => {
      const s: Section = {
        heading,
        depth,
        prose: [],
        satellites: [],
        x: 0,
        y: 0,
        h: 0,
      };
      sections.push(s);
      return s;
    };

    for (const b of ordered) {
      if (b.kind === "heading") {
        cur = open(b, Math.max(0, (b.level ?? 1) - 1));
        continue;
      }
      if (!cur) cur = open(null, 0);
      if (PROSE.has(b.kind)) cur.prose.push(b);
      else cur.satellites.push(b);
    }

    let y = PAD;
    for (const s of sections) {
      const lines = Math.max(s.prose.length ? 1 : 0, s.satellites.length, 1);
      s.x = PAD + s.depth * INDENT;
      s.y = y;
      s.h = HEAD_H + lines * ROW_H;
      y += s.h + SECTION_GAP;
    }
    return sections;
  }

  private draw(snap: ViewJson): void {
    const c = this.c;
    c.empty();
    c.addClass("nl-miner-graph");

    const sections = this.layout(snap);
    if (this.selected && !snap.blocks.some((b) => b.block_id === this.selected)) {
      this.selected = null;
    }

    const svg = svgEl("svg");
    c.appendChild(svg);
    // Fit the section boxes and their satellites; the parse detail beyond is
    // reached by widening the pane, or delegated to the Note tab.
    const visibleW = setViewBox(svg, c, PAD * 2 + BOX_W + SAT_GAP + SAT_W);
    this.detailFits =
      visibleW >= PAD + BOX_W + SAT_GAP + SAT_W + GAP + PHRASE_W;

    this.renderToolbar(c, snap);

    const root = svgEl("g");
    svg.appendChild(root);
    const gRails = svgEl("g");
    const gDetail = svgEl("g");
    const gBoxes = svgEl("g");
    root.appendChild(gRails);
    root.appendChild(gDetail);
    root.appendChild(gBoxes);

    this.drawRails(gRails, sections);
    for (const s of sections) this.drawSection(gBoxes, s);

    const sel = snap.blocks.find((b) => b.block_id === this.selected);
    if (sel && this.detailFits) {
      const owner = sections.find(
        (s) =>
          s.heading?.block_id === sel.block_id ||
          s.prose.some((b) => b.block_id === sel.block_id) ||
          s.satellites.some((b) => b.block_id === sel.block_id),
      );
      this.drawDetail(gDetail, snap, sel, owner ? owner.y : PAD);
    }

    this.attachPanZoom(svg, root);
  }

  private renderToolbar(c: HTMLElement, snap: ViewJson): void {
    const toolbar = createDiv({ cls: "nl-miner-toolbar" });
    const reset = toolbar.createEl("button", {
      text: tr("btn.fit"),
      cls: "clickable-icon",
    });
    reset.addEventListener("click", () => {
      this.zoom = 1;
      this.pan = { x: 0, y: 0 };
      this.render(this.c).catch(() => {});
    });
    toolbar.createEl("span", {
      cls: "nl-miner-meta",
      text: this.detailFits
        ? tr("graph.meta", { n: snap.blocks.length })
        : tr("graph.metaPanel", { n: snap.blocks.length }),
    });
    c.appendChild(toolbar);
  }

  /** Indent rails: a line from a section down past everything nested under it,
   *  so a subsection reads as belonging to its parent rather than merely
   *  following it. */
  private drawRails(g: SVGGElement, sections: Section[]): void {
    for (let i = 0; i < sections.length; i++) {
      const s = sections[i];
      let last = -1;
      for (let j = i + 1; j < sections.length; j++) {
        if (sections[j].depth <= s.depth) break;
        last = j;
      }
      if (last < 0) continue;
      const x = s.x + 6;
      const line = svgEl("line");
      line.classList.add("nl-rail");
      line.setAttribute("x1", String(x));
      line.setAttribute("x2", String(x));
      line.setAttribute("y1", String(s.y + s.h));
      line.setAttribute("y2", String(sections[last].y + HEAD_H / 2));
      g.appendChild(line);
      for (let j = i + 1; j <= last; j++) {
        if (sections[j].depth !== s.depth + 1) continue;
        const el = svgEl("line");
        el.classList.add("nl-rail");
        el.setAttribute("x1", String(x));
        el.setAttribute("x2", String(sections[j].x));
        el.setAttribute("y1", String(sections[j].y + HEAD_H / 2));
        el.setAttribute("y2", String(sections[j].y + HEAD_H / 2));
        g.appendChild(el);
      }
    }
  }

  private drawSection(g: SVGGElement, s: Section): void {
    const el = svgEl("g");
    el.classList.add("nl-section-box");
    if (s.heading?.block_id === this.selected) el.classList.add("selected");
    el.setAttribute("transform", `translate(${s.x},${s.y})`);

    const box = svgEl("rect");
    box.classList.add("nl-box");
    box.setAttribute("x", "0");
    box.setAttribute("y", "0");
    box.setAttribute("width", String(BOX_W));
    box.setAttribute("height", String(s.h));
    box.setAttribute("rx", "5");
    el.appendChild(box);

    const rule = svgEl("line");
    rule.classList.add("nl-box-rule");
    rule.setAttribute("x1", "0");
    rule.setAttribute("x2", String(BOX_W));
    rule.setAttribute("y1", String(HEAD_H));
    rule.setAttribute("y2", String(HEAD_H));
    el.appendChild(rule);

    // -- title row
    const title = svgEl("text");
    title.classList.add("nl-box-title");
    title.setAttribute("x", "10");
    title.setAttribute("y", String(HEAD_H - 6));
    title.textContent = s.heading ? clip(s.heading.text, 24) : tr("graph.intro");
    el.appendChild(title);
    if (s.heading) {
      const lvl = svgEl("text");
      lvl.classList.add("nl-box-level");
      lvl.setAttribute("x", String(BOX_W - 8));
      lvl.setAttribute("y", String(HEAD_H - 6));
      lvl.setAttribute("text-anchor", "end");
      lvl.textContent = blockTitle(s.heading.kind, s.heading.level);
      el.appendChild(lvl);
      const hit = this.hitRow(0, 0, BOX_W, HEAD_H);
      hit.addEventListener("click", () => this.pick(s.heading!.block_id));
      const tip = svgEl("title");
      tip.textContent = tr("graph.sectionTip", {
        title: s.heading.text.trim(),
        n: s.prose.length + s.satellites.length + 1,
      });
      hit.appendChild(tip);
      el.appendChild(hit);
    }

    // -- prose, collapsed to one node
    const row = HEAD_H;
    if (s.prose.length) {
      if (s.prose.some((b) => b.block_id === this.selected)) {
        el.classList.add("selected");
      }
      const dot = svgEl("circle");
      dot.classList.add("nl-dot", "prose");
      dot.setAttribute("cx", "12");
      dot.setAttribute("cy", String(row + ROW_H / 2));
      dot.setAttribute("r", "4");
      el.appendChild(dot);
      const label = svgEl("text");
      label.classList.add("nl-box-prose");
      label.setAttribute("x", "22");
      label.setAttribute("y", String(row + ROW_H / 2 + 4));
      label.textContent =
        s.prose.length === 1
          ? tr("graph.prose1")
          : tr("graph.prose", { n: s.prose.length });
      el.appendChild(label);
      const hit = this.hitRow(0, row, BOX_W, ROW_H);
      hit.addEventListener("click", () => this.pick(s.prose[0].block_id));
      const tip = svgEl("title");
      tip.textContent = tr("graph.proseTip", { n: s.prose.length });
      hit.appendChild(tip);
      el.appendChild(hit);
    }

    g.appendChild(el);

    // -- satellites, one each, outside the box
    s.satellites.forEach((b, i) => {
      const y = s.y + HEAD_H + i * ROW_H + ROW_H / 2;
      const x0 = s.x + BOX_W;
      const x1 = s.x + BOX_W + SAT_GAP;
      const link = svgEl("path");
      link.classList.add("nl-link", "sat");
      link.setAttribute(
        "d",
        `M${x0},${y} C${x0 + 8},${y} ${x1 - 8},${y} ${x1},${y}`,
      );
      g.appendChild(link);

      const sat = svgEl("g");
      sat.classList.add("nl-sat", b.kind);
      if (b.block_id === this.selected) sat.classList.add("selected");
      sat.setAttribute("transform", `translate(${x1},${y})`);
      const pill = svgEl("rect");
      pill.classList.add("nl-pill");
      pill.setAttribute("x", "0");
      pill.setAttribute("y", "-8");
      pill.setAttribute("width", String(SAT_W));
      pill.setAttribute("height", "16");
      pill.setAttribute("rx", "8");
      sat.appendChild(pill);
      const term = blockTerm(b.kind);
      const label = svgEl("text");
      label.classList.add("nl-pill-text");
      label.setAttribute("x", "8");
      label.setAttribute("y", "4");
      label.textContent = `${term.plain} · ${clip(b.text, 10)}`;
      sat.appendChild(label);
      const tip = svgEl("title");
      tip.textContent = `${term.hint}\n\n${clip(b.text, 300)}`;
      sat.appendChild(tip);
      sat.addEventListener("click", () => this.pick(b.block_id));
      g.appendChild(sat);
    });
  }

  private hitRow(x: number, y: number, w: number, h: number): SVGGElement {
    const g = svgEl("g");
    g.classList.add("nl-hit-row");
    const r = svgEl("rect");
    r.classList.add("nl-hit");
    r.setAttribute("x", String(x));
    r.setAttribute("y", String(y));
    r.setAttribute("width", String(w));
    r.setAttribute("height", String(h));
    g.appendChild(r);
    return g;
  }

  /** Select a block. In a pane too narrow for the parse column the Note tab is
   *  the better surface for it — same window, one tab away. */
  private pick(blockId: string): void {
    this.selected = this.selected === blockId ? null : blockId;
    if (!this.detailFits && this.selected) {
      this.plugin.showTab("note", blockId);
      return;
    }
    if (this.snap) this.draw(this.snap);
  }

  // ---- detail (selected block's parse) ----------------------------------

  /** Phrases and their head tokens for one block, plus the dep arcs between
   *  those tokens, drawn level with the section that owns it. */
  private drawDetail(
    g: SVGGElement,
    snap: ViewJson,
    b: ViewBlock,
    baseY: number,
  ): void {
    const nodeLabel = new Map(snap.nodes.map((n) => [n.id, n.label]));
    const tokenIds = snap.edges
      .filter((e) => e.label === "token" && e.src === b.block_id)
      .map((e) => e.dst);
    const head = new Map<string, string>();
    for (const e of snap.edges) if (e.label === "head") head.set(e.src, e.dst);

    const lower = (b.text || "").toLowerCase();
    const pos = (id: string): number => {
      const i = lower.indexOf((nodeLabel.get(id) ?? "").toLowerCase());
      return i < 0 ? Number.MAX_SAFE_INTEGER : i;
    };
    const tokens = [...new Set(tokenIds)].sort((x, y) => pos(x) - pos(y));

    const px = PAD + BOX_W + SAT_GAP + SAT_W + GAP;
    const tx = px + PHRASE_W;

    const phrases = displayPhrases(b.phrases).sort(
      (p, q) => pos(head.get(p.id) ?? "") - pos(head.get(q.id) ?? ""),
    );
    const phraseY = new Map<string, number>();
    phrases.forEach((p, i) => phraseY.set(p.id, baseY + i * DETAIL_ROW_H));
    const tokenY = new Map<string, number>();
    tokens.forEach((tk, i) => tokenY.set(tk, baseY + i * DETAIL_ROW_H));

    for (const p of phrases) {
      const y = phraseY.get(p.id)!;
      const term = phraseTerm(p.label);
      const el = svgEl("g");
      el.classList.add("nl-detail-node", "phrase");
      el.setAttribute("transform", `translate(${px},${y})`);
      const pill = svgEl("rect");
      pill.classList.add("nl-pill");
      pill.setAttribute("x", "0");
      pill.setAttribute("y", "-8");
      pill.setAttribute("width", String(PHRASE_W - 20));
      pill.setAttribute("height", "16");
      pill.setAttribute("rx", "8");
      el.appendChild(pill);
      const label = svgEl("text");
      label.classList.add("nl-pill-text");
      label.setAttribute("x", "8");
      label.setAttribute("y", "4");
      label.textContent = term.plain;
      el.appendChild(label);
      const tip = svgEl("title");
      tip.textContent = `${term.hint}\n${tr("phrase.head")}: ${p.head_text || "?"}`;
      el.appendChild(tip);
      g.appendChild(el);

      const h = head.get(p.id);
      if (h && tokenY.has(h)) {
        const hy = tokenY.get(h)!;
        const link = svgEl("path");
        link.classList.add("nl-link", "head");
        link.setAttribute(
          "d",
          `M${px + PHRASE_W - 20},${y} C${tx - 20},${y} ${tx - 20},${hy} ${tx},${hy}`,
        );
        g.appendChild(link);
      }
    }

    for (const tk of tokens) {
      const y = tokenY.get(tk)!;
      const el = svgEl("g");
      el.classList.add("nl-detail-node", "token");
      el.setAttribute("transform", `translate(${tx},${y})`);
      const dot = svgEl("circle");
      dot.classList.add("nl-dot");
      dot.setAttribute("r", "3");
      el.appendChild(dot);
      const label = svgEl("text");
      label.classList.add("nl-token-text");
      label.setAttribute("x", "8");
      label.setAttribute("y", "4");
      label.textContent = clip(nodeLabel.get(tk) ?? "", 16);
      el.appendChild(label);
      g.appendChild(el);
    }

    // dep arcs, bowed out right of the token column so they never cross labels
    for (const e of snap.edges) {
      if (!e.label.startsWith("dep:")) continue;
      if (!tokenY.has(e.src) || !tokenY.has(e.dst)) continue;
      const y1 = tokenY.get(e.src)!;
      const y2 = tokenY.get(e.dst)!;
      const ax = tx + TOKEN_W;
      const bow = Math.min(ARC_BOW_MAX, 12 + Math.abs(y2 - y1) * 0.4);
      const arc = svgEl("path");
      arc.classList.add("nl-link", "dep");
      arc.setAttribute(
        "d",
        `M${ax},${y1} C${ax + bow},${y1} ${ax + bow},${y2} ${ax},${y2}`,
      );
      const tip = svgEl("title");
      const dt = depTerm(e.label);
      tip.textContent = `${nodeLabel.get(e.src)} → ${nodeLabel.get(e.dst)} : ${dt.plain}\n${dt.hint}`;
      arc.appendChild(tip);
      g.appendChild(arc);
    }
  }

  // ---- pan / zoom -------------------------------------------------------

  private attachPanZoom(svg: SVGSVGElement, root: SVGGElement): void {
    const apply = () =>
      root.setAttribute(
        "transform",
        `translate(${this.pan.x},${this.pan.y}) scale(${this.zoom})`,
      );
    apply();
    svg.addEventListener("wheel", (e) => {
      e.preventDefault();
      if (e.ctrlKey || e.metaKey) {
        this.zoom = Math.max(
          0.2,
          Math.min(4, this.zoom * (e.deltaY < 0 ? 1.1 : 1 / 1.1)),
        );
      } else {
        this.pan = { x: this.pan.x, y: this.pan.y - e.deltaY };
      }
      apply();
    });
    svg.addEventListener("mousedown", (e) => {
      if (e.button !== 0) return;
      let dragging = true;
      svg.classList.add("dragging");
      const start = { x: e.clientX - this.pan.x, y: e.clientY - this.pan.y };
      const move = (ev: MouseEvent) => {
        if (!dragging) return;
        this.pan = { x: ev.clientX - start.x, y: ev.clientY - start.y };
        apply();
      };
      const up = () => {
        dragging = false;
        svg.classList.remove("dragging");
        window.removeEventListener("mousemove", move);
        window.removeEventListener("mouseup", up);
      };
      window.addEventListener("mousemove", move);
      window.addEventListener("mouseup", up);
    });
  }
}
