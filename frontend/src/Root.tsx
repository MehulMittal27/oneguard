import { lazy, Suspense } from 'react'
import App from './App.tsx'

// The operator console (`/ops`, docs/api-contract.md §6 item 18) is a separate
// page of the same build, loaded only there: the phone UI never ships it, links
// to it or shows it in its tab bar.
const OpsConsole = lazy(() => import('./ops/OpsConsole.tsx'))
const IS_OPS = typeof window !== 'undefined' && window.location.pathname.replace(/\/+$/, '') === '/ops'

export function Root() {
  return IS_OPS ? (
    <Suspense fallback={null}>
      <OpsConsole />
    </Suspense>
  ) : (
    <App />
  )
}
