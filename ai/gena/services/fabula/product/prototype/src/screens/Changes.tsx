import { useState } from 'react'
import { ArrowLeft, ChevronDown, CircleAlert, Crosshair, Pencil, Rocket, ShieldCheck, Undo2, Users } from 'lucide-react'
import { Shell } from '../ui/Shell'
import { BeforeAfter, OwsView } from '../ui/bits'
import { Morph, useFreeze } from '../graph/Morph'
import { PROMPT_110 } from '../graph/data'
import { frames } from '../graph/frames'
import { layoutOf } from '../graph/layout'
import { boxOf, widen } from '../graph/view'

export type ChangesView = 'graph' | 'prompt' | 'ows'

const OWS_CHANGE = `  - approve:
      call: 'human.approve:1@gena'
      with: {ticket: '\${ $context.ticket }'}
      try:
        - ask:
            call: 'human.approve:1@gena'
            with: {ticket: '\${ $context.ticket }'}
            timeout: {after: P1D}
      catch:
        errors: {with: {type: timeout}}
        as: silence
  - remind:
      if: '\${ $context.silent and $context.reminders < 3 }'
      call: 'tracker.comment:1@tracker'
      then: approve
  - decide:`

const CHANGES = [
  {
    c: 'var(--changed)',
    s: '~',
    t: 'Изменён шаг «Спросить владельца»',
    d: 'ждать ответа: без срока → 1 день',
  },
  {
    c: 'var(--added)',
    s: '+',
    t: 'Новый шаг «Напомнить в тикете»',
    d: 'не больше 3 раз, потом снова ждать ответа',
  },
  {
    c: 'var(--added)',
    s: '+',
    t: 'Две новые связи',
    d: '«Спросить владельца» → «Напомнить» и обратно',
  },
]

function Header() {
  return (
    <div
      className="card"
      style={{
        padding: '14px 18px',
        display: 'flex',
        alignItems: 'center',
        gap: 12,
      }}
    >
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <h1 className="h1">Изменения черновика</h1>
          <span className="badge accent">1.1.0 · черновик</span>
        </div>
        <div
          className="muted small"
          style={{
            marginTop: 4,
            display: 'flex',
            gap: 6,
            alignItems: 'center',
            whiteSpace: 'nowrap',
          }}
        >
          сравнить с
          <span className="badge" style={{ gap: 4 }}>
            началом этой правки <ChevronDown size={12} />
          </span>
          · сохранено в 14:02
        </div>
      </div>
      <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
        <a className="btn" href="#/studio">
          <ArrowLeft size={15} />
          Продолжить правку
        </a>
        <a className="btn ghost" href="#/studio">
          <Undo2 size={15} />
          Отменить изменения
        </a>
        <a className="btn primary" href="#/versions">
          <Rocket size={15} />
          К публикации
        </a>
      </div>
    </div>
  )
}

function Switch({ view }: { view: ChangesView }) {
  const items: [ChangesView, string, string][] = [
    ['graph', 'Граф', '3 изменения'],
    ['prompt', 'Промт', '1 пункт'],
    ['ows', 'OWS', '+12 −2 строки'],
  ]
  return (
    <div
      className="card"
      style={{
        padding: '10px 14px',
        display: 'flex',
        alignItems: 'center',
        gap: 14,
      }}
    >
      <div className="seg">
        {items.map(([id, label, n]) => (
          <a key={id} href={`#/changes?view=${id}`} style={{ display: 'contents' }}>
            <span className={id === view ? 'on' : ''}>
              {label}{' '}
              <span className="tiny" style={{ opacity: 0.7 }}>
                {n}
              </span>
            </span>
          </a>
        ))}
      </div>
      <span className="small muted">{view === 'graph' ? 'Вы правили граф. Промт и OWS обновились сами.' : view === 'prompt' ? 'Промт обновился по правке графа. Слова можно поправить.' : 'Код, который получился из правки графа.'}</span>
      {view === 'graph' && (
        <span className="seg" style={{ marginLeft: 'auto' }}>
          <span className="on">Наложение</span>
          <span>Рядом</span>
        </span>
      )}
    </div>
  )
}

/** What the behaviour check found: it runs under the hood, the user sees only the places worth a look. */
function Findings({ show, onShow }: { show: boolean; onShow: () => void }) {
  return (
    <div className="card" style={{ width: 300, flex: 'none', borderColor: '#f3d19c' }}>
      <div className="card-h">
        <ShieldCheck size={16} color="var(--warn)" />
        Проверка поведения
        <span className="right sub">6 ситуаций</span>
      </div>
      <div className="card-b" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        <div className="small muted">Фабула прогнала черновик по ситуациям из промта. 5 проходят как ожидается, 1 место стоит посмотреть:</div>
        <div style={{ display: 'grid', gridTemplateColumns: '18px 1fr', gap: 8 }}>
          <CircleAlert size={16} color="var(--warn)" style={{ marginTop: 2 }} />
          <div className="small">
            <b>Владелец молчит 4 дня.</b> После 3 напоминаний процесс уйдёт в ветку «нет» и завершится без комментария в тикете — как будто владелец отказал.
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button className={`btn sm ${show ? 'primary' : ''}`} onClick={onShow}>
            <Crosshair size={13} />
            {show ? 'Показано на графе' : 'Показать на графе'}
          </button>
          <a className="btn sm" href="#/prompt">
            <Pencil size={13} />
            Поправить в промте
          </a>
        </div>
      </div>
    </div>
  )
}

function GraphView() {
  const freeze = useFreeze()
  const [target, setTarget] = useState<'before' | 'after'>('after')
  const [show, setShow] = useState(true)
  const f = frames.edit()
  const box = widen(boxOf(layoutOf('v110'), ['awaitStable', 'probeHealth', 'approve', 'remind', 'decide', 'comment']), 20, 40)
  return (
    <div className="row" style={{ alignItems: 'flex-start' }}>
      <div className="col" style={{ width: 300, flex: 'none' }}>
      <div className="card">
        <div className="card-h">Что изменилось</div>
        <div style={{ padding: 6 }}>
          {CHANGES.map((x, i) => (
            <div key={x.t} className={`chg-item ${i === 0 ? 'on' : ''}`}>
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
        <div className="card-b small" style={{ display: 'flex', gap: 8 }}>
          <Users size={16} color="var(--warn)" style={{ flex: 'none', marginTop: 2 }} />
          <div>
            <b>21 запуск</b> сейчас ждёт ответа на шаге «Спросить владельца». После публикации Фабула спросит, переводить ли их.
          </div>
        </div>
      </div>
      <Findings show={show} onShow={() => setShow((v) => !v)} />
      </div>
      <div className="card grow" style={{ padding: 10 }} data-shot="graph">
        <BeforeAfter shown={freeze?.to ?? target} pick={setTarget} labels={['До правки', 'После правки']} />
        <div className="canvas-fade">
          <Morph frames={f} initial="before" target={target} delay={400} duration={1200} width={752} height={700} overlay={{ runs: { approve: { n: 21, tone: 'warn' } }, warn: show ? ['remind', 'decide'] : [] }} viewBox={box} pad={24} />
        </div>
      </div>
    </div>
  )
}

function PromptDiff() {
  return (
    <div className="card">
      <div
        className="card-b"
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: 6,
          maxWidth: 820,
        }}
      >
        {PROMPT_110.map((c) => (
          <div key={c.n} className={`clause ${c.n === 5 ? 'hl' : ''}`} style={{ fontSize: 15, lineHeight: 1.6 }}>
            <span className="n">{c.n}</span>
            <div>
              {c.n === 5 ? (
                <>
                  Спроси у владельца тикета, выкатываем ли изменение. <span className="del">Ждать ответа без отдельного срока.</span> <span className="ins">Если владелец молчит сутки, напомни в тикете — не больше 3 раз.</span>
                </>
              ) : (
                c.text
              )}
            </div>
          </div>
        ))}
        <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
          <a className="btn" href="#/prompt">
            <Pencil size={14} />
            Поправить слова
          </a>
        </div>
      </div>
    </div>
  )
}

function OwsDiff() {
  return (
    <div className="card" style={{ maxWidth: 820 }}>
      <div className="card-h">
        <span className="mono">ticket-to-prod.yaml</span>
        <span className="sub">шаги «Спросить владельца» и «Напомнить в тикете»</span>
      </div>
      <div style={{ padding: '6px 4px', fontSize: 13 }}>
        <OwsView text={OWS_CHANGE} start={1} removed={[2, 3]} added={[4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]} />
      </div>
    </div>
  )
}

export function Changes({ view }: { view: ChangesView }) {
  return (
    <Shell section="scenarios" crumbs={['Сценарии', 'От тикета до выкатки', 'Изменения черновика']} who="zhenya" compact>
      <Header />
      <Switch view={view} />
      {view === 'graph' && <GraphView />}
      {view === 'prompt' && <PromptDiff />}
      {view === 'ows' && <OwsDiff />}
    </Shell>
  )
}
