import { useState } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { Shell } from '../ui/Shell'
import { BeforeAfter, OwsView, act } from '../ui/bits'
import { ProcessGraph } from '../graph/ProcessGraph'
import { Morph, useFreeze } from '../graph/Morph'
import { RUNS_100, V100 } from '../graph/data'
import { asDiff } from '../graph/diff'
import { DIFF_110, frames } from '../graph/frames'
import { bounds, layoutOf } from '../graph/layout'
import { boxOf } from '../graph/view'

export type DiffMode = 'overlay' | 'side'

const CHANGES = [
  { c: 'var(--added)', s: '+', t: 'Новый шаг «Ждать стабильности релиза»', d: 'сигнал мониторинга, до 5 дн' },
  { c: 'var(--removed)', s: '−', t: 'Уходит шаг «Выдержать изменение»', d: 'пауза 3 дня · здесь стоят 10 запусков' },
  { c: 'var(--changed)', s: '~', t: 'Изменён шаг «Спросить владельца»', d: 'ждать ответа: без срока → 1 дн · 21 запуск' },
  { c: 'var(--added)', s: '+', t: 'Новый шаг «Напомнить в тикете»', d: 'не больше 3 раз' },
  { c: 'var(--renamed)', s: '↻', t: 'Переименован «Проверить здоровье»', d: 'в OWS healthCheck → probeHealth, поведение то же' },
]

const OWS_DIFF = `  - awaitMerge:
      timeout: {after: P30D}
  - soak:
      wait: P3D
  - awaitStable:
      listen:
        to: {one: {with: {type: monitoring.release.stable}}}
      timeout: {after: P5D}
  - probeHealth:`

function Toolbar({ mode, onlyChanges = false }: { mode: DiffMode; onlyChanges?: boolean }) {
  return (
    <div className="card" style={{ padding: '10px 14px', display: 'flex', alignItems: 'center', gap: 14 }}>
      <div>
        <div style={{ fontWeight: 650 }}>1.0.0 → 1.1.0</div>
        <div className="muted tiny" style={{ whiteSpace: 'nowrap' }}>5 изменений · 128 запусков на 1.0.0</div>
      </div>
      <div className="seg" style={{ marginLeft: 12 }}>
        <a href="#/diff" style={{ display: 'contents' }}>
          <span className={mode === 'overlay' ? 'on' : ''}>Наложение</span>
        </a>
        <a href="#/diff?mode=side" style={{ display: 'contents' }}>
          <span className={mode === 'side' ? 'on' : ''}>Рядом</span>
        </a>
      </div>
      <span className={`toggle ${onlyChanges ? 'on' : ''}`}>
        <i />
        только изменения
      </span>
      <span className="toggle on">
        <i />
        идущие запуски
      </span>
      <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
        <span className="muted small" style={{ whiteSpace: 'nowrap' }}>изменение 2 из 5</span>
        <button className="btn sm" onClick={act('Предыдущее изменение')}>
          <ChevronLeft size={14} />
        </button>
        <button className="btn sm" onClick={act('Следующее изменение')}>
          <ChevronRight size={14} />
        </button>
      </div>
    </div>
  )
}

function ChangeList({ selected = 1 }: { selected?: number }) {
  return (
    <div className="card" style={{ width: 300, flex: 'none' }}>
      <div className="card-h">Изменения</div>
      <div style={{ padding: 6 }}>
        {CHANGES.map((x, i) => (
          <div key={x.t} className={`chg-item ${i === selected ? 'on' : ''}`}>
            <span className="chg-ico" style={{ background: x.c }}>
              {x.s}
            </span>
            <div>
              <div style={{ fontWeight: 600 }}>{x.t}</div>
              <div className="muted tiny">{x.d}</div>
            </div>
          </div>
        ))}
      </div>
      <div className="sep" />
      <div className="card-b" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        <div className="section-label">Уходящий шаг: что с идущими</div>
        <div className="small">
          <b>10 запусков</b> стоят на «Выдержать изменение». Предлагаем перевести их на «Ждать стабильности релиза» — ожидание начнётся заново.
        </div>
        <div className="section-label">OWS</div>
        <OwsView text={OWS_DIFF} start={1} removed={[3, 4]} added={[5, 6, 7, 8]} />
        <div className="section-label">Промт, пункт 3</div>
        <div className="small">
          <span className="del">После мержа выдержи изменение 3 дня.</span> <span className="ins">После мержа дождись от мониторинга сигнала, что релиз стабилен, — до 5 дней.</span>
        </div>
      </div>
    </div>
  )
}

export function DiffScreen({ mode }: { mode: DiffMode }) {
  const freeze = useFreeze()
  const [target, setTarget] = useState<'before' | 'after'>('after')
  const f = frames.versions()
  return (
    <Shell section="scenarios" crumbs={['Сценарии', 'От тикета до выкатки', 'Публикация', 'Дифф']} who="igor" compact>
      <Toolbar mode={mode} />
      <div className="row" style={{ alignItems: 'flex-start' }}>
        <ChangeList />
        <div className="card grow" style={{ padding: 10 }} data-shot="graph">
          {mode === 'overlay' && (
            <>
              <BeforeAfter shown={freeze?.to ?? target} pick={setTarget} labels={['Было · 1.0.0', 'Стало · 1.1.0']} />
              <Morph frames={f} initial="before" target={target} delay={500} duration={1400} width={752} height={900} overlay={{ runs: RUNS_100 }} viewBox={bounds(f.before.layout, f.after.layout)} pad={30} />
            </>
          )}
          {mode === 'side' && (
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
              {[
                { g: asDiff(V100), l: layoutOf('v100'), v: '1.0.0 · trunk', ids: ['runAgent', 'awaitMerge', 'soak', 'healthCheck', 'approve', 'decide', 'comment'] },
                { g: { ...DIFF_110, nodes: DIFF_110.nodes.filter((n) => n.state !== 'removed'), edges: DIFF_110.edges.filter((e) => e.state !== 'removed') }, l: layoutOf('v110'), v: '1.1.0 · черновик', ids: ['runAgent', 'awaitMerge', 'awaitStable', 'probeHealth', 'approve', 'remind', 'decide', 'comment'] },
              ].map((x) => (
                <div key={x.v}>
                  <div className="small" style={{ fontWeight: 650, margin: '2px 4px 8px' }}>
                    {x.v}
                  </div>
                  <ProcessGraph graph={x.g} layout={x.l} width={366} height={900} viewBox={boxOf(x.l, x.ids)} maxZoom={0.62} overlay={x.v.startsWith('1.0') ? { runs: RUNS_100 } : {}} pad={22} />
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </Shell>
  )
}
