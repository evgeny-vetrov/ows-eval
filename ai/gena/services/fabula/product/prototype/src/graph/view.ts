import type { Box, Layout } from './types'

/** The box around some nodes of a layout, to aim a viewport at them. */
export function boxOf(layout: Layout, ids: string[]): Box {
  const bs = ids.map((id) => layout[id]).filter(Boolean)
  const x0 = Math.min(...bs.map((b) => b.x))
  const y0 = Math.min(...bs.map((b) => b.y))
  const x1 = Math.max(...bs.map((b) => b.x + b.w))
  const y1 = Math.max(...bs.map((b) => b.y + b.h))
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 }
}

/** Extra room beside a box, for loop edges that run outside the nodes. */
export function widen(box: Box, left: number, right: number): Box {
  return { ...box, x: box.x - left, w: box.w + left + right }
}
