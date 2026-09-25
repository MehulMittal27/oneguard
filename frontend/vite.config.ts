import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
//
// `.env` points the app at a relative `/api` (D-100), so whoever serves the
// page has to forward that to the backend. Keeping the base URL relative is
// what lets one build run behind the dev server, behind FastAPI's own static
// mount, and behind any single-origin deployment — with no rebuild and no
// CORS. The two proxies below are the dev-server half of that.
const backend = {
  target: 'http://localhost:8000',
  changeOrigin: true,
}

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // `npm run dev` — keeps HMR while the app talks to the real engine.
  // `/healthz` too: the operator console (`/ops`) shows it.
  server: { proxy: { '/api': backend, '/healthz': backend } },
  // `npm run preview` — the built bundle, still against a local backend.
  preview: { proxy: { '/api': backend, '/healthz': backend } },
})
