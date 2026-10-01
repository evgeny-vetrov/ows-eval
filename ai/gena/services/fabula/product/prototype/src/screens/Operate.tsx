import { Bot, CalendarClock, Check, CheckCircle2, CornerDownRight, FastForward, Filter, MessageSquare, Pause, Pencil, Plus, RotateCw, Search, SkipForward, Sparkles, Square, X } from 'lucide-react'
import { Shell } from '../ui/Shell'
import { act } from '../ui/bits'
import { ProcessGraph } from '../graph/ProcessGraph'
import { V100 } from '../graph/data'
import { asDiff } from '../graph/diff'
import { DIFF_110 } from '../graph/frames'
import { layoutOf } from '../graph/layout'
import { boxOf } from '../graph/view'

const tone = (s: string) => (s === 'ждёт' ? 'info' : s === 'ждёт человека' ? 'warn' : s === 'упал' ? 'bad' : s === 'завершён' ? 'ok' : '')

/** Run, its labels (keys of other systems the scenario put on it), version, status, where it stands, how long. */
const ROWS: [string, string, string, string, string, string][] = [
  ['f-7c21', 'ticket QUEUE-4821 · pr PR-4821', '1.0.0 · A', 'ждёт', 'Ждать мержа PR', '7 дн'],
  ['f-7b90', 'ticket QUEUE-4817 · pr PR-4810', '1.0.0 · A', 'ждёт человека', 'Спросить владельца', '2 дн'],
  ['f-7a44', 'ticket QUEUE-4813 · pr PR-4806', '1.1.0 · B', 'ждёт', 'Ждать стабильности релиза', '1 дн'],
  ['f-79d2', 'ticket QUEUE-4809 · pr PR-4799', '1.0.0 · A', 'ждёт', 'Выдержать изменение — до 30 сен', '2 дн'],
  ['f-7910', 'ticket QUEUE-4802', '1.0.0 · A', 'упал', 'Агент делает PR — не уложился в 2 дня', '3 дн'],
  ['f-78c5', 'ticket QUEUE-4798 · pr PR-4788', '1.1.0 · B', 'завершён', 'Готово: комментарий «Выкачено»', '4 д 18 ч'],
  ['f-7801', 'ticket QUEUE-4794 · pr PR-4781', '1.0.0 · A', 'завершён', 'Готово: владелец отказал', '6 д 2 ч'],
]

export function Runs() {
  return (
    <Shell section="runs" crumbs={['Запуски']} who="olga">
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        {[
          ['Все', '1 204', false],
          ['Застряли', '12', true],
          ['Ждут человека', '21', false],
          ['В эксперименте', '46', false],
          ['Упали', '3', false],
        ].map(([t, n, on]) => (
          <span key={t as string} className={`badge ${on ? 'accent' : ''}`} style={{ fontSize: 13.5, padding: '3px 11px' }}>
            {t} <b>{n}</b>
          </span>
        ))}
        <span className="badge" style={{ fontSize: 13.5, padding: '3px 11px', background: 'transparent', border: '1px dashed var(--border-strong)' }}>
          <Plus size={13} />
          Сохранить вид
        </span>
      </div>
      <div className="card">
        <div className="card-b" style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <div className="input" style={{ width: 300 }}>
            <Search size={15} color="var(--faint)" />
            QUEUE-48
            <span className="faint tiny" style={{ marginLeft: 'auto' }}>
              по меткам и id
            </span>
          </div>
          <span className="badge">
            <Filter size={12} />
            сценарий: ticket-to-prod
          </span>
          <span className="badge">статус: ждёт, упал</span>
          <span className="badge">дольше 1 дня</span>
          <span className="muted small" style={{ marginLeft: 'auto' }}>
            12 из 1 204 · обновляется вживую
          </span>
        </div>
        <table className="t">
          <thead>
            <tr>
              <th>Запуск</th>
              <th>Метки</th>
              <th>Версия</th>
              <th>Статус</th>
              <th>Где сейчас</th>
              <th>Там уже</th>
            </tr>
          </thead>
          <tbody>
            {ROWS.map(([k, labels, v, st, where, age], i) => (
              <tr key={k} className={`nav ${i === 0 ? 'sel' : ''}`} onClick={() => (location.hash = '#/run')}>
                <td>
                  <b className="mono">{k}</b>
                </td>
                <td className="muted tiny">{labels}</td>
                <td className="small">{v}</td>
                <td>
                  <span className={`badge ${tone(st)}`}>{st}</span>
                </td>
                <td className="small">{where}</td>
                <td className="small">{age}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Shell>
  )
}

const JOURNAL: [string, string, string][] = [
  ['22 сен 10:02', 'ok', 'Старт: тикет QUEUE-4821, версия 1.0.0 (trunk, плечо A)'],
  ['22 сен 10:02', 'ok', 'Агент делает PR: агент coder взял тикет'],
  ['22 сен 11:47', 'ok', 'Агент делает PR: открыт PR-4821'],
  ['22 сен 11:47', 'now', 'Ждать мержа PR: ждём сигнал, что PR-4821 смержен'],
]

const ACTIONS: [typeof Check, string, string, boolean?][] = [
  [FastForward, 'Завершить ожидание', 'как будто PR смержили'],
  [CalendarClock, 'Продлить срок', 'дедлайн шага сейчас 22 окт'],
  [RotateCw, 'Повторить шаг', 'подписка создаётся заново'],
  [SkipForward, 'Пропустить шаг', 'сразу к «Выдержать изменение»'],
  [CornerDownRight, 'Перейти к шагу…', 'любой шаг этой версии'],
  [Pencil, 'Изменить контекст', 'с превью и причиной'],
  [Pause, 'Пауза', 'стимулы копятся в очереди'],
  [X, 'Отменить запуск', 'вызовы с эффектами не отменяются', true],
]

export function RunDetail() {
  const layout = layoutOf('v100')
  return (
    <Shell section="runs" crumbs={['Запуски', 'f-7c21']} who="olga" compact>
      <div className="card" style={{ padding: '14px 18px', display: 'flex', alignItems: 'center', gap: 12 }}>
        <div>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <h1 className="h1" style={{ fontSize: 21 }}>
              Запуск <span className="mono">f-7c21</span>
            </h1>
            <span className="badge info">ждёт 7 дн</span>
            <span className="badge">ticket-to-prod 1.0.0</span>
            <span className="badge">trunk · плечо A</span>
          </div>
          <div className="muted small">метки: ticket QUEUE-4821 · pr PR-4821 · запущен 22 сен 10:02</div>
        </div>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          <button className="btn" onClick={act('Объяснение — в блоке «Почему ждёт»')}>
            <Sparkles size={15} />
            Объяснить
          </button>
          <button className="btn primary" onClick={() => document.getElementById('interventions')?.scrollIntoView({ behavior: 'smooth' })}>
            Вмешаться
          </button>
        </div>
      </div>
      <div className="row" style={{ alignItems: 'flex-start' }}>
        <div className="card" style={{ width: 390, flex: 'none' }}>
          <div className="card-h">Где сейчас</div>
          <div style={{ padding: 10 }}>
            <ProcessGraph graph={asDiff(V100)} layout={layout} width={370} height={700} overlay={{ visited: ['start', 'runAgent'], current: 'awaitMerge' }} viewBox={boxOf(layout, ['start', 'runAgent', 'awaitMerge', 'soak', 'healthCheck'])} pad={20} />
          </div>
        </div>
        <div className="col grow">
          <div className="card">
            <div className="card-h">Почему ждёт</div>
            <div className="card-b" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div>
                Стоит на шаге «Ждать мержа PR»: ждёт сигнал из VCS, что PR-4821 смержен. Ждёт с 22 сен. Срок шага — <b>22 окт</b>, через 23 дня; потом шаг упадёт по таймауту.
              </div>
              <div className="banner accent">
                <Sparkles size={17} color="var(--accent)" />
                <div className="small">
                  Агент открыл PR-4821 22 сен, его пока не смержили. Это пункт 2 промта: «Жди, пока PR смержат, — до 30 дней». Похоже на обычное ожидание ревью, а не на сбой: ещё 11 запусков ждут дольше.
                </div>
              </div>
            </div>
          </div>
          <div className="card">
            <div className="card-h">
              Журнал
              <span className="right">
                <span className="seg">
                  <span className="on">Шаги</span>
                  <span>События</span>
                  <span>Контекст</span>
                  <span>JSON</span>
                </span>
              </span>
            </div>
            <div className="card-b timeline">
              {JOURNAL.map(([w, s, t], i) => (
                <div key={i} className={`tl ${s}`}>
                  <span className="when">{w}</span>
                  <span className="mark">
                    <i />
                    {i < JOURNAL.length - 1 && <b />}
                  </span>
                  <span>{t}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
        <div className="card" id="interventions" style={{ width: 292, flex: 'none' }}>
          <div className="card-h">Вмешаться</div>
          <div style={{ padding: 6 }}>
            {ACTIONS.map(([Icon, t, d, danger]) => (
              <div key={t} className="chg-item nav" style={{ gridTemplateColumns: '20px 1fr' }} onClick={act(`${t}: с причиной, в аудите и журнале`)}>
                <Icon size={16} color={danger ? 'var(--bad)' : 'var(--muted)'} style={{ marginTop: 2 }} />
                <div>
                  <div style={{ fontWeight: 600, color: danger ? 'var(--bad)' : undefined }}>{t}</div>
                  <div className="muted tiny">{d}</div>
                </div>
              </div>
            ))}
          </div>
          <div className="card-b tiny muted" style={{ borderTop: '1px solid var(--border)' }}>
            Каждое вмешательство — с причиной, в аудите и в журнале запуска.
          </div>
        </div>
      </div>
    </Shell>
  )
}

const COLS: [string, number, string][] = [
  ['На лету', 97, 'var(--ok)'],
  ['С перезапуском', 21, 'var(--changed)'],
  ['Заблокированы', 10, 'var(--removed)'],
  ['Заняты', 0, 'var(--faint)'],
  ['Не подходят', 0, 'var(--faint)'],
]

export function Campaign() {
  const union = layoutOf('diff110')
  return (
    <Shell section="migrations" crumbs={['Миграции', '1.0.0 → 1.1.0']} who="igor" compact>
      <div className="card" style={{ padding: '14px 18px', display: 'flex', alignItems: 'center', gap: 12 }}>
        <div>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <h1 className="h1">Миграция запусков 1.0.0 → 1.1.0</h1>
            <span className="badge warn">ждёт решений</span>
          </div>
          <div className="muted small">открыта переводом trunk на 1.1.0 · Игорь, сегодня 15:10 · 128 идущих запусков, без решения никто не переедет</div>
        </div>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          <button className="btn" onClick={act('Анализ запусков обновлён')}>
            Анализ заново
          </button>
          <button className="btn danger" onClick={act('Миграция отменена, запуски остались на 1.0.0')}>
            Отменить миграцию
          </button>
        </div>
      </div>
      <div className="col-board">
        {COLS.map(([t, n, c]) => (
          <div key={t} className="vcol" style={{ borderTop: `3px solid ${c}` }}>
            <div className="vn">{n}</div>
            <div className="vl">{t}</div>
          </div>
        ))}
      </div>
      <div className="row" style={{ alignItems: 'flex-start' }}>
        <div className="col grow">
          {[
            {
              n: 97,
              c: 'var(--ok)',
              t: 'Ещё не дошли до изменений',
              d: '«Агент делает PR» (14) и «Ждать мержа PR» (83): до этих шагов версии не отличаются.',
              dec: 'Перевести на лету',
              ok: true,
            },
            {
              n: 21,
              c: 'var(--changed)',
              t: 'Ждут ответа владельца — шаг «Спросить владельца» изменился',
              d: 'Появился срок ответа 1 день. Шаг начнётся заново: владелец получит вопрос повторно.',
              dec: 'Спросить владельца заново',
              ok: true,
            },
            {
              n: 10,
              c: 'var(--removed)',
              t: 'Стоят на шаге «Выдержать изменение», которого в 1.1.0 нет',
              d: 'Подсказка из диффа: перевести на «Ждать стабильности релиза», ожидание начнётся заново.',
              dec: 'Решает агент миграций',
              ok: false,
            },
          ].map((x) => (
            <div key={x.t} className="card" style={{ borderLeft: `4px solid ${x.c}` }}>
              <div className="card-b" style={{ display: 'grid', gridTemplateColumns: '54px 1fr 250px', gap: 14, alignItems: 'center' }}>
                <div style={{ fontSize: 24, fontWeight: 700 }}>{x.n}</div>
                <div>
                  <div style={{ fontWeight: 620 }}>{x.t}</div>
                  <div className="muted small">{x.d}</div>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6, alignItems: 'stretch' }}>
                  <div className="input small" style={{ justifyContent: 'space-between' }}>
                    {x.ok ? <Check size={14} color="var(--ok)" /> : <Bot size={14} color="var(--accent)" />}
                    <span style={{ flex: 1 }}>{x.dec}</span>▾
                  </div>
                  {!x.ok && (
                    <a className="btn sm primary" href="#/inbox" style={{ justifyContent: 'center' }}>
                      Предложение агента готово
                    </a>
                  )}
                </div>
              </div>
            </div>
          ))}
          <div className="card">
            <div className="card-b" style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <div>
                <b>Одобрено 118 из 128</b>
                <div className="muted tiny">10 ждут решения по предложению агента</div>
              </div>
              <div className="input ph" style={{ marginLeft: 'auto', width: 300 }}>
                Введите «trunk → 1.1.0» для подтверждения
              </div>
              <button className="btn primary" disabled>
                Применить
              </button>
            </div>
          </div>
        </div>
        <div className="col" style={{ width: 360, flex: 'none' }}>
          <div className="card">
            <div className="card-h">
              Откуда → куда <span className="sub">«Выдержать изменение»</span>
            </div>
            <div style={{ padding: 10 }}>
              <ProcessGraph graph={DIFF_110} layout={union} width={340} height={380} overlay={{ runs: { soak: { n: 10, tone: 'bad' } }, focus: ['soak', 'awaitStable'] }} viewBox={boxOf(union, ['awaitMerge', 'awaitStable', 'soak', 'probeHealth'])} pad={22} tags />
            </div>
          </div>
          <div className="card">
            <div className="card-h">Как решаем</div>
            <div className="card-b small" style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <div>Агент миграций предлагает, человек одобряет.</div>
              <div className="muted">Агент не отменяет вызовы с внешними действиями и заново запускает только шаги без них.</div>
            </div>
          </div>
        </div>
      </div>
    </Shell>
  )
}

/** Everything that waits for a person: steps of processes that ask people, and Fabula's own tasks. */
const INBOX: [string, string, string, string, boolean?][] = [
  ['accent', 'Предложение агента', 'Миграция 1.0.0 → 1.1.0: 10 запусков перевести с «Выдержать изменение» на «Ждать стабильности релиза»', '2 мин', true],
  ['warn', 'Решение по миграции', 'Миграция 1.0.0 → 1.1.0: 2 группы запусков ждут решения', '15 мин'],
  ['warn', 'Спросить владельца', 'От тикета до выкатки · тикет QUEUE-4817: выкатываем PR-4810?', '2 дн'],
  ['bad', 'Шаг падает', 'Онбординг сотрудника: «Создать аккаунт» отвечает ошибкой 503 в 3 запусках', '3 ч'],
  ['info', 'Запуск висит 7 дней', 'От тикета до выкатки · тикет QUEUE-4821: «Ждать мержа PR» дольше, чем у 90 % запусков', '5 ч'],
]

/** Where an inbox record leads: the screen where its action lives. */
const TARGET: Record<string, string> = {
  'Решение по миграции': '#/campaign',
  'Спросить владельца': '#/run',
  'Шаг падает': '#/runs',
  'Запуск висит 7 дней': '#/run',
}

const FILTERS: [string, number, boolean][] = [
  ['Ждут меня', 5, true],
  ['Все', 12, false],
]
/** Built from the names of the steps that ask people, across scenarios. */
const BY_STEP: [string, number][] = [
  ['Спросить владельца', 3],
  ['Согласовать у руководителя', 2],
  ['Выдать ноутбук', 1],
]

export function Inbox() {
  return (
    <Shell section="inbox" crumbs={['Входящие']} who="igor">
      <div className="row" style={{ alignItems: 'flex-start' }}>
        <div className="card" style={{ width: 350, flex: 'none' }}>
          <div className="card-h">Ждут действия</div>
          <div className="filters">
            {FILTERS.map(([t, n, on]) => (
              <span key={t} className={`badge ${on ? 'accent' : ''}`}>
                {t} <b>{n}</b>
              </span>
            ))}
            <span className="tiny muted" style={{ width: '100%', margin: '4px 0 0 2px' }}>
              по шагам
            </span>
            {BY_STEP.map(([t, n]) => (
              <span key={t} className="badge">
                {t} <b>{n}</b>
              </span>
            ))}
          </div>
          <div style={{ padding: 6 }}>
            {INBOX.map(([c, t, d, when, on]) => (
              <div key={t + d} className={`chg-item nav ${on ? 'on' : ''}`} style={{ gridTemplateColumns: '10px 1fr' }} onClick={() => TARGET[t] && (location.hash = TARGET[t])}>
                <i className="dot" style={{ marginTop: 7, background: `var(--${c})` }} />
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                    <b style={{ fontSize: 14 }}>{t}</b>
                    <span className="faint tiny" style={{ whiteSpace: 'nowrap' }}>
                      {when}
                    </span>
                  </div>
                  <div className="muted tiny">{d}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
        <div className="card grow">
          <div className="card-h">
            <Bot size={17} color="var(--accent)" />
            Предложение агента миграций
            <span className="right sub">миграция 1.0.0 → 1.1.0</span>
          </div>
          <div className="card-b" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div>
              <div className="section-label">Группа запусков</div>
              <div style={{ fontWeight: 620, marginTop: 3 }}>10 запусков стоят на шаге «Выдержать изменение», которого в 1.1.0 нет</div>
            </div>
            <table className="t" style={{ border: '1px solid var(--border)', borderRadius: 8 }}>
              <thead>
                <tr>
                  <th>Сейчас, 1.0.0</th>
                  <th>Станет, 1.1.0</th>
                  <th>Что произойдёт</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td className="small">Выдержать изменение · пауза 3 дня</td>
                  <td className="small">Ждать стабильности релиза · до 5 дней</td>
                  <td className="small">ожидание начнётся заново</td>
                </tr>
                <tr>
                  <td className="muted small" colSpan={2}>
                    данные запуска
                  </td>
                  <td className="small">не меняются</td>
                </tr>
              </tbody>
            </table>
            <div className="banner ok">
              <CheckCircle2 size={18} color="var(--ok)" />
              <div className="small">
                <b>Пробный перевод: подходит всем 10.</b> Ничего не отменится, внешних действий нет, в рамках правил миграции.
              </div>
            </div>
            <div>
              <div className="section-label">Почему так</div>
              <div className="small" style={{ marginTop: 4 }}>
                Оба шага стоят в одном месте процесса — между мержем и проверкой здоровья. Выдержка ничего не делает снаружи, поэтому начать ожидание заново безопасно. Так же подсказывает дифф версий.
              </div>
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
              <a className="btn primary" href="#/campaign">
                <Check size={15} />
                Принять
              </a>
              <button className="btn" onClick={act('Перевод открыт на правку')}>
                <Pencil size={14} />
                Править
              </button>
              <button className="btn" onClick={act('Ответ отправлен агенту')}>
                <MessageSquare size={14} />
                Ответить
              </button>
              <a className="btn danger" href="#/campaign">
                <X size={14} />
                Отклонить
              </a>
            </div>
            <label className="small muted" style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <Square size={15} />
              Принимать такие предложения в этой миграции без меня
            </label>
          </div>
        </div>
      </div>
    </Shell>
  )
}
