import type { DiffGraph, DiffNode, Graph, GNode, ParamChange } from './types'

function paramChanges(a: GNode, b: GNode): ParamChange[] {
  const keys = new Set([...(a.params ?? []).map((p) => p.k), ...(b.params ?? []).map((p) => p.k)])
  const out: ParamChange[] = []
  for (const k of keys) {
    const from = a.params?.find((p) => p.k === k)?.v ?? '—'
    const to = b.params?.find((p) => p.k === k)?.v ?? '—'
    if (from !== to) out.push({ k, from, to })
  }
  return out
}

/**
 * Union of two graphs with a state per node and edge. `renames` maps an old node id to
 * its new id (the manager's mapping hints); a renamed node keeps the new id.
 */
export function diffGraphs(a: Graph, b: Graph, renames: Record<string, string> = {}): DiffGraph {
  const back = Object.fromEntries(Object.entries(renames).map(([o, n]) => [n, o]))
  const inA = new Map(a.nodes.map((n) => [n.id, n]))
  const inB = new Map(b.nodes.map((n) => [n.id, n]))
  const nodes: DiffNode[] = []
  for (const n of b.nodes) {
    const oldId = back[n.id]
    const old = inA.get(oldId ?? n.id)
    if (!old) nodes.push({ ...n, state: 'added' })
    else {
      const changes = paramChanges(old, n)
      const state = oldId ? 'renamed' : changes.length || old.title !== n.title ? 'changed' : 'same'
      nodes.push({ ...n, state, oldId, changes })
    }
  }
  for (const n of a.nodes) {
    if (!inB.has(renames[n.id] ?? n.id)) nodes.push({ ...n, state: 'removed' })
  }
  const key = (s: string, t: string) => `${s}->${t}`
  const map = (id: string) => renames[id] ?? id
  const eb = new Map(b.edges.map((e) => [key(e.source, e.target), e]))
  const ea = new Map(a.edges.map((e) => [key(map(e.source), map(e.target)), e]))
  const edges: DiffGraph['edges'] = []
  for (const [k, e] of eb) edges.push({ ...e, id: `b:${k}`, state: ea.has(k) ? 'same' : 'added' })
  for (const [k, e] of ea) {
    if (!eb.has(k)) edges.push({ ...e, id: `a:${k}`, source: map(e.source), target: map(e.target), state: 'removed' })
  }
  return { nodes, edges }
}

export function asDiff(g: Graph): DiffGraph {
  return { nodes: g.nodes.map((n) => ({ ...n, state: 'same' })), edges: g.edges.map((e) => ({ ...e, state: 'same' })) }
}
