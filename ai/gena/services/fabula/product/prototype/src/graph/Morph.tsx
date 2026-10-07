import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { ProcessGraph } from './ProcessGraph'
import { bounds } from './layout'
import type { Box, DiffGraph, Layout, Overlay } from './types'

/* A frozen animation moment, set by the screenshot script through window.__freeze. */

export type Freeze = { from?: string; to?: string; t?: number } | null
let frozen: Freeze = null
const subs = new Set<() => void>()
declare global {
  interface Window {
    __freeze: (f: Freeze) => void
    __ready: boolean
  }
}
window.__freeze = (f) => {
  frozen = f
  subs.forEach((s) => s())
}
export function useFreeze(): Freeze {
  return useSyncExternalStore(
    (s) => (subs.add(s), () => subs.delete(s)),
    () => frozen,
  )
}

export type Frame = { graph: DiffGraph; layout: Layout }

const ease = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2)
const lerp = (a: number, b: number, t: number) => a + (b - a) * t

/**
 * Draws the move from one graph to another in three beats: what goes away fades out
 * where it was, the nodes both graphs have slide to their new places, and what is new
 * fades in where it will be. Parameter changes cannot be animated; the target's chips
 * show them.
 */
export function MorphView({ from, to, t, width, height, overlay, tags = true, viewBox, pad }: { from: Frame; to: Frame; t: number; width: number; height: number; overlay?: Overlay; tags?: boolean; viewBox?: Box; pad?: number }) {
  const k = ease(Math.max(0, Math.min(1, t)))
  const fadeOut = Math.max(0, 1 - 2.5 * k)
  const fadeIn = Math.max(0, 2.5 * k - 1.5)
  const toIds = new Set(to.graph.nodes.map((n) => n.id))
  const gone = from.graph.nodes.filter((n) => !toIds.has(n.id))
  // Until the middle of the move the nodes both graphs have keep their old look.
  const fromNodes = new Map(from.graph.nodes.map((n) => [n.id, n]))
  const nodes = to.graph.nodes.map((n) => (k < 0.5 && fromNodes.get(n.id)) || n)
  const layout: Layout = {}
  const nodeOpacity: Record<string, number> = {}
  for (const n of to.graph.nodes) {
    const a = from.layout[n.id]
    const b = to.layout[n.id]
    if (!b) continue
    if (a && from.graph.nodes.some((m) => m.id === n.id)) layout[n.id] = { x: lerp(a.x, b.x, k), y: lerp(a.y, b.y, k), w: b.w, h: lerp(a.h, b.h, k) }
    else {
      layout[n.id] = b
      nodeOpacity[n.id] = fadeIn
    }
  }
  for (const n of gone) {
    layout[n.id] = from.layout[n.id]
    nodeOpacity[n.id] = fadeOut
  }
  const edgeKey = (e: { source: string; target: string }) => `${e.source}->${e.target}`
  const toEdges = new Map(to.graph.edges.map((e) => [edgeKey(e), e]))
  const fromEdges = new Map(from.graph.edges.map((e) => [edgeKey(e), e]))
  const edgeOpacity: Record<string, number> = {}
  const edges = to.graph.edges.map((e) => {
    const old = fromEdges.get(edgeKey(e))
    return old && k < 0.5 ? { ...e, state: old.state } : e
  })
  for (const e of to.graph.edges) if (!fromEdges.has(edgeKey(e))) edgeOpacity[e.id] = fadeIn
  for (const [key, e] of fromEdges) {
    if (!toEdges.has(key)) {
      const id = `gone:${key}`
      edges.push({ ...e, id })
      edgeOpacity[id] = fadeOut * 0.9
    }
  }
  const graph: DiffGraph = { nodes: [...nodes, ...gone], edges }
  return (
    <ProcessGraph
      graph={graph}
      layout={layout}
      width={width}
      height={height}
      overlay={overlay}
      tags={tags && (k < 0.2 || k > 0.8)}
      viewBox={viewBox ?? bounds(from.layout, to.layout)}
      pad={pad}
      nodeOpacity={nodeOpacity}
      edgeOpacity={edgeOpacity}
    />
  )
}

type MorphProps = { frames: Record<string, Frame>; target: string; width: number; height: number; overlay?: Overlay; viewBox?: Box; pad?: number; duration?: number; initial?: string; delay?: number }

/**
 * Animates towards `target` whenever it changes; a frozen moment wins. With `initial` the
 * move also plays once on mount, from `initial` to `target`, after `delay` ms; screenshots
 * start at `target` right away.
 */
export function Morph({ frames, target, width, height, overlay, viewBox, pad, duration = 750, initial, delay = 0 }: MorphProps) {
  const freeze = useFreeze()
  const [state, setState] = useState(() => {
    const at = document.documentElement.classList.contains('shooting') ? target : (initial ?? target)
    return { from: at, to: at, t: 1 }
  })
  const raf = useRef(0)
  const first = useRef(true)
  useEffect(() => {
    if (target === state.to) return
    const from = state.to
    const t0 = performance.now() + (first.current ? delay : 0)
    cancelAnimationFrame(raf.current)
    const step = (now: number) => {
      first.current = false
      const t = Math.max(0, Math.min(1, (now - t0) / duration))
      setState({ from, to: target, t })
      if (t < 1) raf.current = requestAnimationFrame(step)
    }
    raf.current = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf.current)
  }, [target])
  const s = freeze && freeze.to && frames[freeze.to] ? { from: freeze.from ?? freeze.to, to: freeze.to, t: freeze.t ?? 1 } : state
  return <MorphView from={frames[s.from]} to={frames[s.to]} t={s.t} width={width} height={height} overlay={overlay} viewBox={viewBox} pad={pad} />
}
