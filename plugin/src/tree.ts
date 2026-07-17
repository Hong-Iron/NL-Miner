// Pure tree helpers for the query panel: nesting blocks by parent, and turning
// a node tree into flat rows carrying box-drawing guides. Deliberately free of
// Obsidian imports so the layout logic can be exercised without a vault.

/** One line of the rendered tree. */
export interface TNode {
  /** small leading tag, e.g. the block kind or the relation name */
  tag?: string;
  /** the line's main text */
  label: string;
  /** trailing muted text */
  note?: string;
  hint?: string;
  cls?: string;
  onClick?: () => void;
  children?: TNode[];
}

/** Guide column for a node whose ancestors' "was last sibling" flags are
 *  `ancestors`, and which is itself `last` among its siblings:
 *
 *      ├─ a          ancestors=[]        last=false
 *      │  └─ a1      ancestors=[false]   last=true
 *      └─ b          ancestors=[]        last=true
 *         └─ b1      ancestors=[true]    last=true
 *
 *  An ancestor that was last has no line to continue, so its column is blank;
 *  otherwise the column carries │ down past this row. */
export function guideFor(ancestors: readonly boolean[], last: boolean): string {
  if (ancestors.length === 0) return "";
  return ancestors.map((a) => (a ? "   " : "│  ")).join("") + (last ? "└─ " : "├─ ");
}

export interface FlatRow {
  node: TNode;
  guide: string;
  depth: number;
}

/** Depth-first flatten, computing each row's guide. */
export function flatten(
  nodes: readonly TNode[],
  ancestors: readonly boolean[] = [],
): FlatRow[] {
  const out: FlatRow[] = [];
  nodes.forEach((n, i) => {
    const last = i === nodes.length - 1;
    out.push({ node: n, guide: guideFor(ancestors, last), depth: ancestors.length });
    if (n.children && n.children.length > 0) {
      out.push(...flatten(n.children, [...ancestors, last]));
    }
  });
  return out;
}

export interface BlockLike {
  block_id: string;
  parent_block_id: string | null;
  span: [number, number];
}
export interface Nested<T> {
  block: T;
  children: Nested<T>[];
}

/** Blocks as a tree: document order (by span start), nested by parent_block_id.
 *  A block whose parent isn't in this file surfaces at the top level rather
 *  than vanishing, and a parent cycle can't recurse forever. */
export function nestBlocks<T extends BlockLike>(blocks: readonly T[]): Nested<T>[] {
  const ordered = [...blocks].sort((a, b) => a.span[0] - b.span[0]);
  const ids = new Set(ordered.map((b) => b.block_id));
  const kids = new Map<string, T[]>();
  const roots: T[] = [];
  for (const b of ordered) {
    const p = b.parent_block_id;
    if (p && ids.has(p) && p !== b.block_id) {
      const list = kids.get(p) ?? [];
      list.push(b);
      kids.set(p, list);
    } else {
      roots.push(b);
    }
  }
  const seen = new Set<string>();
  const build = (b: T): Nested<T> => {
    seen.add(b.block_id);
    const children: Nested<T>[] = [];
    for (const k of kids.get(b.block_id) ?? []) {
      if (!seen.has(k.block_id)) children.push(build(k));
    }
    return { block: b, children };
  };
  return roots.map(build);
}

/** Nested blocks flattened back to one row per block, in reading order, each
 *  with its nesting depth. Depth-first, so a block's descendants are always
 *  contiguous below it — the graph view's containment rails rely on that to
 *  find where a parent's subtree ends by scanning forward. */
export function flattenBlocks<T extends BlockLike>(
  blocks: readonly T[],
): Array<{ block: T; depth: number }> {
  const out: Array<{ block: T; depth: number }> = [];
  const walk = (nodes: readonly Nested<T>[], depth: number): void => {
    for (const n of nodes) {
      out.push({ block: n.block, depth });
      walk(n.children, depth + 1);
    }
  };
  walk(nestBlocks(blocks), 0);
  return out;
}
