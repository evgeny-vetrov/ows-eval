export type Kind = 'start' | 'end' | 'call' | 'listen' | 'wait' | 'try' | 'switch' | 'set' | 'emit'

export type Param = { k: string; v: string }
export type ParamChange = { k: string; from: string; to: string }

/** What a step calls: an agent, a person, an HTTP endpoint, an MCP tool, a Nirvana operation. */
export type Tool = 'agent' | 'human' | 'http' | 'mcp' | 'nirvana'

export type GNode = {
  id: string
  kind: Kind
  tool?: Tool
  title: string
  sub?: string
  params?: Param[]
}

export type GEdge = {
  id: string
  source: string
  target: string
  label?: string
  /** Handles: side of the node the edge leaves or enters, for loops drawn beside the flow. */
  sh?: 'b' | 'r' | 'l'
  th?: 't' | 'r' | 'l' | 'tr'
  /** Closes a loop: drawn, but does not push its target down a row. */
  back?: boolean
}

export type Graph = { nodes: GNode[]; edges: GEdge[] }

export type DiffState = 'same' | 'added' | 'removed' | 'changed' | 'renamed'

export type DiffNode = GNode & { state: DiffState; oldId?: string; changes?: ParamChange[] }
export type DiffEdge = GEdge & { state: 'same' | 'added' | 'removed' }
export type DiffGraph = { nodes: DiffNode[]; edges: DiffEdge[] }

export type Box = { x: number; y: number; w: number; h: number }
export type Layout = Record<string, Box>

/** What a node shows on top of its own content. */
export type Overlay = {
  runs?: Record<string, { n: number; tone?: 'ok' | 'warn' | 'bad' }>
  selected?: string[]
  dim?: string[]
  visited?: string[]
  current?: string
  focus?: string[]
  /** Steps where the behaviour check found something to look at. */
  warn?: string[]
}
