import { useState } from 'react'
import type { Customer } from '../api/types'
import { listScenarios } from '../api/scenarios'
import { usePolling } from '../lib/usePolling'
import { SCENARIOS_POLL_SECONDS } from '../config'
import { NowRunningPanel } from '../components/NowRunningPanel'
import { ScenarioCard } from '../components/ScenarioCard'
import { RunLauncher } from '../components/RunLauncher'
import { EmptyState, ErrorState, Spinner } from '../components/StateViews'

/**
 * The home screen: what the judging pack serves (D8), what's running now
 * (D7, in `NowRunningPanel`), and the controls to start the next one.
 * `docs/judging-pack.md` is the reference for which ten scenarios exist.
 */
export function ScenariosScreen({ customers }: { customers: Customer[] }) {
  const { data: scenarios, error, loading, refresh } = usePolling(listScenarios, SCENARIOS_POLL_SECONDS, [])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const selected = scenarios?.find((s) => s.scenario_id === selectedId) ?? null

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-[20px] font-semibold text-ink">Scenarios</h1>
        <p className="text-[13px] text-ink-muted">The catalogue Viseca serves this team, and what's bound to which customer.</p>
      </div>

      <NowRunningPanel customers={customers} />

      {loading && !scenarios && <Spinner />}
      {error ? <ErrorState error={error} onRetry={refresh} /> : null}
      {scenarios && scenarios.length === 0 && <EmptyState>No scenarios in the catalogue yet — the worker syncs it from Viseca at start.</EmptyState>}

      {scenarios && scenarios.length > 0 && (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_360px]">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {scenarios.map((s) => (
              <ScenarioCard key={s.scenario_id} scenario={s} selected={s.scenario_id === selectedId} onClick={() => setSelectedId(s.scenario_id)} />
            ))}
          </div>

          <aside className="h-fit rounded-card border border-hairline bg-surface p-5 lg:sticky lg:top-7">
            {selected ? (
              <div className="space-y-4">
                <div>
                  <p className="text-[14px] font-semibold text-ink">{selected.scenario_name}</p>
                  <p className="text-[11px] tabular-nums text-ink-muted">{selected.scenario_id}</p>
                  <p className="mt-2 text-[12.5px] text-ink-soft">{selected.cardholder_instruction}</p>
                </div>
                <RunLauncher scenario={selected} customers={customers} />
              </div>
            ) : (
              <p className="text-[13px] text-ink-muted">Select a scenario to configure and run it.</p>
            )}
          </aside>
        </div>
      )}
    </div>
  )
}
