import { Plus, Search } from 'lucide-react'
import { Shell } from '../ui/Shell'

type Row = { t: string; id: string; owners: string; version: string; draft?: string; rollout?: string; runs: string; state: [string, string]; href?: string }

const ROWS: Row[] = [
  { t: 'От тикета до выкатки', id: 'gena/ticket-to-prod', owners: 'Женя, Гоша', version: 'trunk → 1.0.0', draft: '1.1.0 · Женя', rollout: 'эксперимент 1.1.0 · 10 %', runs: '128 идут', state: ['ok', 'работает'], href: '#/studio' },
  { t: 'Согласование доступа', id: 'it/access-request', owners: 'Женя', version: 'trunk → 2.3.0', draft: '2.4.0 · Женя', runs: '41 идёт', state: ['warn', 'есть вопрос'] },
  { t: 'Онбординг сотрудника', id: 'hr/onboarding', owners: 'Гоша', version: 'trunk → 2.4.0', runs: '9 идут', state: ['bad', '3 упали'] },
  { t: 'Возврат заказа', id: 'shop/refund', owners: 'Ольга, Игорь', version: 'trunk → 1.2.0', rollout: 'эксперимент 1.3.0 · 25 %', runs: '312 идут', state: ['ok', 'работает'] },
]

const FILTERS: [string, number, boolean][] = [
  ['Все', 4, true],
  ['Мои', 2, false],
  ['С черновиком', 2, false],
  ['В эксперименте', 2, false],
  ['Есть упавшие', 1, false],
]

/** All scenarios: drill into one to edit it, or start a new one. */
export function Scenarios() {
  const open = (href = '#/studio') => () => (location.hash = href)
  return (
    <Shell section="scenarios" crumbs={['Сценарии']} who="zhenya">
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <h1 className="h1">Сценарии</h1>
        <a className="btn primary" href="#/home" style={{ marginLeft: 'auto' }}>
          <Plus size={15} />
          Новый сценарий
        </a>
      </div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        {FILTERS.map(([t, n, on]) => (
          <span key={t} className={`badge ${on ? 'accent' : ''}`} style={{ fontSize: 13.5, padding: '3px 11px' }}>
            {t} <b>{n}</b>
          </span>
        ))}
        <div className="input" style={{ width: 280, marginLeft: 'auto' }}>
          <Search size={15} color="var(--faint)" />
          <span className="faint">Название, владелец, метка…</span>
        </div>
      </div>
      <div className="card">
        <table className="t">
          <thead>
            <tr>
              <th>Сценарий</th>
              <th>Владельцы</th>
              <th>Версия</th>
              <th>Черновик</th>
              <th>Выкатка</th>
              <th>Запуски</th>
              <th>Состояние</th>
            </tr>
          </thead>
          <tbody>
            {ROWS.map((r, i) => (
              <tr key={r.id} className={`nav ${i === 0 ? 'sel' : ''}`} onClick={open(r.href)}>
                <td style={{ whiteSpace: 'nowrap' }}>
                  <div style={{ fontWeight: 620 }}>{r.t}</div>
                  <div className="mono tiny muted">{r.id}</div>
                </td>
                <td className="small">{r.owners}</td>
                <td className="small" style={{ whiteSpace: 'nowrap' }}>
                  {r.version}
                </td>
                <td className="small">{r.draft ? <span className="badge accent">{r.draft}</span> : <span className="faint">—</span>}</td>
                <td className="small">
                  {r.rollout ? (
                    <a
                      className="badge info"
                      href="#/rollout"
                      onClick={(e) => e.stopPropagation()}
                      style={{ textDecoration: 'none' }}
                    >
                      {r.rollout}
                    </a>
                  ) : (
                    <span className="faint">—</span>
                  )}
                </td>
                <td className="small">
                  <a className="crumb-link" href="#/runs" onClick={(e) => e.stopPropagation()}>
                    {r.runs}
                  </a>
                </td>
                <td>
                  <span className={`badge ${r.state[0]}`}>{r.state[1]}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="small muted">Клик по строке открывает редактор сценария. Черновик, публикация и выкатка — шаги одной страницы, без вкладок.</div>
    </Shell>
  )
}
