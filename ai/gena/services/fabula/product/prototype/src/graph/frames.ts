import { asDiff, diffGraphs } from './diff'
import { DIFF_LANES, LANES, RENAMES_110, V100, V110, V110_BEFORE_EDIT, VARIANT_A, VARIANT_B, VARIANT_C } from './data'
import { layoutOf, register } from './layout'
import type { DiffGraph, DiffState, Graph } from './types'
import type { Frame } from './Morph'

const renamed = (g: Graph, renames: Record<string, string>): Graph => ({
  nodes: g.nodes.map((n) => ({ ...n, id: renames[n.id] ?? n.id })),
  edges: g.edges.map((e) => ({ ...e, source: renames[e.source] ?? e.source, target: renames[e.target] ?? e.target })),
})

/** The target side of a diff: what stays, with its states. */
const after = (d: DiffGraph): DiffGraph => ({ nodes: d.nodes.filter((n) => n.state !== 'removed'), edges: d.edges.filter((e) => e.state !== 'removed') })

/** The source side of a diff: the old graph, with what goes away and what changes marked. */
function before(g: Graph, d: DiffGraph): DiffGraph {
  const states = new Map(d.nodes.map((n) => [n.id, n]))
  const gone = new Set(d.edges.filter((e) => e.state === 'removed').map((e) => `${e.source}->${e.target}`))
  return {
    nodes: g.nodes.map((n) => {
      const m = states.get(n.id)
      const state: DiffState = m?.state === 'removed' || m?.state === 'changed' || m?.state === 'renamed' ? m.state : 'same'
      return { ...n, state, oldId: m?.oldId }
    }),
    edges: g.edges.map((e) => ({ ...e, state: gone.has(`${e.source}->${e.target}`) ? 'removed' : 'same' })),
  }
}

export const DIFF_110 = diffGraphs(V100, V110, RENAMES_110)
const V100R = renamed(V100, RENAMES_110)
const DIFF_B = diffGraphs(VARIANT_A, VARIANT_B)
export const DIFF_EDIT = diffGraphs(V110_BEFORE_EDIT, V110)
const DIFF_C = diffGraphs(VARIANT_A, VARIANT_C)

register('v100', V100.nodes, V100.edges, { lanes: LANES })
register('v100r', V100R.nodes, V100R.edges, { lanes: LANES })
register('v110', V110.nodes, V110.edges, { lanes: LANES })
register('diff110', DIFF_110.nodes, DIFF_110.edges, { lanes: DIFF_LANES })
register('edit', DIFF_EDIT.nodes, DIFF_EDIT.edges, { lanes: LANES })
register('v110pre', V110_BEFORE_EDIT.nodes, V110_BEFORE_EDIT.edges, { lanes: LANES })
register('varA', VARIANT_A.nodes, VARIANT_A.edges, { lanes: LANES })
register('varB', VARIANT_B.nodes, VARIANT_B.edges, { lanes: LANES })
register('varC', VARIANT_C.nodes, VARIANT_C.edges, { lanes: LANES })

export const frames = {
  /** 1.0.0 and 1.1.0 of the diff viewer: «Было» and «Стало». */
  versions: (): Record<string, Frame> => ({
    before: { graph: before(V100R, DIFF_110), layout: layoutOf('v100r') },
    after: { graph: after(DIFF_110), layout: layoutOf('v110') },
  }),
  /** The draft before and after Женя's edit on the canvas. */
  edit: (): Record<string, Frame> => ({
    before: { graph: before(V110_BEFORE_EDIT, DIFF_EDIT), layout: layoutOf('v110pre') },
    after: { graph: after(DIFF_EDIT), layout: layoutOf('v110') },
  }),
  variants: (): Record<string, Frame> => ({
    A: { graph: asDiff(VARIANT_A), layout: layoutOf('varA') },
    B: { graph: after(DIFF_B), layout: layoutOf('varB') },
    C: { graph: after(DIFF_C), layout: layoutOf('varC') },
  }),
}
