// Shared SVG helpers for the two graph views.

const SVG_NS = "http://www.w3.org/2000/svg";

export function svgEl<K extends keyof SVGElementTagNameMap>(
  tag: K,
): SVGElementTagNameMap[K] {
  return document.createElementNS(SVG_NS, tag) as SVGElementTagNameMap[K];
}

/** Below this the labels stop being readable, so we stop shrinking. */
const MIN_SCALE = 0.6;

/**
 * Size the viewBox so the graph stays legible in a narrow pane.
 *
 * These views live in the right sidebar (~300px), which breaks the obvious
 * `viewBox="0 0 contentW contentH"` + `meet` in three ways:
 *
 *  - it fits the *whole* width, so a 700px-wide graph scales to 43% and 9px
 *    labels become 4px;
 *  - `meet` fits the height too, so the more notes you have the smaller they
 *    get — a long vault becomes a smudge;
 *  - the widest part is the *optional* region (a selected block's phrases, the
 *    link arcs). Letting that drive the zoom shrinks the column you actually
 *    read to make room for something off to the side.
 *
 * So `fitWidth` is the **primary column** — the outline itself — not the total
 * content. Fit that to the pane, never enlarge past 1:1 (a wide pane should
 * reveal more graph, not bigger text), and never shrink past `MIN_SCALE`.
 * Anything beyond, horizontally or vertically, is reached by panning. Matching
 * the viewBox's aspect to the container's is what reduces `meet` to a pure
 * width fit.
 *
 * Returns the visible width in viewBox units, so a caller can tell whether an
 * optional column will actually be on screen.
 */
export function setViewBox(
  svg: SVGSVGElement,
  container: HTMLElement,
  fitWidth: number,
): number {
  const cw = container.clientWidth || 600;
  const ch = container.clientHeight || 420;
  const scale = Math.min(1, Math.max(MIN_SCALE, cw / fitWidth));
  const vw = cw / scale;
  svg.setAttribute("viewBox", `0 0 ${vw} ${ch / scale}`);
  svg.setAttribute("preserveAspectRatio", "xMinYMin meet");
  return vw;
}

export function clip(s: string, n: number): string {
  const t = (s || "").trim().replace(/\s+/g, " ");
  return t.length > n ? t.slice(0, n) + "…" : t;
}

/** ``Note.md`` → ``Note`` — wikilinks name the note, not the file. */
export function stripExt(name: string): string {
  return name.replace(/\.(md|markdown|txt)$/i, "");
}
