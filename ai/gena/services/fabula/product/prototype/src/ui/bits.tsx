import { useSyncExternalStore, type ReactNode } from 'react'
import { CheckCircle2, Eye } from 'lucide-react'
import type { Clause } from '../graph/data'

export function PromptView({ clauses, highlight = [], warn = [], links = true, extra }: { clauses: Clause[]; highlight?: number[]; warn?: number[]; links?: boolean; extra?: ReactNode }) {
  return (
    <div className="prompt">
      {clauses.map((c) => (
        <div key={c.n} className={`clause ${highlight.includes(c.n) ? 'hl' : ''} ${warn.includes(c.n) ? 'warn' : ''}`}>
          <span className="n">{c.n}</span>
          <div>
            {c.text}
            {links && (
              <div className="links">
                {c.nodes.map((n) => (
                  <span key={n} className="nodechip">
                    {n}
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>
      ))}
      {extra}
    </div>
  )
}

const YAML_TOKEN = /(#.*$)|('[^']*')|(^\s*-?\s*[A-Za-z_]+:)/g

function colorize(line: string): ReactNode[] {
  const out: ReactNode[] = []
  let last = 0
  line.replace(YAML_TOKEN, (m, c, s, _k, idx: number) => {
    if (idx > last) out.push(line.slice(last, idx))
    out.push(
      <span key={idx} className={c ? 'c' : s ? 's' : 'k'}>
        {m}
      </span>,
    )
    last = idx + m.length
    return m
  })
  if (last < line.length) out.push(line.slice(last))
  return out
}

/** Lines between `from` (a line that starts a block) and the next sibling block. */
export function blockLines(text: string, from: string): number[] {
  const lines = text.split('\n')
  const i = lines.findIndex((l) => l.startsWith(from))
  if (i < 0) return []
  const indent = from.length - from.trimStart().length
  let j = i + 1
  while (j < lines.length && (lines[j].length - lines[j].trimStart().length > indent || lines[j].trim() === '')) j++
  return Array.from({ length: j - i }, (_, k) => i + k + 1)
}

export function OwsView({ text, highlight = [], added = [], removed = [], start = 1, end }: { text: string; highlight?: number[]; added?: number[]; removed?: number[]; start?: number; end?: number }) {
  const lines = text.split('\n')
  return (
    <div className="code">
      {lines.slice(start - 1, end).map((l, i) => {
        const no = start + i
        const cls = added.includes(no) ? 'add' : removed.includes(no) ? 'rem' : highlight.includes(no) ? 'hl' : ''
        return (
          <div key={no} className={`ln ${cls}`}>
            <span className="no">{no}</span>
            <span>{colorize(l)}</span>
          </div>
        )
      })}
    </div>
  )
}

/** Saved as a draft revision on every change; the header only says so. */
export function DraftSaved({ at = '14:02' }: { at?: string }) {
  return (
    <span className="small muted" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, whiteSpace: 'nowrap' }}>
      <CheckCircle2 size={14} color="var(--ok)" />
      черновик сохранён · {at}
    </span>
  )
}

/* A toast for actions that have no screen of their own in the prototype. */

let toastText = ''
let toastTimer = 0
const toastSubs = new Set<() => void>()
export function toast(text: string) {
  toastText = text
  toastSubs.forEach((f) => f())
  clearTimeout(toastTimer)
  toastTimer = window.setTimeout(() => {
    toastText = ''
    toastSubs.forEach((f) => f())
  }, 2400)
}
/** onClick for a button whose action the prototype only acknowledges. */
export const act = (what: string) => () => toast(what)
export function Toaster() {
  const text = useSyncExternalStore(
    (f) => (toastSubs.add(f), () => toastSubs.delete(f)),
    () => toastText,
  )
  return text ? (
    <div className="toast">
      <CheckCircle2 size={16} color="#4ade80" />
      {text}
      <span className="faint small">в прототипе без эффекта</span>
    </div>
  ) : null
}

/** The one page of a scenario: no tabs, the draft and «Посмотреть изменения». */
export function StudioHeader({ version = '1.1.0 · черновик', right, fresh = false }: { version?: string; right?: ReactNode; fresh?: boolean }) {
  return (
    <div className="card" style={{ overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '14px 18px' }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <h1 className="h1">От тикета до выкатки</h1>
            <span className="badge accent">{fresh ? '1.0.0 · черновик' : version}</span>
          </div>
          {fresh ? (
            <div className="muted small" style={{ marginTop: 2 }}>
              <span className="mono">gena/ticket-to-prod</span> · владелец: Женя · ещё не опубликован
            </div>
          ) : (
            <div className="muted small" style={{ marginTop: 2 }}>
              <span className="mono">gena/ticket-to-prod</span> · владельцы: Женя, Гоша ·{' '}
              <a className="crumb-link" href="#/rollout">
                выкатка: trunk → 1.0.0
              </a>{' '}
              ·{' '}
              <a className="crumb-link" href="#/runs">
                запуски: 128
              </a>
            </div>
          )}
        </div>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 10, alignItems: 'center' }}>
          {right ?? (
            <>
              <DraftSaved />
              <a className="btn primary" href="#/changes">
                <Eye size={15} />
                Посмотреть изменения
              </a>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

/** Two buttons instead of a slider: the graph moves between the two versions with a morph. */
export function BeforeAfter({ shown, pick, labels, hint = 'Переключайте: уходящее гаснет, общее переезжает, новое проявляется.' }: { shown: string; pick: (v: 'before' | 'after') => void; labels: [string, string]; hint?: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '2px 4px 8px' }}>
      <div className="seg">
        <span className={shown === 'before' ? 'on' : ''} onClick={() => pick('before')}>
          {labels[0]}
        </span>
        <span className={shown === 'after' ? 'on' : ''} onClick={() => pick('after')}>
          {labels[1]}
        </span>
      </div>
      <span className="small muted">{hint}</span>
    </div>
  )
}

export type EditMode = 'prompt' | 'graph' | 'ows'

const MODES: { id: EditMode; label: string; href: string }[] = [
  { id: 'prompt', label: 'Промт', href: '#/prompt' },
  { id: 'graph', label: 'Граф', href: '#/studio' },
  { id: 'ows', label: 'OWS', href: '#/ows' },
]

/** One way of editing at a time; the others follow and show up in «Посмотреть изменения». */
export function ModeBar({ mode, hint }: { mode: EditMode; hint: string }) {
  return (
    <div className="card" style={{ padding: '10px 14px', display: 'flex', alignItems: 'center', gap: 14 }}>
      <span className="small" style={{ fontWeight: 600 }}>
        Правка
      </span>
      <div className="seg">
        {MODES.map((m) => (
          <a key={m.id} href={m.href} style={{ display: 'contents' }}>
            <span className={m.id === mode ? 'on' : ''}>{m.label}</span>
          </a>
        ))}
      </div>
      <span className="small muted">{hint}</span>
    </div>
  )
}

export function Stack({ parts }: { parts: { v: number; color: string; label: string }[] }) {
  const total = parts.reduce((s, p) => s + p.v, 0)
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div className="stack">
        {parts.map((p) => (
          <i key={p.label} style={{ width: `${(100 * p.v) / total}%`, background: p.color }} />
        ))}
      </div>
      <div className="legend">
        {parts.map((p) => (
          <span key={p.label}>
            <i className="dot" style={{ background: p.color }} />
            <b style={{ color: 'var(--text)' }}>{p.v}</b> {p.label}
          </span>
        ))}
      </div>
    </div>
  )
}
