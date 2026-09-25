import { getSoftSignals, setSoftSignals } from '../api/softSignals'
import { usePolling } from '../lib/usePolling'
import { RUN_POLL_SECONDS } from '../config'
import { Badge } from '../components/Badge'
import { ErrorState, Spinner } from '../components/StateViews'

/**
 * D5, the chaos toggle already used by `frontend/src/components/OperatorStrip.tsx`
 * — given its own screen here since an admin console is where a toggle that
 * can turn a model off mid-demo belongs, not a thin strip behind a query param.
 */
export function SettingsScreen() {
  const { data: signals, error, loading, refresh } = usePolling(getSoftSignals, RUN_POLL_SECONDS, [])

  async function toggle() {
    if (!signals) return
    await setSoftSignals(!(signals.live && signals.replay))
    refresh()
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-[20px] font-semibold text-ink">Settings</h1>
        <p className="text-[13px] text-ink-muted">Operator-only switches. Never reachable from the customer app.</p>
      </div>

      <section className="rounded-card border border-hairline bg-surface p-5">
        <h2 className="text-[14px] font-semibold text-ink">Soft signals (Laya)</h2>
        <p className="mt-1 text-[12.5px] text-ink-muted">
          Can only add evidence and raise <code>approve → step_up</code>; never lowers a decline or approves on its own
          (docs/rules.md). Off it, the keyword detector or nothing runs instead.
        </p>
        <div className="mt-4 flex items-center gap-3">
          {loading && !signals && <Spinner />}
          {error ? <ErrorState error={error} onRetry={refresh} /> : null}
          {signals && (
            <>
              <Badge tone={signals.live ? 'accent' : 'neutral'}>live: {signals.live ? 'on' : 'off'}</Badge>
              <Badge tone={signals.replay ? 'accent' : 'neutral'}>replay: {signals.replay ? 'on' : 'off'}</Badge>
              <button
                type="button"
                onClick={toggle}
                className="ml-auto rounded-button bg-ink px-4 py-2 text-[12.5px] font-semibold text-on-ink"
              >
                Turn {signals.live && signals.replay ? 'off' : 'on'}
              </button>
            </>
          )}
        </div>
      </section>

      <section className="rounded-card border border-hairline bg-surface p-5">
        <h2 className="text-[14px] font-semibold text-ink">Connection</h2>
        <p className="mt-1 text-[12.5px] text-ink-muted">
          API base URL: <code className="rounded bg-surface-sunken px-1.5 py-0.5">{import.meta.env.VITE_API_BASE_URL ?? '/api'}</code>. This
          console never holds the Viseca key or calls Viseca directly — every action here goes through this app's own{' '}
          <code>/api/dev/*</code> operator endpoints (root CLAUDE.md rule 8).
        </p>
      </section>
    </div>
  )
}
