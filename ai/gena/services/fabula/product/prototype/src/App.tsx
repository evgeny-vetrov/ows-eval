import { useEffect, useState, type ReactNode } from 'react'
import { Home } from './screens/Home'
import { Studio } from './screens/Studio'
import { PromptMode } from './screens/PromptMode'
import { OwsMode } from './screens/OwsMode'
import { Changes, type ChangesView } from './screens/Changes'
import { DiffScreen } from './screens/Diff'
import { Rollout, Versions } from './screens/Release'
import { Scenarios } from './screens/Scenarios'
import { Campaign, Inbox, RunDetail, Runs } from './screens/Operate'
import { ClarifyDiagram, LifecycleDiagram, MapDiagram, ModelDiagram } from './diagrams/Diagrams'

type Route = { path: string; params: URLSearchParams }

function parse(): Route {
  const [path, q] = location.hash.replace(/^#/, '').split('?')
  return { path: path || '/index', params: new URLSearchParams(q ?? '') }
}

/** One editor page for a new scenario and for a draft; all /prompt routes share it. */
const prompt = (p: URLSearchParams) => <PromptMode fresh={p.has('new')} view={p.get('view') === 'ows' ? 'ows' : 'graph'} />

export const SCREENS: { path: string; title: string; render: (p: URLSearchParams) => ReactNode }[] = [
  { path: '/home', title: '1. «+ Новый сценарий»: промт и шаблоны', render: () => <Home /> },
  { path: '/prompt?new', title: '2. Редактор нового сценария: промт и вопрос слева, граф справа (наведите на варианты)', render: prompt },
  { path: '/prompt?new&view=ows', title: '2. Редактор нового сценария: как изменится OWS', render: prompt },
  { path: '/studio', title: '3. Редактор: правка графа', render: () => <Studio /> },
  { path: '/ows', title: '3. Редактор: правка OWS — код с диагностикой', render: () => <OwsMode /> },
  { path: '/prompt', title: '4. Редактор: правка промта, граф рядом', render: prompt },
  { path: '/changes', title: '5. «Посмотреть изменения»: дифф графа', render: (p) => <Changes view={(p.get('view') as ChangesView) ?? 'graph'} /> },
  { path: '/changes?view=prompt', title: '5. «Посмотреть изменения»: дифф промта', render: () => <Changes view="prompt" /> },
  { path: '/changes?view=ows', title: '5. «Посмотреть изменения»: дифф OWS', render: () => <Changes view="ows" /> },
  { path: '/diff', title: '6. Дифф-вьюер версий: наложение, «Было / Стало»', render: (p) => <DiffScreen mode={p.get('mode') === 'side' ? 'side' : 'overlay'} /> },
  { path: '/diff?mode=side', title: '6. Дифф-вьюер версий: рядом', render: () => <DiffScreen mode="side" /> },
  { path: '/versions', title: '7. Публикация: прогноз, проверки, эксперимент', render: (p) => <Versions experiment={p.has('experiment')} /> },
  { path: '/versions?experiment', title: '7. Публикация: запуск эксперимента', render: () => <Versions experiment /> },
  { path: '/rollout', title: '8. Выкатка: эксперимент, A/B, откат', render: () => <Rollout /> },
  { path: '/scenarios', title: '9. Сценарии: список, вход в правку', render: () => <Scenarios /> },
  { path: '/runs', title: '10. Запуски', render: () => <Runs /> },
  { path: '/run', title: '11. Карточка запуска', render: () => <RunDetail /> },
  { path: '/campaign', title: '12. Миграция запусков', render: () => <Campaign /> },
  { path: '/inbox', title: '13. Входящие и предложение агента', render: () => <Inbox /> },
  { path: '/d/model', title: 'Схема: как устроена правка', render: () => <ModelDiagram /> },
  { path: '/d/clarify', title: 'Схема: как появляется вопрос', render: () => <ClarifyDiagram /> },
  { path: '/d/lifecycle', title: 'Схема: жизненный цикл', render: () => <LifecycleDiagram /> },
  { path: '/d/map', title: 'Схема: карта интерфейса', render: () => <MapDiagram /> },
]

function Index() {
  return (
    <div className="index-list">
      <h1 className="h1" style={{ gridColumn: '1 / -1' }}>
        Фабула — прототип интерфейса
      </h1>
      <p className="muted" style={{ gridColumn: '1 / -1', marginTop: -4 }}>
        Макеты веб-интерфейса на статических данных сценария gena/ticket-to-prod, без бэкенда. Кнопки ведут по пути персон: «+ Новый сценарий» → редактор с вопросом под промтом → «Посмотреть изменения» → «К публикации» → «Сохранить и запустить» → миграция → входящие; или «Запустить эксперимент» → выкатка → «Запустить на 100 %». Действия без своего экрана показывают подтверждение внизу. Живое: в экране 2 наведите курсор на варианты ответа и ответьте — ответ ляжет в промт; в экранах 4–6 граф переходом показывает правку, кнопки «Было / Стало» повторяют его; в экране 7 «Запустить эксперимент» открывает диалог.
      </p>
      {SCREENS.map((s) => (
        <a key={s.path} href={`#${s.path}`}>
          {s.title}
        </a>
      ))}
    </div>
  )
}

export function App() {
  const [route, setRoute] = useState(parse)
  useEffect(() => {
    const on = () => setRoute(parse())
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  const screen = SCREENS.find((s) => s.path.split('?')[0] === route.path)
  return <>{screen ? screen.render(route.params) : <Index />}</>
}
