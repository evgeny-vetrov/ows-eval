import type { ReactNode } from 'react'
import { Activity, ArrowRightLeft, ChevronRight, Inbox, ListTree, Plus, ScrollText, Search, Shield } from 'lucide-react'
import { Toaster } from './bits'

export type Section = 'home' | 'inbox' | 'scenarios' | 'runs' | 'migrations' | 'audit' | 'access'

const NAV: { id: Section; label: string; Icon: typeof Inbox; href: string; count?: number }[] = [
  { id: 'inbox', label: 'Входящие', Icon: Inbox, href: '#/inbox', count: 5 },
  { id: 'scenarios', label: 'Сценарии', Icon: ListTree, href: '#/scenarios' },
  { id: 'runs', label: 'Запуски', Icon: Activity, href: '#/runs' },
  { id: 'migrations', label: 'Миграции', Icon: ArrowRightLeft, href: '#/campaign' },
]
const NAV2: typeof NAV = [
  { id: 'audit', label: 'Аудит', Icon: ScrollText, href: '#/index' },
  { id: 'access', label: 'Доступ', Icon: Shield, href: '#/index' },
]

export const PEOPLE = {
  zhenya: { name: 'Женя', role: 'author', color: '#7c3aed', letter: 'Ж' },
  gosha: { name: 'Гоша', role: 'author', color: '#0f8a7e', letter: 'Г' },
  olga: { name: 'Ольга', role: 'operator', color: '#db2777', letter: 'О' },
  igor: { name: 'Игорь', role: 'operator', color: '#2563eb', letter: 'И' },
} as const
export type Who = keyof typeof PEOPLE

export function Avatar({ who, size = 30 }: { who: Who; size?: number }) {
  const p = PEOPLE[who]
  return (
    <span className="avatar" style={{ background: p.color, width: size, height: size, fontSize: size * 0.43 }}>
      {p.letter}
    </span>
  )
}

/** Breadcrumbs lead back up the path; the last one is where you are. */
const CRUMB: Record<string, string> = {
  Сценарии: '#/scenarios',
  'От тикета до выкатки': '#/studio',
  'Новый сценарий': '#/home',
  Запуски: '#/runs',
  Миграции: '#/campaign',
  Публикация: '#/versions',
  Входящие: '#/inbox',
}

export function Shell({ section, crumbs, who, children, compact = false }: { section: Section; crumbs: string[]; who: Who; children: ReactNode; compact?: boolean }) {
  const item = (n: (typeof NAV)[number]) => (
    <a key={n.id} href={n.href} className={`nav-item ${n.id === section ? 'active' : ''}`}>
      <n.Icon size={17} />
      {!compact && n.label}
      {!compact && n.count ? <span className="count">{n.count}</span> : null}
    </a>
  )
  const p = PEOPLE[who]
  return (
    <div className={`app ${compact ? 'compact' : ''}`}>
      <aside className="side">
        <div className="logo">
          <span className="logo-mark">ф</span>
          {!compact && 'Фабула'}
        </div>
        <a href="#/home" className={`btn primary new-btn ${section === 'home' ? 'on' : ''}`} title="Новый сценарий">
          <Plus size={16} />
          {!compact && 'Новый сценарий'}
        </a>
        {NAV.map(item)}
        <div className="nav-sep" />
        {NAV2.map(item)}
        <div className="side-foot">
          <Avatar who={who} />
          {!compact && (
            <div>
              <div style={{ color: '#fff', fontWeight: 600 }}>{p.name}</div>
              <div style={{ color: '#9aa4b2', fontSize: 12.5 }}>роль: {p.role}</div>
            </div>
          )}
        </div>
      </aside>
      <main className="main">
        <header className="top">
          <div className="crumbs">
            {crumbs.map((c, i) => (
              <span key={i} style={{ display: 'contents' }}>
                {i > 0 && <ChevronRight size={14} />}
                {i === crumbs.length - 1 ? (
                  <b>{c}</b>
                ) : CRUMB[c] ? (
                  <a href={CRUMB[c]} className="crumb">
                    {c}
                  </a>
                ) : (
                  <span>{c}</span>
                )}
              </span>
            ))}
          </div>
          <div className="search">
            <Search size={15} />
            Сценарий, запуск, тикет…
            <span className="kbd">⌘K</span>
          </div>
          <span className="live">
            <i />
            live
          </span>
        </header>
        <div className="content">{children}</div>
      </main>
      <Toaster />
    </div>
  )
}
