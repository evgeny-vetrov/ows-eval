import { useState } from 'react'
import { ArrowLeft, CheckCircle2, FlaskConical, KeyRound, Pause, Pencil, Rocket, RotateCcw, Save, ShieldCheck, TrendingUp, X } from 'lucide-react'
import { Shell } from '../ui/Shell'
import { StudioHeader, Stack, act } from '../ui/bits'
import { ProcessGraph } from '../graph/ProcessGraph'
import { DIFF_110 } from '../graph/frames'
import { layoutOf } from '../graph/layout'
import { boxOf } from '../graph/view'

const GATES: [string, string][] = [
  ['Диагностика OWS', '0 ошибок, 0 предупреждений'],
  ['Шаги подключены', 'агент, MCP и HTTP отвечают, версии закреплены'],
  ['Проверка поведения', '6 ситуаций; 1 место просмотрено в изменениях'],
  ['Ревью', 'Гоша одобрил сегодня в 13:20'],
]

/** Limit of an experiment: more than a half is no longer an experiment, it is a move of the tag. */
const EXP_MAX = 50

function ExperimentDialog({ close, start }: { close: () => void; start: () => void }) {
  const [pct, setPct] = useState(10)
  const set = (v: number) => setPct(Math.max(1, Math.min(EXP_MAX, Math.round(v) || 1)))
  return (
    <div className="modal-back" onClick={close}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="card-h">
          <FlaskConical size={17} color="var(--accent)" />
          Эксперимент с 1.1.0
          <span className="right">
            <button className="btn sm ghost" onClick={close} aria-label="Закрыть">
              <X size={15} />
            </button>
          </span>
        </div>
        <div className="card-b" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div className="small">Какая доля новых стартов пойдёт на 1.1.0? Остальные — на 1.0.0 по тегу trunk. Идущие запуски не трогаем.</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <input type="range" min={1} max={EXP_MAX} value={pct} onChange={(e) => set(Number(e.target.value))} style={{ flex: 1 }} />
            <div className="input" style={{ width: 88, padding: '6px 10px' }}>
              <input type="number" min={1} max={EXP_MAX} value={pct} onChange={(e) => set(Number(e.target.value))} style={{ width: 44, border: 0, outline: 0, font: 'inherit', background: 'transparent' }} />%
            </div>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between' }} className="tiny muted">
            <span>1 %</span>
            <span>не больше {EXP_MAX} % — дальше это уже запуск на 100 %</span>
            <span>{EXP_MAX} %</span>
          </div>
          <div className="banner accent">
            <ShieldCheck size={17} color="var(--accent)" />
            <div className="small">Страховка: эксперимент встанет на паузу, если упавших больше 2 % или медиана выросла на 20 %.</div>
          </div>
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
            <button className="btn ghost" onClick={close}>
              Отмена
            </button>
            <button className="btn primary" onClick={start}>
              <FlaskConical size={15} />
              Запустить на {pct} %
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

export function Versions({ experiment = false }: { experiment?: boolean }) {
  const [open, setOpen] = useState(experiment)
  return (
    <Shell section="scenarios" crumbs={['Сценарии', 'От тикета до выкатки', 'Публикация']} who="igor" compact>
      <StudioHeader
        right={
          <a className="btn" href="#/changes">
            <ArrowLeft size={15} />
            К изменениям
          </a>
        }
      />
      <div className="row" style={{ alignItems: 'flex-start' }}>
        <div className="card" style={{ width: 270, flex: 'none' }}>
          <div className="card-h">Версии</div>
          <div style={{ padding: 6 }}>
            {[
              ['1.1.0', 'черновик · Женя, Гоша · сегодня', 'accent', true],
              ['1.0.0', 'trunk · 12 сен · 128 запусков', 'ok', false],
              ['0.9.0', 'устарела · новые старты закрыты', '', false],
            ].map(([v, d, tone, on]) => (
              <div key={v as string} className={`chg-item ${on ? 'on' : ''}`} style={{ gridTemplateColumns: '1fr' }}>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <b>{v}</b>
                  {tone && <i className="dot" style={{ background: `var(--${tone})` }} />}
                </div>
                <div className="muted tiny">{d}</div>
              </div>
            ))}
          </div>
        </div>
        <div className="col grow">
          <div className="card">
            <div className="card-h">
              Что будет с идущими запусками при переходе 1.0.0 → 1.1.0
              <span className="right sub">прогноз по каждому из 128</span>
            </div>
            <div className="card-b" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <Stack
                parts={[
                  { v: 97, color: 'var(--ok)', label: 'перейдут на лету' },
                  { v: 21, color: 'var(--changed)', label: 'с перезапуском шага «Спросить владельца»' },
                  { v: 10, color: 'var(--removed)', label: 'заблокированы: стоят на уходящем «Выдержать изменение»' },
                ]}
              />
              <div className="small muted">Сами никто не переедет: «Сохранить и запустить» откроет миграцию запусков с этим прогнозом.</div>
            </div>
          </div>
          <div className="card">
            <div className="card-h">
              Дифф
              <span className="right">
                <a className="btn sm" href="#/diff">
                  Открыть дифф-вьюер
                </a>
              </span>
            </div>
            <div style={{ padding: 10 }}>
              <ProcessGraph graph={DIFF_110} layout={layoutOf('diff110')} width={466} height={560} viewBox={boxOf(layoutOf('diff110'), ['awaitMerge', 'awaitStable', 'soak', 'probeHealth', 'approve', 'remind'])} pad={24} />
            </div>
          </div>
        </div>
        <div className="card" style={{ width: 300, flex: 'none' }}>
          <div className="card-h">
            <ShieldCheck size={16} />
            Публикация 1.1.0
          </div>
          <div className="card-b" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {GATES.map(([t, d]) => (
              <div key={t} style={{ display: 'grid', gridTemplateColumns: '18px 1fr', gap: 8 }}>
                <CheckCircle2 size={17} color="var(--ok)" style={{ marginTop: 2 }} />
                <div>
                  <div style={{ fontWeight: 600, fontSize: 14 }}>{t}</div>
                  <div className="muted tiny">{d}</div>
                </div>
              </div>
            ))}
            <div className="sep" />
            <a className="btn primary" href="#/campaign" style={{ justifyContent: 'center' }}>
              <Rocket size={15} />
              Сохранить и запустить
            </a>
            <button className="btn" onClick={() => setOpen(true)} style={{ justifyContent: 'center' }}>
              <FlaskConical size={15} />
              Запустить эксперимент
            </button>
            <button className="btn" onClick={act('1.1.0 сохранена · trunk остался на 1.0.0')} style={{ justifyContent: 'center' }}>
              <Save size={15} />
              Сохранить
            </button>
            <div className="tiny muted">
              «Сохранить и запустить» — все новые старты на 1.1.0 и миграция идущих. Эксперимент — часть новых стартов, до {EXP_MAX} %. «Сохранить» — только версия, trunk остаётся на 1.0.0.
            </div>
          </div>
        </div>
      </div>
      {open && <ExperimentDialog close={() => setOpen(false)} start={() => (location.hash = '#/rollout')} />}
    </Shell>
  )
}

const HISTORY: [string, string, string, string][] = [
  ['сегодня 14:40', 'Игорь', 'эксперимент: 1.1.0 на 10 % новых стартов', 'rev 9'],
  ['12 сен 11:02', 'Игорь', 'trunk → 1.0.0 · миграция закрыта: 64 переведено, 3 остались', 'rev 8'],
  ['9 сен 18:15', 'Игорь', 'откат trunk на 0.9.0 · упавших 6 %', 'rev 7'],
  ['9 сен 10:00', 'Женя', 'trunk → 1.0.0-rc.2', 'rev 6'],
]

export function Rollout() {
  return (
    <Shell section="scenarios" crumbs={['Сценарии', 'От тикета до выкатки', 'Выкатка']} who="igor" compact>
      <StudioHeader
        version="1.1.0 · опубликована"
        right={
          <a className="btn" href="#/studio">
            <Pencil size={15} />
            Новый черновик
          </a>
        }
      />
      <div className="card">
        <div className="card-h">
          Новые старты по тегу <span className="mono">trunk</span>
          <span className="sub">эксперимент: плечи A/B, ключ закреплён за плечом</span>
          <span className="right">
            <button className="btn sm" onClick={act('Эксперимент расширен до 25 %')}>
              <TrendingUp size={13} />
              До 25 %
            </button>
            <button className="btn sm" onClick={act('Эксперимент на паузе')}>
              <Pause size={13} />
              Пауза
            </button>
            <button className="btn sm danger" onClick={act('trunk откатан на 1.0.0')}>
              <RotateCcw size={13} />
              Откатить
            </button>
            <a className="btn sm primary" href="#/campaign">
              <Rocket size={13} />
              Запустить на 100 %
            </a>
          </span>
        </div>
        <div className="card-b" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div style={{ display: 'flex', height: 34, borderRadius: 8, overflow: 'hidden', fontWeight: 650, fontSize: 14 }}>
            <div style={{ width: '90%', background: '#e8ecf3', display: 'flex', alignItems: 'center', padding: '0 12px' }}>A · 1.0.0 · 90 %</div>
            <div style={{ width: '10%', background: 'var(--accent)', color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>B · 10 %</div>
          </div>
          <table className="t">
            <thead>
              <tr>
                <th>Плечо</th>
                <th>Стартов, 7 дн</th>
                <th>Завершено</th>
                <th>Упало</th>
                <th>Медиана до конца</th>
                <th>Ждут человека</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>
                  <b>A · 1.0.0</b>
                </td>
                <td>412</td>
                <td>380</td>
                <td>3 · 0,7 %</td>
                <td>5 д 4 ч</td>
                <td>18</td>
              </tr>
              <tr className="sel">
                <td>
                  <b>B · 1.1.0</b>
                </td>
                <td>46</td>
                <td>38</td>
                <td>0</td>
                <td>4 д 20 ч</td>
                <td>2</td>
              </tr>
            </tbody>
          </table>
          <div className="banner accent">
            <ShieldCheck size={18} color="var(--accent)" />
            <div className="small">
              <b>Страховка эксперимента:</b> пауза и вопрос Игорю во Входящих, если упавших больше 2 % или медиана выросла на 20 %.
            </div>
          </div>
        </div>
      </div>
      <div className="row" style={{ alignItems: 'flex-start' }}>
        <div className="card grow">
          <div className="card-h">История тега</div>
          <table className="t">
            <tbody>
              {HISTORY.map(([when, who, what, rev]) => (
                <tr key={rev}>
                  <td className="muted small" style={{ whiteSpace: 'nowrap' }}>
                    {when}
                  </td>
                  <td>{who}</td>
                  <td>{what}</td>
                  <td>
                    <span className="badge">{rev}</span>
                  </td>
                  <td>
                    <button className="btn sm ghost" onClick={act(`Тег вернули к ${rev}`)}>
                      вернуть
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="card" style={{ width: 330, flex: 'none' }}>
          <div className="card-h">
            <KeyRound size={15} />
            Какую версию получит ключ
          </div>
          <div className="card-b" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div className="input">QUEUE-4821</div>
            <div className="small">
              → <b>1.0.0</b>, плечо A. Ключ закреплён за плечом: повторный старт попадёт туда же.
            </div>
          </div>
        </div>
      </div>
    </Shell>
  )
}
