import { useState } from 'react'
import { CheckCircle2, CircleHelp, MessageSquare, Sparkles, Workflow } from 'lucide-react'
import { Shell } from '../ui/Shell'
import { ModeBar, OwsView, StudioHeader } from '../ui/bits'
import { Morph, useFreeze } from '../graph/Morph'
import { frames } from '../graph/frames'
import { layoutOf } from '../graph/layout'
import { boxOf, widen } from '../graph/view'
import { PROMPT_100, PROMPT_NEW } from '../graph/data'
import { ADDED, FROM, OWS, TO } from './OwsMode'

type View = 'graph' | 'ows'

const TYPED = 'Если владелец молчит сутки, напомни в тикете — не больше 3 раз.'

/* The clarifying question of a new scenario: three readings of clause 5. */

const OPTIONS = [
  { id: 'A', t: 'Без срока — до общего дедлайна 60 дней', d: 'Ничего не добавляется: процесс ждёт, пока владелец не ответит.', rec: true, clause: 'Ждать ответа без отдельного срока.' },
  { id: 'B', t: '2 дня, потом напомнить в тикете и ждать снова', d: 'Появится шаг «Напомнить в тикете» и повторное ожидание ответа.', clause: 'Если владелец молчит 2 дня, напомни в тикете и жди снова.' },
  { id: 'C', t: '2 дня, потом считать отказом', d: 'Процесс завершится без выкатки и без комментария.', clause: 'Если владелец молчит 2 дня, считай это отказом.' },
]

/* The same fragment of OWS for each reading, as a diff against the recommended one. */
const ASK = [
  "  - approve:",
  "      call: 'human.approve:1@gena'",
  "      with: {ticket: '${ $context.ticket }', question: 'Ship?'}",
]
const WRAPPED = (after: string) => [
  "  - approve:",
  "      try:",
  "        - ask:",
  "            call: 'human.approve:1@gena'",
  "            with: {ticket: '${ $context.ticket }', question: 'Ship?'}",
  "            timeout: {after: P2D}",
  "      catch:",
  "        errors: {with: {type: timeout}}",
  ...after.split('\n'),
]
const TAIL = ["      export: {as: '${ $context + {approved: .approved} }'}", '  - decide:']
type OwsVariant = { text: string; added: number[]; removed: number[] }
const OWS_VARIANTS: Record<string, OwsVariant> = {
  A: { text: [...ASK, ...TAIL].join('\n'), added: [], removed: [] },
  B: {
    text: [
      ...ASK,
      ...WRAPPED("        do: [{remind: {call: 'tracker.comment:1@tracker', then: approve}}]").slice(1),
      ...TAIL,
    ].join('\n'),
    removed: [2, 3],
    added: [4, 5, 6, 7, 8, 9, 10, 11],
  },
  C: {
    text: [...ASK, ...WRAPPED("        do: [{refuse: {set: {approved: false}, then: end}}]").slice(1), ...TAIL].join('\n'),
    removed: [2, 3],
    added: [4, 5, 6, 7, 8, 9, 10, 11],
  },
}

/** The part of the graph that clause 5 maps to, with room for the reminder loop. */
const EDIT_BOX = widen(boxOf(layoutOf('v110'), ['probeHealth', 'approve', 'remind', 'decide', 'comment', 'end']), 20, 40)

function ViewSwitch({ view, set }: { view: View; set: (v: View) => void }) {
  return (
    <span className="seg">
      <span className={view === 'graph' ? 'on' : ''} onClick={() => set('graph')}>
        Граф
      </span>
      <span className={view === 'ows' ? 'on' : ''} onClick={() => set('ows')}>
        OWS
      </span>
    </span>
  )
}

/**
 * The editor in «Промт» mode: the prompt on the left, the graph it builds on the right.
 * A new scenario opens here straight from «Собрать»; a clarifying question, if any, sits
 * under the prompt, and the answer goes into the prompt as text.
 */
export function PromptMode({ fresh = false, view: initialView = 'graph' }: { fresh?: boolean; view?: View }) {
  const [view, setView] = useState<View>(initialView)
  return (
    <Shell section={fresh ? 'home' : 'scenarios'} crumbs={fresh ? ['Сценарии', 'Новый сценарий'] : ['Сценарии', 'От тикета до выкатки', 'Редактор']} who="zhenya" compact>
      <StudioHeader fresh={fresh} />
      {fresh ? <NewScenario view={view} setView={setView} /> : <Draft view={view} setView={setView} />}
    </Shell>
  )
}

/** A new scenario: the graph is built, one place of the prompt can be read in three ways. */
function NewScenario({ view, setView }: { view: View; setView: (v: View) => void }) {
  const [hover, setHover] = useState('B')
  const [picked, setPicked] = useState('A')
  const [answer, setAnswer] = useState<string | null>(null)
  const freeze = useFreeze()
  const shown = freeze?.to ?? (answer ?? hover)
  const n = OPTIONS.findIndex((o) => o.id === shown) + 1
  const chosen = OPTIONS.find((o) => o.id === answer)
  return (
    <>
      <ModeBar mode="prompt" hint={answer ? 'Граф собран по промту · вопросов нет. Дальше — «Посмотреть изменения».' : 'Граф собран по промту · осталось уточнить 1 место. Ответ попадёт в промт.'} />
      <div className="row" style={{ alignItems: 'flex-start' }}>
        <div className="col" style={{ width: 470, flex: 'none' }}>
          <div className="card">
            <div className="card-h">
              Промт <span className="sub">7 пунктов · Женя · только что</span>
            </div>
            <div className="card-b" style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              {PROMPT_NEW.map((c) => (
                <div key={c.n} className={`clause ${c.n === 5 ? 'hl' : ''}`} style={{ fontSize: 14.5, lineHeight: 1.55 }}>
                  <span className="n">{c.n}</span>
                  <div>
                    {c.n === 5 ? (
                      <>
                        <span className={chosen ? '' : 'ambig'}>{c.text}</span>
                        {chosen && (
                          <>
                            {' '}
                            <span className="ins">{chosen.clause}</span>
                          </>
                        )}
                      </>
                    ) : (
                      c.text
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
          {chosen ? (
            <div className="banner ok">
              <CheckCircle2 size={17} color="var(--ok)" />
              <div className="small">
                <b>Ответ записан в пункт 5.</b> Его можно поправить текстом, как любой пункт промта.
              </div>
            </div>
          ) : (
            <div className="card" style={{ borderColor: '#c7cbff', boxShadow: '0 0 0 4px rgba(79,70,229,.08)' }}>
              <div className="card-h">
                <CircleHelp size={17} color="var(--accent)" />
                Вопрос 1 из 1
                <span className="right sub">пункт 5 можно понять по-разному</span>
              </div>
              <div className="card-b" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                <div className="h2">Сколько ждать ответа владельца тикета?</div>
                <div className="muted small" style={{ marginTop: -4 }}>
                  Наведите на вариант — справа перестроится граф или OWS.
                </div>
                {OPTIONS.map((o) => (
                  <div key={o.id} className={`opt ${o.id === picked ? 'on' : ''} ${o.id === shown && o.id !== picked ? 'hover' : ''}`} onMouseEnter={() => setHover(o.id)} onClick={() => setPicked(o.id)}>
                    <span className="radio" />
                    <div>
                      <div className="ot">
                        {o.t} {o.rec && <span className="badge accent" style={{ marginLeft: 4 }}>рекомендуем</span>}
                      </div>
                      <div className="od">{o.d}</div>
                    </div>
                  </div>
                ))}
                <div className="input ph">Свой вариант…</div>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 2 }}>
                  <button className="btn primary" onClick={() => setAnswer(picked)}>
                    Ответить
                  </button>
                  <button className="btn ghost" onClick={() => setAnswer('A')}>
                    Пропустить: взять рекомендованный
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
        <div className="card grow" data-shot="graph">
          <div className="card-h">
            <Sparkles size={16} color="var(--accent)" />
            {answer ? (view === 'graph' ? 'Граф по промту' : 'OWS по промту') : `Как изменится ${view === 'graph' ? 'граф' : 'OWS'}`}
            <span className="sub" style={{ whiteSpace: 'nowrap' }}>
              {answer ? `вариант ${n} в пункте 5` : `вариант ${n} против рекомендованного`}
            </span>
            <span className="right">
              <ViewSwitch view={view} set={setView} />
            </span>
          </div>
          {view === 'graph' ? (
            <div style={{ padding: 10 }}>
              <Morph frames={frames.variants()} target={answer ?? hover} width={586} height={760} />
            </div>
          ) : (
            <div className="card-b" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div className="small muted">{shown === 'A' ? 'Рекомендованный вариант: шаг approve как есть, без срока.' : 'Красным — строки рекомендованного варианта, которые уходят; зелёным — что появится с этим ответом.'}</div>
              <div style={{ fontSize: 13 }}>
                <OwsView text={OWS_VARIANTS[shown].text} added={OWS_VARIANTS[shown].added} removed={OWS_VARIANTS[shown].removed} />
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  )
}

/** The 1.1.0 draft: Женя adds a reminder to clause 5, the graph beside it follows. */
function Draft({ view, setView }: { view: View; setView: (v: View) => void }) {
  return (
    <>
      <ModeBar mode="prompt" hint="Правите промт текстом или просьбой — граф справа перестраивается сразу." />
      <div className="row" style={{ alignItems: 'flex-start' }}>
        <div className="col" style={{ width: 470, flex: 'none' }}>
          <div className="card">
            <div className="card-h">
              Промт <span className="sub">7 пунктов · русский</span>
            </div>
            <div className="card-b" style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              {PROMPT_100.map((c) => (
                <div key={c.n} className={`clause ${c.n === 5 ? 'hl' : ''}`} style={{ fontSize: 14.5, lineHeight: 1.55 }}>
                  <span className="n">{c.n}</span>
                  <div>
                    {c.n === 5 ? (
                      <>
                        Спроси у владельца тикета, выкатываем ли изменение. <span className="del">Ждать ответа без отдельного срока.</span>{' '}
                        <span className="ins">{TYPED}</span>
                        <span style={{ display: 'inline-block', width: 2, height: 18, background: 'var(--accent)', verticalAlign: -3, marginLeft: 2 }} />
                      </>
                    ) : (
                      c.text
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
          <div className="card">
            <div className="card-h">
              <MessageSquare size={16} color="var(--accent)" />
              Или попросите словами
            </div>
            <div className="card-b" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div className="bubble me">
                <span className="mono" style={{ background: 'rgba(255,255,255,.18)', padding: '0 5px', borderRadius: 4 }}>
                  @Спросить владельца
                </span>{' '}
                если владелец молчит сутки — напомни в тикете, но не больше 3 раз
              </div>
              <div className="bubble ai">
                <div style={{ display: 'flex', gap: 6, alignItems: 'center', fontWeight: 620, marginBottom: 4 }}>
                  <Sparkles size={14} color="var(--accent)" />
                  Меняю пункт 5
                </div>
                Правка уже в тексте выше: шаг «Спросить владельца» получает срок ответа, появляется «Напомнить в тикете». Её можно дописать руками.
              </div>
              <div className="input ph">Что поменять? @ — шаг</div>
            </div>
          </div>
        </div>
        <div className="card grow" data-shot="graph">
          <div className="card-h">
            <Workflow size={16} color="var(--accent)" />
            {view === 'graph' ? 'Граф по промту' : 'OWS по промту'}
            <span className="sub" style={{ whiteSpace: 'nowrap' }}>
              участок пункта 5
            </span>
            <span className="right">
              <ViewSwitch view={view} set={setView} />
            </span>
          </div>
          {view === 'graph' ? (
            <div style={{ padding: 10 }}>
              <Morph frames={frames.edit()} initial="before" target="after" delay={500} duration={1200} width={586} height={700} viewBox={EDIT_BOX} pad={24} />
              <div className="tiny muted" style={{ padding: '2px 6px 4px' }}>
                Показан участок пункта под курсором. Весь граф — в режиме «Граф».
              </div>
            </div>
          ) : (
            <div className="card-b" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div className="small muted">Зелёным — строки, которые добавит правка пункта 5.</div>
              <div style={{ fontSize: 12.5 }}>
                <OwsView text={OWS} start={FROM} end={TO} added={ADDED} />
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  )
}
