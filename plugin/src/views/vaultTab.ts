// Vault graph — the whole vault (or a slice of it) as a hierarchy, not a blob.
//
// Obsidian's own graph is a flat force-directed cloud: every note is a dot,
// position means nothing, and the folder tree the user spent months building is
// thrown away. This view answers the two questions that one can't:
//
//   hierarchy (위계)  notes sit under their folders, indented — the structure already in
//          the vault, laid out deterministically so it never drifts
//   scope (범위)      a folder subtree, or the notes discussing one concept,
//          so you read a graph you can actually follow
//
// Layout is computed, exactly like the per-note graph view: x = nesting depth,
// y = position in the (sorted) tree. Edges hang to the right as arcs, so they
// never cross the labels. Solid = a real wikilink; dashed = a suggestion from
// shared concepts — the two must never look alike.

import { TFile } from "obsidian";

import type NLMinerPlugin from "../main";
import type { VaultJson, VaultNote } from "../types";
import { vaultRoot } from "../util";
import { clip, setViewBox, stripExt, svgEl } from "./svgutil";
import { t as tr } from "../i18n";
import type { Tab } from "./panel";

const PAD = 16;
const ROW_H = 22;
const INDENT = 16;
const LABEL_W = 260;
const ARC_X = PAD + LABEL_W + 24; // where arcs live
const ARC_MAX = 150;

/** One rendered line: a folder or a note. */
interface Row {
  kind: "folder" | "note";
  label: string;
  depth: number;
  y: number;
  x: number;
  note?: VaultNote;
}

export class VaultTab implements Tab {
  private plugin: NLMinerPlugin;
  private c!: HTMLElement;
  private data: VaultJson | null = null;
  private folderScope = "";
  private conceptScope = "";
  private loading = false;
  private error = "";
  private zoom = 1;
  private pan = { x: 0, y: 0 };
  private selected: string | null = null;

  constructor(plugin: NLMinerPlugin) {
    this.plugin = plugin;
  }

  private get app() {
    return this.plugin.app;
  }

  /** The viewBox is computed from the pane width, so a resize (dragging the
   *  sidebar wider) has to recompute it. Repaints from cached data — no
   *  backend call. */
  onResize(): void {
    if (this.data) this.paint();
  }

  onReindex(): void {
    this.data = null;
  }

  /** Scope to one concept — the Note tab hands one over when a chip is clicked. */
  accept(concept: unknown): void {
    if (typeof concept !== "string") return;
    this.conceptScope = concept;
    this.folderScope = "";
    this.data = null;
  }

  async render(container: HTMLElement): Promise<void> {
    this.c = container;
    if (!this.data) await this.reload();
    else this.paint();
  }

  private async reload(): Promise<void> {
    this.loading = true;
    this.error = "";
    this.paint();
    try {
      this.data = await this.plugin.backend.vault({
        root: vaultRoot(this.app) ?? undefined,
        folder: this.folderScope || undefined,
        concept: this.conceptScope || undefined,
      });
    } catch (e) {
      console.error("[nl-miner] vault graph failed", e);
      this.error = tr("err.vaultGraph");
      this.data = null;
    }
    this.loading = false;
    this.paint();
  }

  // ---- layout -----------------------------------------------------------

  /** Folders and their notes as one ordered, indented list. Deterministic:
   *  sorted by path, so a note never moves between renders. */
  private layout(d: VaultJson): Row[] {
    const byFolder = new Map<string, VaultNote[]>();
    for (const n of d.notes) {
      const list = byFolder.get(n.folder) ?? [];
      list.push(n);
      byFolder.set(n.folder, list);
    }
    const rows: Row[] = [];
    let y = PAD;
    // Render every ancestor folder, even one holding no notes directly, so the
    // tree reads continuously instead of jumping Biology → Biology/Cephalopods.
    const all = new Set<string>();
    for (const f of byFolder.keys()) {
      const parts = f ? f.split("/") : [];
      for (let i = 1; i <= parts.length; i++) all.add(parts.slice(0, i).join("/"));
      all.add(f);
    }
    for (const folder of [...all].sort()) {
      const depth = folder ? folder.split("/").length : 0;
      if (folder) {
        rows.push({
          kind: "folder",
          label: folder.split("/").pop() ?? folder,
          depth: depth - 1,
          x: PAD + (depth - 1) * INDENT,
          y,
          });
        y += ROW_H;
      }
      for (const n of (byFolder.get(folder) ?? []).sort((a, b) =>
        a.name.localeCompare(b.name),
      )) {
        rows.push({
          kind: "note",
          label: n.name,
          depth,
          x: PAD + depth * INDENT,
          y,
          note: n,
        });
        y += ROW_H;
      }
    }
    return rows;
  }

  // ---- render -----------------------------------------------------------

  private paint(): void {
    const c = this.c;
    c.empty();
    c.addClass("nl-miner-vault");

    this.renderToolbar(c);

    if (this.loading) {
      c.createDiv({ cls: "nl-miner-empty", text: tr("empty.loading") });
      return;
    }
    if (this.error) {
      c.createDiv({ cls: "nl-miner-empty", text: this.error });
      return;
    }
    const d = this.data;
    if (!d || d.notes.length === 0) {
      c.createDiv({
        cls: "nl-miner-empty",
        text: this.conceptScope
          ? tr("empty.noConcept", { concept: this.conceptScope })
          : tr("empty.noNotes"),
      });
      return;
    }

    const rows = this.layout(d);
    const svg = svgEl("svg");
    c.appendChild(svg);
    const root = svgEl("g");
    svg.appendChild(root);

    const gArcs = svgEl("g");
    const gRows = svgEl("g");
    root.appendChild(gArcs);
    root.appendChild(gRows);

    this.drawRails(gRows, rows);
    this.drawArcs(gArcs, d, rows);
    for (const r of rows) this.drawRow(gRows, r, d);

    // Fit the note column; the link arcs to its right are pannable.
    setViewBox(svg, c, PAD * 2 + LABEL_W);
    this.attachPanZoom(svg, root);
  }

  private renderToolbar(c: HTMLElement): void {
    const bar = c.createDiv({ cls: "nl-miner-toolbar nl-vault-bar" });
    const d = this.data;

    const scope = bar.createEl("select", { cls: "dropdown nl-scope" });
    scope.createEl("option", { text: tr("vault.scopeAll"), value: "" });
    const folders = new Set<string>();
    for (const f of d?.folders ?? []) {
      const parts = f ? f.split("/") : [];
      for (let i = 1; i <= parts.length; i++) {
        folders.add(parts.slice(0, i).join("/"));
      }
    }
    // the current scope may not appear in a filtered response; keep it listed
    if (this.folderScope) folders.add(this.folderScope);
    for (const f of [...folders].sort()) {
      scope.createEl("option", { text: `📁 ${f}`, value: f });
    }
    scope.value = this.conceptScope ? "" : this.folderScope;
    scope.addEventListener("change", () => {
      this.folderScope = scope.value;
      this.conceptScope = "";
      this.reload().catch(() => {});
    });

    if (this.conceptScope) {
      const chip = bar.createEl("span", {
        cls: "nl-miner-chip nl-scope-chip",
        text: `🔍 ${this.conceptScope} ✕`,
      });
      chip.title = tr("vault.scopeClear");
      chip.addEventListener("click", () => {
        this.conceptScope = "";
        this.reload().catch(() => {});
      });
    }

    bar
      .createEl("button", { text: tr("btn.refresh"), cls: "clickable-icon" })
      .addEventListener("click", () => { this.data = null; this.reload().catch(() => {}); });

    if (d) {
      bar.createEl("span", {
        cls: "nl-miner-meta",
        text: tr("vault.meta", { notes: d.notes.length, links: d.links.length, bridges: d.bridges.length }),
      });
    }
  }

  /** Indent rails: the folder hierarchy drawn as continuous guides. */
  private drawRails(g: SVGGElement, rows: Row[]): void {
    for (let i = 0; i < rows.length; i++) {
      const r = rows[i];
      if (r.kind !== "folder") continue;
      let last = -1;
      for (let j = i + 1; j < rows.length; j++) {
        if (rows[j].depth <= r.depth) break;
        last = j;
      }
      if (last < 0) continue;
      const line = svgEl("line");
      line.classList.add("nl-rail");
      line.setAttribute("x1", String(r.x + 5));
      line.setAttribute("x2", String(r.x + 5));
      line.setAttribute("y1", String(r.y + 6));
      line.setAttribute("y2", String(rows[last].y));
      g.appendChild(line);
    }
  }

  private drawRow(g: SVGGElement, r: Row, d: VaultJson): void {
    const el = svgEl("g");
    el.classList.add("nl-vault-row", r.kind);
    el.setAttribute("transform", `translate(${r.x},${r.y})`);

    if (r.kind === "folder") {
      const t = svgEl("text");
      t.classList.add("nl-folder-text");
      t.setAttribute("x", "0");
      t.setAttribute("y", "4");
      t.textContent = `▾ ${r.label}`;
      el.appendChild(t);
      el.addEventListener("click", () => {
        // clicking a folder scopes to it — the "category" view
        const full = this.folderPathFor(r, d);
        this.folderScope = full;
        this.conceptScope = "";
        this.reload().catch(() => {});
      });
      g.appendChild(el);
      return;
    }

    const n = r.note!;
    const isSel = this.selected === n.file_uuid;
    if (isSel) el.classList.add("selected");

    const hit = svgEl("rect");
    hit.classList.add("nl-hit");
    hit.setAttribute("x", "-4");
    hit.setAttribute("y", "-8");
    hit.setAttribute("width", String(LABEL_W));
    hit.setAttribute("height", String(ROW_H - 3));
    hit.setAttribute("rx", "3");
    el.appendChild(hit);

    const dot = svgEl("circle");
    dot.classList.add("nl-dot");
    dot.setAttribute("cx", "4");
    dot.setAttribute("cy", "1");
    // size carries weight, the way a graph node should
    dot.setAttribute("r", String(Math.min(6, 2.5 + n.n_blocks / 12)));
    el.appendChild(dot);

    const t = svgEl("text");
    t.classList.add("nl-note-text");
    t.setAttribute("x", "14");
    t.setAttribute("y", "4");
    t.textContent = clip(stripExt(n.name), 30);
    el.appendChild(t);

    const meta = svgEl("text");
    meta.classList.add("nl-badge-text");
    meta.setAttribute("x", String(LABEL_W - 14));
    meta.setAttribute("y", "4");
    meta.setAttribute("text-anchor", "end");
    meta.textContent = n.concepts.slice(0, 3).join(" · ");
    el.appendChild(meta);

    const title = svgEl("title");
    title.textContent = tr("vault.noteTip", {
      name: n.name,
      folder: n.folder || tr("vault.root"),
      blocks: n.n_blocks,
      concepts: n.concepts.length
        ? tr("vault.conceptsTip", { list: n.concepts.join(", ") })
        : "",
    });
    el.appendChild(title);

    el.addEventListener("click", () => {
      this.selected = isSel ? null : n.file_uuid;
      this.paint();
    });
    el.addEventListener("dblclick", () => this.openNote(n));
    g.appendChild(el);
  }

  private folderPathFor(r: Row, d: VaultJson): string {
    // the row label is the last segment; recover the full path from the notes
    for (const n of d.notes) {
      const parts = n.folder.split("/");
      if (parts[r.depth] === r.label) return parts.slice(0, r.depth + 1).join("/");
    }
    return r.label;
  }

  /** Real links (solid) and suggestions (dashed), bowed out to the right.
   *  When a note is selected only its own edges are drawn — the whole-vault
   *  arc set is exactly the hairball this view exists to avoid. */
  private drawArcs(g: SVGGElement, d: VaultJson, rows: Row[]): void {
    const yOf = new Map<string, number>();
    for (const r of rows) if (r.note) yOf.set(r.note.file_uuid, r.y);

    const arc = (a: string, b: string, cls: string, title: string) => {
      const y1 = yOf.get(a);
      const y2 = yOf.get(b);
      if (y1 === undefined || y2 === undefined) return;
      const bow = Math.min(ARC_MAX, 20 + Math.abs(y2 - y1) * 0.35);
      const p = svgEl("path");
      p.classList.add("nl-link", cls);
      p.setAttribute(
        "d",
        `M${ARC_X},${y1} C${ARC_X + bow},${y1} ${ARC_X + bow},${y2} ${ARC_X},${y2}`,
      );
      const t = svgEl("title");
      t.textContent = title;
      p.appendChild(t);
      g.appendChild(p);
    };

    const names = new Map(d.notes.map((n) => [n.file_uuid, stripExt(n.name)]));
    const involves = (a: string, b: string) =>
      !this.selected || this.selected === a || this.selected === b;

    for (const l of d.links) {
      if (!involves(l.src, l.dst)) continue;
      arc(l.src, l.dst, "real",
          tr("vault.linkTip", { a: names.get(l.src) ?? "?", b: names.get(l.dst) ?? "?" }));
    }
    // Only show suggestions for a selected note, or they drown the real links.
    for (const b of d.bridges) {
      if (!this.selected || !involves(b.a, b.b)) continue;
      arc(b.a, b.b, "suggested",
          tr("vault.bridgeTip", {
            a: names.get(b.a) ?? "?",
            b: names.get(b.b) ?? "?",
            n: b.n_shared,
            shared: b.shared.slice(0, 8).join(", "),
          }));
    }
  }

  private async openNote(n: VaultNote): Promise<void> {
    const f = this.app.vault.getAbstractFileByPath(
      n.path.replace(`${vaultRoot(this.app) ?? ""}/`, ""),
    );
    if (f instanceof TFile) {
      await this.app.workspace.getLeaf(false).openFile(f);
      return;
    }
    const alt = this.app.metadataCache.getFirstLinkpathDest(stripExt(n.name), "");
    if (alt) await this.app.workspace.getLeaf(false).openFile(alt);
  }

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
        this.zoom = Math.max(0.2, Math.min(4, this.zoom * (e.deltaY < 0 ? 1.1 : 1 / 1.1)));
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
