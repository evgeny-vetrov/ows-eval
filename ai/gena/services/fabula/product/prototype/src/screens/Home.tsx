import { ArrowRight, GitPullRequest, KeyRound, ListChecks, Paperclip, Plug, Sparkles, UserPlus, Undo2 } from 'lucide-react'
import { Shell } from '../ui/Shell'

const PROMPT =
  'Когда в трекере появляется тикет с меткой ai-ready, запусти агента: он делает изменение и открывает PR. Жди мержа до 30 дней, потом выдержи изменение 3 дня и проверь здоровье сервиса — если не отвечает, повтори до 3 раз. Спроси у владельца тикета, выкатываем ли. Если да — оставь в тикете комментарий «Выкачено» со ссылкой на PR, если нет — заверши. Весь процесс не дольше 60 дней.'

const TEMPLATES = [
  { Icon: GitPullRequest, t: 'От тикета до выкатки', d: 'Агент, PR, мерж, проверка, подтверждение' },
  { Icon: KeyRound, t: 'Согласование доступа', d: 'Заявка, руководитель, владелец ресурса, срок' },
  { Icon: UserPlus, t: 'Онбординг сотрудника', d: 'Аккаунты, железо, наставник, чек-лист' },
  { Icon: Undo2, t: 'Возврат заказа', d: 'Заявка, склад, платёж, уведомление' },
]

export function Home() {
  return (
    <Shell section="home" crumbs={['Сценарии', 'Новый сценарий']} who="zhenya">
      <div style={{ width: '100%', maxWidth: 900, margin: '12px auto 0' }}>
        <div className="col">
          <div className="card" style={{ padding: 22 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 4 }}>
              <Sparkles size={20} color="var(--accent)" />
              <h1 className="h1">Опишите процесс — Фабула соберёт сценарий</h1>
            </div>
            <div className="muted" style={{ marginBottom: 14 }}>
              Своими словами, как объяснили бы коллеге. Если что-то можно понять по-разному, Фабула спросит.
            </div>
            <div style={{ border: '1.5px solid var(--accent)', borderRadius: 12, padding: '14px 16px', boxShadow: '0 0 0 4px rgba(79,70,229,.1)', background: '#fff' }}>
              <div style={{ fontSize: 15.5, lineHeight: 1.6 }}>
                {PROMPT}
                <span style={{ display: 'inline-block', width: 2, height: 18, background: 'var(--accent)', verticalAlign: -3, marginLeft: 2 }} />
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 14, flexWrap: 'wrap' }}>
                <span className="badge">
                  <Paperclip size={13} />
                  Регламент выкатки.pdf
                </span>
                <span className="badge">
                  <Plug size={13} />
                  шаги: агенты gena, MCP tracker
                </span>
                <span className="badge">язык: русский</span>
                <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, paddingTop: 4 }}>
                  <a className="btn" href="#/prompt?new">
                    <ListChecks size={15} />
                    Сначала план
                  </a>
                  <a className="btn primary" href="#/prompt?new">
                    Собрать
                    <ArrowRight size={15} />
                  </a>
                </div>
              </div>
            </div>
          </div>
          <div>
            <div className="section-label" style={{ margin: '4px 2px 10px' }}>
              Начать с шаблона
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
              {TEMPLATES.map(({ Icon, t, d }) => (
                <div key={t} className="card nav" onClick={() => (location.hash = '#/prompt?new')} style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 6 }}>
                  <Icon size={18} color="var(--accent)" />
                  <div style={{ fontWeight: 620 }}>{t}</div>
                  <div className="muted small">{d}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </Shell>
  )
}
