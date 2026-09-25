import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
//
// Same relative-`/api` + dev-proxy pattern as `frontend/vite.config.ts`: the
// app always fetches a relative `/api`, and the two proxies below are what
// make that work against a local backend without CORS. Port 5174 (not
// Vite's default 5173) so this console and the customer PWA can run side by
// side during a demo — drive a scenario here, watch it land in the phone.
const backend = {
  target: 'http://localhost:8000',
  changeOrigin: true,
}

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5174, proxy: { '/api': backend } },
  preview: { port: 4174, proxy: { '/api': backend } },
})
