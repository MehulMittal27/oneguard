// Minimal service worker — exists only so the app is installable
// (ROADMAP.md slice 9). Deliberately does no caching: a hackathon demo
// rebuilds/redeploys often, and stale cached assets would be a worse
// failure mode than no offline support at all. Register-only, no fetch
// interception, no vite-plugin-pwa dependency (version compatibility with
// this project's Vite version couldn't be verified without network access).
self.addEventListener('install', () => {
  self.skipWaiting()
})

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim())
})
