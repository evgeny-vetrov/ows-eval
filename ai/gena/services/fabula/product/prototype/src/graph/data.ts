import type { Graph, GNode, GEdge, Overlay } from './types'

/* The running example: the engine's pilot scenario gena/ticket-to-prod. */

const start: GNode = { id: 'start', kind: 'start', title: 'Тикет с меткой ai-ready', sub: 'вход: ticket' }
const runAgent: GNode = { id: 'runAgent', kind: 'call', tool: 'agent', title: 'Агент делает PR', sub: 'coder · gena', params: [{ k: 'таймаут', v: '2 дн' }] }
const awaitMerge: GNode = { id: 'awaitMerge', kind: 'listen', title: 'Ждать мержа PR', sub: 'vcs.pr.merged', params: [{ k: 'срок', v: '30 дн' }] }
const soak: GNode = { id: 'soak', kind: 'wait', title: 'Выдержать изменение', sub: 'wait', params: [{ k: 'пауза', v: '3 дн' }] }
const awaitStable: GNode = { id: 'awaitStable', kind: 'listen', title: 'Ждать стабильности релиза', sub: 'monitoring.release.stable', params: [{ k: 'срок', v: '5 дн' }] }
const healthCheck: GNode = { id: 'healthCheck', kind: 'try', tool: 'http', title: 'Проверить здоровье', sub: 'GET service.example/health', params: [{ k: 'повторы', v: '3, экспон.' }] }
const probeHealth: GNode = { ...healthCheck, id: 'probeHealth' }
const approve0: GNode = { id: 'approve', kind: 'call', tool: 'human', title: 'Спросить владельца', sub: 'владелец тикета, в трекере', params: [{ k: 'таймаут', v: '—' }] }
const approve1: GNode = { ...approve0, params: [{ k: 'таймаут', v: '1 дн' }] }
const remind: GNode = { id: 'remind', kind: 'call', tool: 'mcp', title: 'Напомнить в тикете', sub: 'tracker · add_comment', params: [{ k: 'не больше', v: '3 раз' }] }
const decide: GNode = { id: 'decide', kind: 'switch', title: 'Одобрено?', sub: 'switch' }
const comment: GNode = { id: 'comment', kind: 'call', tool: 'mcp', title: 'Комментарий в тикете', sub: 'tracker · add_comment' }
const end: GNode = { id: 'end', kind: 'end', title: 'Готово', sub: 'выход: ticket, pr, approved' }

const e = (source: string, target: string, label?: string, extra: Partial<GEdge> = {}): GEdge => ({ id: `${source}->${target}`, source, target, label, ...extra })

export const V100: Graph = {
  nodes: [start, runAgent, awaitMerge, soak, healthCheck, approve0, decide, comment, end],
  edges: [
    e('start', 'runAgent'),
    e('runAgent', 'awaitMerge'),
    e('awaitMerge', 'soak'),
    e('soak', 'healthCheck'),
    e('healthCheck', 'approve'),
    e('approve', 'decide'),
    e('decide', 'comment', 'да'),
    e('decide', 'end', 'нет', { sh: 'r', th: 'r' }),
    e('comment', 'end'),
  ],
}

export const V110: Graph = {
  nodes: [start, runAgent, awaitMerge, awaitStable, probeHealth, approve1, remind, decide, comment, end],
  edges: [
    e('start', 'runAgent'),
    e('runAgent', 'awaitMerge'),
    e('awaitMerge', 'awaitStable'),
    e('awaitStable', 'probeHealth'),
    e('probeHealth', 'approve'),
    e('approve', 'decide'),
    e('approve', 'remind', 'нет ответа 1 дн', { sh: 'r', th: 't' }),
    e('remind', 'approve', 'снова ждать', { sh: 'r', th: 'tr', back: true }),
    e('decide', 'comment', 'да'),
    e('decide', 'end', 'нет', { sh: 'l', th: 'l' }),
    e('comment', 'end'),
  ],
}

export const RENAMES_110 = { healthCheck: 'probeHealth' }

/** The draft right before Женя's edit on the canvas: no reminder yet. */
export const V110_BEFORE_EDIT: Graph = {
  nodes: V110.nodes.filter((n) => n.id !== 'remind').map((n) => (n.id === 'approve' ? approve0 : n)),
  edges: V110.edges.filter((x) => x.source !== 'remind' && x.target !== 'remind'),
}

/** Nodes off the spine: 1 to the right of it, -1 to the left. */
export const LANES = { remind: 1 }
/** In a diff the removed soak stands beside its replacement. */
export const DIFF_LANES = { ...LANES, soak: 1 }

/** Where the 128 running fabulas of 1.0.0 stand. */
export const RUNS_100: Overlay['runs'] = {
  runAgent: { n: 14 },
  awaitMerge: { n: 83 },
  soak: { n: 10, tone: 'bad' },
  approve: { n: 21, tone: 'warn' },
}

/* Clarifying question: three readings of "ask the owner" (a fragment of the graph). */

const fragStart: GNode = { ...healthCheck, params: [] }
const approveT2: GNode = { ...approve0, params: [{ k: 'таймаут', v: '2 дн' }] }
const remind2: GNode = { ...remind, params: [] }

export const VARIANT_A: Graph = {
  nodes: [fragStart, approve0, decide, comment, end],
  edges: [e('healthCheck', 'approve'), e('approve', 'decide'), e('decide', 'comment', 'да'), e('decide', 'end', 'нет', { sh: 'r', th: 'r' }), e('comment', 'end')],
}
export const VARIANT_B: Graph = {
  nodes: [fragStart, approveT2, remind2, decide, comment, end],
  edges: [
    e('healthCheck', 'approve'),
    e('approve', 'decide'),
    e('approve', 'remind', 'нет ответа 2 дн', { sh: 'r', th: 't' }),
    e('remind', 'approve', 'снова ждать', { sh: 'r', th: 'tr', back: true }),
    e('decide', 'comment', 'да'),
    e('decide', 'end', 'нет', { sh: 'l', th: 'l' }),
    e('comment', 'end'),
  ],
}
export const VARIANT_C: Graph = {
  nodes: [fragStart, approveT2, decide, comment, end],
  edges: [
    e('healthCheck', 'approve'),
    e('approve', 'decide'),
    e('approve', 'end', 'нет ответа 2 дн → отказ', { sh: 'l', th: 'l' }),
    e('decide', 'comment', 'да'),
    e('decide', 'end', 'нет', { sh: 'r', th: 'r' }),
    e('comment', 'end'),
  ],
}

/* Prompt of 1.0.0 and the describer's update for 1.1.0. */

export type Clause = { n: number; text: string; nodes: string[] }

export const PROMPT_100: Clause[] = [
  { n: 1, text: 'Когда в трекере появляется тикет с меткой ai-ready, запусти агента: он делает изменение и открывает PR. На работу агента — не больше 2 дней.', nodes: ['start', 'runAgent'] },
  { n: 2, text: 'Жди, пока PR смержат, — до 30 дней.', nodes: ['awaitMerge'] },
  { n: 3, text: 'После мержа выдержи изменение 3 дня.', nodes: ['soak'] },
  { n: 4, text: 'Проверь здоровье сервиса. Если сервис не отвечает, повтори проверку до 3 раз с растущей паузой.', nodes: ['healthCheck'] },
  { n: 5, text: 'Спроси у владельца тикета, выкатываем ли изменение. Ждать ответа без отдельного срока.', nodes: ['approve'] },
  { n: 6, text: 'Если владелец согласен, оставь в тикете комментарий «Выкачено» со ссылкой на PR. Если нет — заверши процесс.', nodes: ['decide', 'comment', 'end'] },
  { n: 7, text: 'Весь процесс — не дольше 60 дней.', nodes: ['start'] },
]

/** Женя's first prompt from «+ Новый сценарий», split into clauses; clause 5 is ambiguous. */
export const PROMPT_NEW: Clause[] = [
  { n: 1, text: 'Когда в трекере появляется тикет с меткой ai-ready, запусти агента: он делает изменение и открывает PR.', nodes: ['start', 'runAgent'] },
  { n: 2, text: 'Жди мержа до 30 дней.', nodes: ['awaitMerge'] },
  { n: 3, text: 'Потом выдержи изменение 3 дня.', nodes: ['soak'] },
  { n: 4, text: 'Проверь здоровье сервиса.', nodes: ['healthCheck'] },
  { n: 5, text: 'Спроси у владельца тикета, выкатываем ли.', nodes: ['approve'] },
  { n: 6, text: 'Если да — оставь в тикете комментарий «Выкачено», если нет — заверши.', nodes: ['decide', 'comment', 'end'] },
  { n: 7, text: 'Весь процесс не дольше 60 дней.', nodes: ['start'] },
]

export const PROMPT_110: Clause[] = PROMPT_100.map((c) =>
  c.n === 3
    ? { ...c, text: 'После мержа дождись от мониторинга сигнала, что релиз стабилен, — до 5 дней.', nodes: ['awaitStable'] }
    : c.n === 4
      ? { ...c, nodes: ['probeHealth'] }
      : c.n === 5
        ? { ...c, text: 'Спроси у владельца тикета, выкатываем ли изменение. Если владелец молчит сутки, напомни в тикете — не больше 3 раз.', nodes: ['approve', 'remind'] }
        : c,
)
