import { memo, type ReactNode } from 'react'
import { Handle, Position, ReactFlow, type Edge, type Node, type NodeProps } from '@xyflow/react'
import { Braces, CircleHelp, Database, Eye, MessageSquareText, Rocket, Sparkles, Workflow } from 'lucide-react'
import { PEOPLE, type Who } from '../ui/Shell'

/* Diagrams for the product doc: boxes placed by hand, edges drawn by React Flow. */

type BoxData = { title: ReactNode; lines?: string[]; tone?: 'accent' | 'ok' | 'warn' | 'dark' | 'ghost'; who?: Who[]; icon?: ReactNode }

const SIDES = { t: Position.Top, r: Position.Right, b: Position.Bottom, l: Position.Left }
const AT = [25, 50, 75]

function BoxNode({ data }: NodeProps<Node<BoxData>>) {
  return (
    <div className={`box ${data.tone ?? ''}`}>
      {Object.entries(SIDES).flatMap(([k, pos]) =>
        AT.map((a) => {
          const style = k === 't' || k === 'b' ? { left: `${a}%` } : { top: `${a}%` }
          return [
            <Handle key={`${k}${a}s`} id={`${k}${a}s`} type="source" position={pos} style={style} />,
            <Handle key={`${k}${a}t`} id={`${k}${a}t`} type="target" position={pos} style={style} />,
          ]
        }),
      )}
      <div className="bt">
        {data.icon}
        {data.title}
      </div>
      {data.lines?.map((l) => (
        <div key={l} className="bl">
          {l}
        </div>
      ))}
      {data.who && (
        <div className="who">
          {data.who.map((w) => (
            <span key={w} className="badge" style={{ background: PEOPLE[w].color, color: '#fff', fontSize: 12 }}>
              {PEOPLE[w].name}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

const nodeTypes = { box: memo(BoxNode) }

type B = { id: string; x: number; y: number; w: number; h: number } & BoxData
type L = { from: string; to: string; fh?: string; th?: string; label?: string; dashed?: boolean; color?: string; both?: boolean }

function Diagram({ boxes, links, width, height, title }: { boxes: B[]; links: L[]; width: number; height: number; title?: string }) {
  const nodes: Node<BoxData>[] = boxes.map((b) => ({ id: b.id, type: 'box', position: { x: b.x, y: b.y }, width: b.w, height: b.h, style: { width: b.w, height: b.h }, data: b, draggable: false }))
  const edges: Edge[] = links.map((l, i) => {
    const color = l.color ?? '#6b7280'
    return {
      id: `e${i}`,
      source: l.from,
      target: l.to,
      sourceHandle: `${l.fh ?? 'r50'}s`,
      targetHandle: `${l.th ?? 'l50'}t`,
      type: 'smoothstep',
      pathOptions: { borderRadius: 14, offset: 22 },
      label: l.label,
      labelBgPadding: [6, 3] as [number, number],
      labelBgBorderRadius: 5,
      labelStyle: { fontWeight: 600, fill: '#374151', fontSize: 13 },
      style: { stroke: color, strokeWidth: 2, strokeDasharray: l.dashed ? '6 5' : undefined },
      markerEnd: { type: 'arrowclosed' as never, color, width: 16, height: 16 },
      markerStart: l.both ? ({ type: 'arrowclosed', color, width: 16, height: 16, orient: 'auto-start-reverse' } as never) : undefined,
    }
  })
  return (
    <div data-shot="diagram" style={{ width, background: '#fff', padding: '18px 0 0' }}>
      {title && <div style={{ fontWeight: 700, fontSize: 18, padding: '0 24px 4px' }}>{title}</div>}
      <div style={{ width, height }}>
        <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} defaultViewport={{ x: 24, y: 16, zoom: 1 }} nodesDraggable={false} nodesConnectable={false} elementsSelectable={false} zoomOnScroll={false} panOnDrag={false} preventScrolling={false} proOptions={{ hideAttribution: true }} />
      </div>
    </div>
  )
}

export function ModelDiagram() {
  return (
    <Diagram
      width={900}
      height={630}
      title="Правка: один режим за раз, потом «Посмотреть изменения»"
      boxes={[
        { id: 'graph', x: 0, y: 0, w: 200, h: 96, icon: <Workflow size={17} />, title: 'Граф', lines: ['палитра шагов, инспектор', 'для не-разработчиков'] },
        { id: 'ows', x: 0, y: 124, w: 200, h: 96, icon: <Braces size={17} />, title: 'OWS', lines: ['код для инженеров', 'то же — через API'] },
        { id: 'prompt', x: 0, y: 340, w: 200, h: 96, tone: 'accent', icon: <MessageSquareText size={17} />, title: 'Промт', lines: ['текст или просьба', '@шаг — что менять'] },
        { id: 'sync', x: 250, y: 62, w: 230, h: 96, icon: <Sparkles size={17} />, title: 'Промт подтянется', lines: ['сам, в фоне', 'минимальная правка текста'] },
        { id: 'build', x: 250, y: 340, w: 230, h: 96, icon: <Sparkles size={17} />, title: 'Граф рядом', lines: ['перестраивается сразу', 'по тексту промта'] },
        { id: 'ask', x: 250, y: 510, w: 230, h: 96, tone: 'warn', icon: <CircleHelp size={17} />, title: 'Вопрос под промтом', lines: ['если текст можно', 'понять по-разному'] },
        { id: 'changes', x: 590, y: 130, w: 262, h: 164, tone: 'accent', icon: <Eye size={17} />, title: 'Изменения черновика', lines: ['по «Посмотреть изменения»', 'дифф: Граф | Промт | OWS', 'граф — по умолчанию', 'продолжить · отменить'] },
        { id: 'pub', x: 590, y: 380, w: 262, h: 86, tone: 'ok', icon: <Rocket size={17} />, title: 'Публикация', lines: ['сохранить, запустить, эксперимент'] },
        { id: 'db', x: 590, y: 510, w: 262, h: 96, tone: 'dark', icon: <Database size={17} />, title: 'Версия в базе', lines: ['промт + OWS вместе', 'неизменяема после публикации'] },
      ]}
      links={[
        { from: 'graph', to: 'sync', fh: 'r50', th: 'l25' },
        { from: 'ows', to: 'sync', fh: 'r50', th: 'l75' },
        { from: 'prompt', to: 'build' },
        { from: 'sync', to: 'changes', fh: 'r50', th: 'l25' },
        { from: 'build', to: 'changes', fh: 'r50', th: 'l75', label: 'граф собран' },
        { from: 'build', to: 'ask', fh: 'b75', th: 't75', color: '#b45309' },
        { from: 'ask', to: 'build', fh: 't25', th: 'b25', dashed: true, label: 'ответ → в промт' },
        { from: 'changes', to: 'pub', fh: 'b50', th: 't50' },
        { from: 'pub', to: 'db', fh: 'b50', th: 't50', color: '#15803d' },
      ]}
    />
  )
}

export function ClarifyDiagram() {
  return (
    <Diagram
      width={900}
      height={380}
      title="Как появляется уточняющий вопрос"
      boxes={[
        { id: 'p', x: 0, y: 20, w: 140, h: 96, tone: 'accent', title: 'Промт', lines: ['с прошлыми ответами'] },
        { id: 'g', x: 172, y: 20, w: 160, h: 96, title: '5 генераций', lines: ['few-shot по сценариям из базы'] },
        { id: 'j', x: 364, y: 20, w: 170, h: 96, title: 'LLM-судья', lines: ['группы: 3 · 1 · 1'] },
        { id: 'd', x: 566, y: 20, w: 140, h: 96, tone: 'warn', title: 'Порог?', lines: ['главная группа ≥ 80 %'] },
        { id: 'ok', x: 746, y: 20, w: 106, h: 96, tone: 'ok', title: 'Граф', lines: ['и сверка'] },
        { id: 'x', x: 546, y: 230, w: 210, h: 112, title: 'Точки расхождения', lines: ['дифф групп: узел и поле'] },
        { id: 'q', x: 286, y: 230, w: 230, h: 112, tone: 'accent', title: 'Вопрос', lines: ['вариант на группу, большую — рекомендуем', 'плюс свободный ввод'] },
        { id: 'a', x: 26, y: 230, w: 230, h: 112, title: 'Ответ → пункт промта', lines: ['в «Уточнения» или «Допущения»'] },
      ]}
      links={[
        { from: 'p', to: 'g' },
        { from: 'g', to: 'j' },
        { from: 'j', to: 'd' },
        { from: 'd', to: 'ok', label: 'да', color: '#15803d' },
        { from: 'd', to: 'x', fh: 'b50', th: 't50', label: 'нет', color: '#b45309' },
        { from: 'x', to: 'q', fh: 'l50', th: 'r50' },
        { from: 'q', to: 'a', fh: 'l50', th: 'r50' },
        { from: 'a', to: 'p', fh: 't50', th: 'b50', label: 'генерируем снова', dashed: true },
      ]}
    />
  )
}

export function LifecycleDiagram() {
  const w = 190
  const h = 112
  return (
    <Diagram
      width={900}
      height={370}
      title="Жизненный цикл сценария и кто на каком этапе"
      boxes={[
        { id: 'idea', x: 0, y: 0, w, h, title: '1. Идея', lines: ['промт, шаблон, регламент'], who: ['zhenya'] },
        { id: 'clar', x: 224, y: 0, w, h, title: '2. Редактор', lines: ['промт и граф рядом, вопросы'], who: ['zhenya', 'gosha'] },
        { id: 'test', x: 448, y: 0, w, h, title: '3. Изменения', lines: ['дифф, проблемные места'], who: ['zhenya', 'gosha'] },
        { id: 'pub', x: 672, y: 0, w, h, tone: 'accent', title: '4. Публикация', lines: ['прогноз, ревью, эксперимент'], who: ['zhenya', 'gosha'] },
        { id: 'roll', x: 672, y: 230, w, h, title: '5. Выкатка', lines: ['эксперимент, 100 %, откат'], who: ['igor'] },
        { id: 'mig', x: 448, y: 230, w, h, title: '6. Миграция', lines: ['идущих запусков, с агентом'], who: ['igor'] },
        { id: 'ops', x: 224, y: 230, w, h, tone: 'ok', title: '7. Эксплуатация', lines: ['запуски, вмешательства'], who: ['olga'] },
        { id: 'chg', x: 0, y: 230, w, h, tone: 'warn', title: '8. Новая правка', lines: ['черновик в одном режиме'], who: ['zhenya', 'gosha'] },
      ]}
      links={[
        { from: 'idea', to: 'clar' },
        { from: 'clar', to: 'test' },
        { from: 'test', to: 'pub' },
        { from: 'pub', to: 'roll', fh: 'b50', th: 't50' },
        { from: 'roll', to: 'mig', fh: 'l50', th: 'r50' },
        { from: 'mig', to: 'ops', fh: 'l50', th: 'r50' },
        { from: 'ops', to: 'chg', fh: 'l50', th: 'r50' },
        { from: 'chg', to: 'clar', fh: 't75', th: 'b25', label: 'новая версия', dashed: true },
      ]}
    />
  )
}

export function MapDiagram() {
  const rows: [string, [string, string[]], [string, string[]]?, boolean?][] = [
    ['+ Новый сценарий', ['Промт и шаблоны', ['текст, регламент, схема']], ['Редактор сценария', ['промт и граф рядом, вопросы']]],
    ['Входящие', ['Ждут действия', ['шаги для людей, ревью, вопросы, агент']], ['Ведут в сценарий, миграцию', ['или карточку запуска']]],
    ['Сценарии', ['Список сценариев', ['фильтры, выкатка, запуски']], ['Страница сценария, без вкладок', ['Редактор: Промт | Граф | OWS', '«Посмотреть изменения»: дифф, проблемы', 'Публикация → Выкатка']]],
    ['Запуски', ['Список и сохранённые виды', ['поиск по бизнес-ключу']], ['Карточка запуска', ['где стоит, почему, вмешательства']]],
    ['Миграции', ['Список миграций', ['открываются переводом тега']], ['Миграция запусков', ['группы, решения, агент']]],
    ['Аудит', ['Кто, когда и почему', ['у каждого объекта — история']], undefined, true],
    ['Доступ', ['Роли и права', ['из /v1/me: что скрыть в UI']], undefined, true],
  ]
  const boxes: B[] = []
  const links: L[] = []
  let y = 0
  rows.forEach(([nav, main, next, service], i) => {
    const n = next ? next[1].length : 1
    const h = n > 2 ? 124 : n > 1 ? 104 : 72
    boxes.push({ id: `n${i}`, x: 0, y, w: 150, h, title: nav, tone: service ? 'ghost' : 'dark' })
    boxes.push({ id: `m${i}`, x: 196, y, w: 300, h, title: main[0], lines: main[1] })
    links.push({ from: `n${i}`, to: `m${i}` })
    if (next) {
      boxes.push({ id: `x${i}`, x: 542, y, w: 310, h, title: next[0], lines: next[1], tone: i === 2 ? 'accent' : undefined })
      links.push({ from: `m${i}`, to: `x${i}` })
    }
    y += h + 14
  })
  return <Diagram width={900} height={y + 40} title="Карта интерфейса: пункт меню → его экраны" boxes={boxes} links={links} />
}
