import type { Box, GEdge, GNode, Layout } from './types'

export const NODE_W = 250

/** Height follows the content: a long title wraps, chips take a row. */
export function nodeSize(n: GNode): { w: number; h: number } {
  if (n.kind === 'start' || n.kind === 'end') return { w: NODE_W, h: 58 }
  const lines = Math.ceil(n.title.length / 25)
  return { w: NODE_W, h: 56 + 19 * lines + (n.params && n.params.length ? 28 : 0) }
}

export type LayoutOptions = { lanes?: Record<string, number>; rowGap?: number; laneGap?: number }

/**
 * A process reads top down along its spine. Rows follow the longest path over forward
 * edges (a `back` edge closes a loop and does not count); a node off the spine sits in
 * its lane, 1 to the right, -1 to the left.
 */
export function spineLayout(nodes: GNode[], edges: GEdge[], opts: LayoutOptions = {}): Layout {
  const lanes = opts.lanes ?? {}
  const rowGap = opts.rowGap ?? 50
  const laneGap = opts.laneGap ?? 70
  const ids = new Set(nodes.map((n) => n.id))
  const fwd = edges.filter((e) => !e.back && ids.has(e.source) && ids.has(e.target))
  const row: Record<string, number> = {}
  const visit = (id: string, seen: Set<string>): number => {
    if (row[id] !== undefined) return row[id]
    if (seen.has(id)) return 0
    seen.add(id)
    const preds = fwd.filter((e) => e.target === id).map((e) => visit(e.source, seen) + 1)
    return (row[id] = Math.max(0, ...preds))
  }
  nodes.forEach((n) => visit(n.id, new Set()))
  const rows = Math.max(0, ...Object.values(row)) + 1
  const heights = Array.from({ length: rows }, (_, r) => Math.max(0, ...nodes.filter((n) => row[n.id] === r).map((n) => nodeSize(n).h)))
  const top = heights.map((_, r) => heights.slice(0, r).reduce((a, h) => a + h + rowGap, 0))
  const out: Layout = {}
  for (const n of nodes) {
    const { w, h } = nodeSize(n)
    const r = row[n.id]
    out[n.id] = { x: (lanes[n.id] ?? 0) * (NODE_W + laneGap), y: top[r] + (heights[r] - h) / 2, w, h }
  }
  return out
}

export function bounds(...layouts: Layout[]): Box {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity
  for (const l of layouts) {
    for (const b of Object.values(l)) {
      x0 = Math.min(x0, b.x); y0 = Math.min(y0, b.y)
      x1 = Math.max(x1, b.x + b.w); y1 = Math.max(y1, b.y + b.h)
    }
  }
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 }
}

/** A viewport that fits the box into a frame, never zooming in past `max`. */
export function fit(box: Box, width: number, height: number, pad = 36, max = 1) {
  const zoom = Math.min(max, (width - 2 * pad) / box.w, (height - 2 * pad) / box.h)
  return { zoom, x: (width - box.w * zoom) / 2 - box.x * zoom, y: (height - box.h * zoom) / 2 - box.y * zoom }
}

/* Layouts are computed once, at start, so screens draw synchronously. */

const cache = new Map<string, Layout>()

export function register(name: string, nodes: GNode[], edges: GEdge[], opts?: LayoutOptions) {
  cache.set(name, spineLayout(nodes, edges, opts))
}

/** Layouts are synchronous; kept async for an engine that is not (ELK). */
export async function ready() {}

export function layoutOf(name: string): Layout {
  const l = cache.get(name)
  if (!l) throw new Error(`no layout ${name}`)
  return l
}
