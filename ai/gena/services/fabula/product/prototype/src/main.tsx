import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@fontsource/inter/400.css'
import '@fontsource/inter/500.css'
import '@fontsource/inter/600.css'
import '@fontsource/inter/700.css'
import '@fontsource/jetbrains-mono/400.css'
import '@fontsource/jetbrains-mono/500.css'
import '@xyflow/react/dist/style.css'
import './styles.css'
import './graph/frames'
import { ready } from './graph/layout'
import { App } from './App'

window.__ready = false
if (new URLSearchParams(location.search).has('shot')) document.documentElement.classList.add('shooting')

ready().then(() => {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
  document.fonts.ready.then(() => requestAnimationFrame(() => requestAnimationFrame(() => (window.__ready = true))))
})
