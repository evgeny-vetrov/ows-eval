import { ChevronRight, CircleAlert, Wand2, Workflow } from 'lucide-react'
import { Shell } from '../ui/Shell'
import { ModeBar, OwsView, StudioHeader, act } from '../ui/bits'
import { TOOL } from '../graph/ProcessGraph'

/* ticket-to-prod 1.1.0 as Гоша edits it: the engine's pilot plus the reminder. */
export const OWS = `document:
  dsl: '1.0.3'
  namespace: gena
  name: ticket-to-prod
  version: '1.1.0'
  title: From a ticket to a deployed change
input:
  schema:
    document:
      type: object
      required: [ticket]
      properties:
        ticket: {type: string}
timeout: {after: P60D}
do:
  - runAgent:
      call: 'agent.run:1@gena'
      with: {ticket: '\${ .ticket }'}
      timeout: {after: P2D}
      export: {as: '\${ {ticket: $input.ticket, pr: .pr_id, reminders: 0} }'}
  - awaitMerge:
      listen:
        to:
          one:
            with: {type: vcs.pr.merged}
            correlate:
              pr: {from: '\${ .data.pr_id }', expect: '\${ $context.pr }'}
      timeout: {after: P30D}
  - awaitStable:
      listen:
        to: {one: {with: {type: monitoring.release.stable}}}
      timeout: {after: P5D}
  - probeHealth:
      try:
        - probe:
            call: 'http.healthcheck:1@platform'
            with: {url: 'https://service.example/health'}
      catch:
        errors: {with: {type: communication}}
        retry: {delay: PT1M, backoff: {exponential: {}}, limit: {attempt: {count: 3}}}
  - approve:
      try:
        - ask:
            call: 'human.approve:1@gena'
            with:
              ticket: '\${ $context.ticket }'
              question: '\${ "Ship " + $context.pr + "?" }'
            timeout: {after: P1D}
      catch:
        errors: {with: {type: timeout}}
        as: silence
      export: {as: '\${ $context + {approved: .approved} }'}
  - remind:
      if: '\${ $silence != null and $context.reminders < 3 }'
      call: 'tracker.comment:1@tracker'
      with: {ticket: '\${ $context.ticket }', text: 'Ждём решения по выкатке'}
      export: {as: '\${ $context + {reminders: $context.reminders + 1} }'}
      then: approve
  - decide:
      switch:
        - approved: {when: '\${ $context.approved }', then: comment}
        - rejected: {then: end}
  - comment:
      call: 'tracker.comment:1@tracker'
      with:
        ticket: '\${ $context.ticket }'
        text: '\${ "Deployed " + $context.pr + " after a health check" }'
output:
  as: '\${ {ticket: $context.ticket, pr: $context.pr, approved: $context.approved} }'`

export const FROM = 41
export const TO = 58
/** Lines the reminder edit adds to approve and remind. */
export const ADDED = [42, 43, 49, 50, 51, 53, 54, 55, 56, 57, 58]
const TOTAL = OWS.split('\n').length

function Fold({ lines, what }: { lines: string; what: string }) {
  return (
    <div className="small muted" style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '4px 12px', background: '#f4f5f8', fontFamily: 'var(--mono)', fontSize: 12 }}>
      <ChevronRight size={13} />
      {what}
      <span style={{ marginLeft: 'auto' }}>строки {lines}</span>
    </div>
  )
}

/** Engineers edit the code itself; the graph and the prompt follow. */
export function OwsMode() {
  const human = TOOL.human
  return (
    <Shell section="scenarios" crumbs={['Сценарии', 'От тикета до выкатки', 'Редактор']} who="gosha" compact>
      <StudioHeader />
      <ModeBar mode="ows" hint="Правите OWS. Граф и промт подтянутся сами — их изменения видны в «Посмотреть изменения»." />
      <div className="row" style={{ alignItems: 'flex-start' }}>
        <div className="card grow">
          <div className="card-h">
            <span className="mono">ticket-to-prod.yaml</span>
            <span className="sub">OWS 1.0.3 · {TOTAL} строк</span>
            <span className="right sub">стр. 48, столбец 32</span>
          </div>
          <Fold lines={`1–${FROM - 1}`} what="document, input, runAgent, awaitMerge, awaitStable, probeHealth" />
          <div style={{ padding: '2px 4px', fontSize: 13 }}>
            <OwsView text={OWS} start={FROM} end={TO} highlight={[48]} added={ADDED} />
          </div>
          <Fold lines={`${TO + 1}–${TOTAL}`} what="decide, comment, output" />
        </div>
        <div className="col" style={{ width: 330, flex: 'none' }}>
          <div className="card">
            <div className="card-h">
              Диагностика
              <span className="right">
                <span className="badge warn">1 предупреждение</span>
              </span>
            </div>
            <div className="card-b" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div style={{ display: 'grid', gridTemplateColumns: '18px 1fr', gap: 8 }}>
                <CircleAlert size={16} color="var(--warn)" style={{ marginTop: 2 }} />
                <div className="small">
                  <b>стр. 54, столбец 13.</b> <span className="mono">$silence</span> виден только внутри <span className="mono">catch</span> шага approve — в шаге remind его нет, и напоминание не сработает.
                </div>
              </div>
              <button className="btn sm" style={{ alignSelf: 'flex-start' }} onClick={act('Молчание запомнено в контексте, предупреждение ушло')}>
                <Wand2 size={13} />
                Запомнить молчание в контексте
              </button>
              <div className="tiny muted">Проверяет валидатор движка: профиль OWS 1.0.3, контракты шагов, события.</div>
            </div>
          </div>
          <div className="card">
            <div className="card-h">
              <human.Icon size={16} color={human.color} />
              Шаг под курсором
              <span className="right sub">approve · ask</span>
            </div>
            <div className="card-b small" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <div className="mono">human.approve:1@gena</div>
              <div className="muted">Спросить человека: вопрос приходит в трекер и во Входящие.</div>
              <div>
                вход: <span className="mono">ticket</span>, <span className="mono">question</span> — строки, обязательны
              </div>
              <div>
                выход: <span className="mono">approved</span> — да или нет
              </div>
              <div>
                эффекты: <span className="mono">asks:human</span> · можно отменить
              </div>
              <a className="btn sm ghost" href="#/studio" style={{ alignSelf: 'flex-start', paddingLeft: 0 }}>
                <Workflow size={13} />
                Показать на графе
              </a>
            </div>
          </div>
        </div>
      </div>
    </Shell>
  )
}
