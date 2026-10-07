import { memo, type CSSProperties } from 'react'
import { Handle, Position, ReactFlow, type Edge, type Node, type NodeProps } from '@xyflow/react'
import { Bot, Check, Flag, GitBranch, Globe, Hourglass, Pencil, Play, Plug, Puzzle, Radio, RotateCw, Send, User, Users, Workflow } from 'lucide-react'
import type { Box, DiffEdge, DiffGraph, DiffNode, Kind, Layout, Overlay, Tool } from './types'
import { bounds, fit } from './layout'

const KIND: Record<Kind, { label: string; color: string; Icon: typeof Puzzle }> = {
  start: { label: 'старт', color: '#fff', Icon: Play },
  end: { label: 'конец', color: '#fff', Icon: Flag },
  call: { label: 'шаг', color: 'var(--k-call)', Icon: Puzzle },
  listen: { label: 'ждёт событие', color: 'var(--k-listen)', Icon: Radio },
  wait: { label: 'пауза', color: 'var(--k-wait)', Icon: Hourglass },
  try: { label: 'повторы', color: 'var(--k-try)', Icon: RotateCw },
  switch: { label: 'развилка', color: 'var(--k-switch)', Icon: GitBranch },
  set: { label: 'данные', color: 'var(--k-set)', Icon: Pencil },
  emit: { label: 'событие', color: 'var(--k-emit)', Icon: Send },
}

/** A step that calls something is labelled by what it calls. */
export const TOOL: Record<Tool, { label: string; color: string; Icon: typeof Puzzle }> = {
  agent: { label: 'агент', color: 'var(--k-agent)', Icon: Bot },
  human: { label: 'человек', color: 'var(--k-call)', Icon: User },
  http: { label: 'HTTP', color: 'var(--k-http)', Icon: Globe },
  mcp: { label: 'MCP', color: 'var(--k-mcp)', Icon: Plug },
  nirvana: { label: 'Нирвана', color: 'var(--k-nirvana)', Icon: Workflow },
}

const TAG: Record<string, string> = { added: 'новый', removed: 'уходит', changed: 'изменён', renamed: 'переименован' }

type StepData = {
  node: DiffNode
  tags: boolean
  runs?: { n: number; tone?: string }
  cls: string
  visited: boolean
  warn: boolean
}

function StepNode({ data }: NodeProps<Node<StepData>>) {
  const { node, tags, runs, cls, visited, warn } = data
  const k = node.tool ? TOOL[node.tool] : KIND[node.kind]
  const term = node.kind === 'start' || node.kind === 'end'
  return (
    <div className={`step ${term ? 'term' : ''} st-${node.state} ${cls} ${warn ? 'warn' : ''}`}>
      {warn && <span className="warn-ico">!</span>}
      <Handle type="target" position={Position.Top} id="t" />
      <Handle type="source" position={Position.Bottom} id="b" />
      <Handle type="source" position={Position.Right} id="r-s" />
      <Handle type="target" position={Position.Right} id="r-t" />
      <Handle type="source" position={Position.Left} id="l-s" />
      <Handle type="target" position={Position.Left} id="l-t" />
      <Handle type="target" position={Position.Top} id="tr-t" style={{ left: '84%' }} />
      {tags && node.state !== 'same' && <span className="tag">{TAG[node.state]}{node.oldId ? ` · было ${node.oldId}` : ''}</span>}
      {runs && (
        <span className={`runs ${runs.tone ?? ''}`}>
          <Users size={12} />
          {runs.n}
        </span>
      )}
      {term ? (
        <>
          <div className="title">{node.title}</div>
          <div className="sub">{node.sub}</div>
        </>
      ) : (
        <>
          <div className="kind" style={{ color: k.color }}>
            <k.Icon size={13} strokeWidth={2.4} />
            {k.label}
            <span className="id">{node.id}</span>
          </div>
          <div className="title">{node.title}</div>
          <div className="sub">{node.sub}</div>
          {!!node.params?.length && (
            <div className="chips">
              {node.params.map((p) => {
                const c = node.changes?.find((x) => x.k === p.k)
                return c ? (
                  <span key={p.k} className="pchip chg">
                    {p.k}: <span className="from">{c.from}</span> → <span className="to">{c.to}</span>
                  </span>
                ) : (
                  <span key={p.k} className="pchip">
                    {p.k}: {p.v}
                  </span>
                )
              })}
            </div>
          )}
        </>
      )}
      {visited && (
        <span className="vis">
          <Check size={12} strokeWidth={3} />
        </span>
      )}
    </div>
  )
}

const nodeTypes = { step: memo(StepNode) }

const handle = (e: DiffEdge) => ({
  sourceHandle: e.sh === 'r' ? 'r-s' : e.sh === 'l' ? 'l-s' : 'b',
  targetHandle: e.th === 'r' ? 'r-t' : e.th === 'l' ? 'l-t' : e.th === 'tr' ? 'tr-t' : 't',
})

export type GraphProps = {
  graph: DiffGraph
  layout: Layout
  width: number
  height: number
  overlay?: Overlay
  tags?: boolean
  viewBox?: Box
  maxZoom?: number
  nodeOpacity?: Record<string, number>
  edgeOpacity?: Record<string, number>
  pad?: number
}

export function ProcessGraph({ graph, layout, width, height, overlay = {}, tags = true, viewBox, maxZoom = 1, nodeOpacity, edgeOpacity, pad }: GraphProps) {
  const visited = new Set(overlay.visited ?? [])
  const nodes: Node<StepData>[] = graph.nodes
    .filter((n) => layout[n.id])
    .map((n) => {
      const b = layout[n.id]
      const cls = [
        overlay.selected?.includes(n.id) ? 'sel' : '',
        overlay.dim?.includes(n.id) ? 'dim' : '',
        overlay.current === n.id ? 'current pulse' : visited.has(n.id) ? 'visited' : '',
        overlay.focus?.includes(n.id) ? 'focus' : '',
      ].join(' ')
      const style: CSSProperties = { width: b.w, height: b.h, opacity: nodeOpacity?.[n.id] ?? 1 }
      return {
        id: n.id,
        type: 'step',
        position: { x: b.x, y: b.y },
        width: b.w,
        height: b.h,
        style,
        draggable: false,
        data: { node: n, tags, runs: overlay.runs?.[n.id], cls, visited: visited.has(n.id) && overlay.current !== n.id, warn: !!overlay.warn?.includes(n.id) },
      }
    })
  const edges: Edge[] = graph.edges
    .filter((e) => layout[e.source] && layout[e.target])
    .map((e) => {
      const onPath = visited.has(e.source) && (visited.has(e.target) || overlay.current === e.target)
      const color = e.state === 'added' ? 'var(--added)' : e.state === 'removed' ? 'var(--removed)' : onPath ? 'var(--ok)' : 'var(--edge)'
      const op = edgeOpacity?.[e.id] ?? (e.state === 'removed' ? 0.55 : 1)
      return {
        id: e.id,
        source: e.source,
        target: e.target,
        ...handle(e),
        type: 'smoothstep',
        pathOptions: { borderRadius: 12, offset: 26 },
        label: e.label,
        labelBgPadding: [6, 3] as [number, number],
        labelBgBorderRadius: 5,
        labelStyle: { fill: e.state === 'added' ? '#15803d' : e.state === 'removed' ? '#b91c1c' : '#4b5563', fontWeight: 600, opacity: op },
        labelBgStyle: { opacity: op },
        style: {
          stroke: color,
          strokeWidth: e.state === 'same' && !onPath ? 1.6 : 2.4,
          strokeDasharray: e.state === 'removed' ? '6 5' : undefined,
          opacity: op,
        },
        markerEnd: { type: 'arrowclosed' as never, color, width: 16, height: 16 },
      }
    })
  const vp = fit(viewBox ?? bounds(layout), width, height, pad ?? 40, maxZoom)
  return (
    <div className="graph-wrap" style={{ width, height }}>
      <ReactFlow
        key={`${width}x${height}:${Math.round(vp.zoom * 1000)}`}
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        defaultViewport={vp}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        zoomOnScroll={false}
        preventScrolling={false}
        proOptions={{ hideAttribution: true }}
        minZoom={0.2}
        maxZoom={2}
      />
    </div>
  )
}
