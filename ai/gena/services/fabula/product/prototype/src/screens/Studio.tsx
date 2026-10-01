import { GitBranch, Hourglass, Radio, Search } from 'lucide-react'
import { Shell } from '../ui/Shell'
import { ModeBar, StudioHeader } from '../ui/bits'
import { ProcessGraph, TOOL } from '../graph/ProcessGraph'
import { V110 } from '../graph/data'
import { asDiff } from '../graph/diff'
import { layoutOf } from '../graph/layout'
import { boxOf, widen } from '../graph/view'
import type { Tool } from '../graph/types'

const TOOLS: { tool: Tool; name: string; note: string }[] = [
  { tool: 'agent', name: 'Агент', note: 'coder, reviewer, support…' },
  { tool: 'mcp', name: 'MCP-инструмент', note: 'tracker, calendar, wiki' },
  { tool: 'http', name: 'HTTP-запрос', note: 'любой эндпоинт' },
  { tool: 'nirvana', name: 'Операция Нирваны', note: 'через мост' },
  { tool: 'human', name: 'Человек', note: 'вопрос в трекере' },
]

const FLOW = [
  { Icon: Radio, name: 'Ждать событие', color: 'var(--k-listen)' },
  { Icon: Hourglass, name: 'Пауза', color: 'var(--k-wait)' },
  { Icon: GitBranch, name: 'Развилка', color: 'var(--k-switch)' },
]

function Palette() {
  return (
    <div className="card" style={{ width: 226, flex: 'none' }}>
      <div className="card-h">Шаги</div>
      <div className="card-b" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        <div className="input ph small">
          <Search size={14} />
          Найти шаг
        </div>
        <div className="section-label">Действия</div>
        {TOOLS.map(({ tool, name, note }) => {
          const t = TOOL[tool]
          return (
            <div
              key={tool}
              style={{
                display: 'grid',
                gridTemplateColumns: '26px 1fr',
                gap: 8,
                alignItems: 'center',
              }}
            >
              <span
                style={{
                  width: 26,
                  height: 26,
                  borderRadius: 7,
                  display: 'grid',
                  placeItems: 'center',
                  background: '#f1f3f6',
                  color: t.color,
                }}
              >
                <t.Icon size={15} />
              </span>
              <div>
                <div style={{ fontWeight: 600, fontSize: 14 }}>{name}</div>
                <div className="tiny muted">{note}</div>
              </div>
            </div>
          )
        })}
        <div className="section-label" style={{ marginTop: 4 }}>
          Ход процесса
        </div>
        {FLOW.map(({ Icon, name, color }) => (
          <div
            key={name}
            style={{
              display: 'grid',
              gridTemplateColumns: '26px 1fr',
              gap: 8,
              alignItems: 'center',
            }}
          >
            <span
              style={{
                width: 26,
                height: 26,
                borderRadius: 7,
                display: 'grid',
                placeItems: 'center',
                background: '#f1f3f6',
                color,
              }}
            >
              <Icon size={15} />
            </span>
            <div style={{ fontWeight: 600, fontSize: 14 }}>{name}</div>
          </div>
        ))}
        <div className="tiny muted" style={{ marginTop: 4 }}>
          Перетащите шаг на граф или между шагами.
        </div>
      </div>
    </div>
  )
}

function Field({ label, value, hot = false }: { label: string; value: string; hot?: boolean }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <span className="tiny muted">{label}</span>
      <div
        className="input small"
        style={
          hot
            ? {
                borderColor: 'var(--accent)',
                boxShadow: '0 0 0 3px rgba(79,70,229,.14)',
              }
            : undefined
        }
      >
        {value}
      </div>
    </div>
  )
}

function Inspector() {
  const t = TOOL.human
  return (
    <div className="card" style={{ width: 282, flex: 'none' }}>
      <div className="card-h">
        <t.Icon size={16} color={t.color} />
        Шаг
        <span className="right sub">человек · approve</span>
      </div>
      <div className="card-b" style={{ display: 'flex', flexDirection: 'column', gap: 11 }}>
        <Field label="Название" value="Спросить владельца" />
        <Field label="Кого спросить" value="Владельца тикета, в трекере" />
        <Field label="Вопрос" value="Выкатываем PR-4821?" />
        <Field label="Ждать ответа" value="1 день" hot />
        <Field label="Если ответа нет" value="Напомнить в тикете (MCP · tracker)" hot />
        <Field label="Напоминать не больше" value="3 раз" hot />
        <div className="sep" />
        <div className="section-label">Контракт шага</div>
        <div className="small">
          вход: <span className="mono">ticket</span>, <span className="mono">question</span>
          <br />
          выход: <span className="mono">approved</span> — да или нет
        </div>
      </div>
    </div>
  )
}

export function Studio() {
  const layout = layoutOf('v110')
  const box = widen(boxOf(layout, ['awaitStable', 'probeHealth', 'approve', 'remind', 'decide', 'comment']), 30, 60)
  return (
    <Shell section="scenarios" crumbs={['Сценарии', 'От тикета до выкатки', 'Редактор']} who="zhenya" compact>
      <StudioHeader />
      <ModeBar mode="graph" hint="Правите граф. Промт и OWS подтянутся сами — их изменения видны в «Посмотреть изменения»." />
      <div className="row" style={{ alignItems: 'flex-start' }}>
        <Palette />
        <div className="card grow" style={{ padding: 10 }}>
          <div className="canvas-fade">
            <ProcessGraph graph={asDiff(V110)} layout={layout} width={544} height={760} overlay={{ selected: ['approve'] }} viewBox={box} maxZoom={0.9} pad={24} />
          </div>
        </div>
        <Inspector />
      </div>
    </Shell>
  )
}
