import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

// PWA installability (ROADMAP.md slice 9) — a minimal, no-op service
// worker (see public/sw.js), not vite-plugin-pwa: this sandbox has no
// network access to verify the plugin's compatibility with this project's
// Vite version.
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js')
  })
}
