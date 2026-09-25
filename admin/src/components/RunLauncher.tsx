import { useState } from 'react'
import type { Customer, Scenario } from '../api/types'
import { ApiError } from '../api/client'
import { getPolicy } from '../api/customers'
import { draftFromInstruction, confirmDraft } from '../api/policy'
import { createRun } from '../api/runs'
import { restartReplay } from '../api/replay'
import { useStepRunner } from '../lib/useStepRunner'
import { StepList } from './StepList'
import { ErrorState } from './StateViews'

type Mode = 'live' | 'offline'

/**
 * Starts a scenario, one visible step at a time — the exact recipe
 * `make demo-live` and D3 use (`docs/api-contract.md` §1.2): ensure the card
 * has an active policy (compiling the scenario's own cardholder instruction
 * through C1/C2 when it doesn't), then D3. Offline replay (D2) skips the
 * policy step — it compiles the instruction ad hoc for the replay only,
 * never storing a mandate (`routes_dev.py`'s `_replay_policy`).
 */
export function RunLauncher({ scenario, customers }: { scenario: Scenario; customers: Customer[] }) {
  const [mode, setMode] = useState<Mode>('live')
  const boundCardId = scenario.profile?.card_id ?? null
  const [cardId, setCardId] = useState(boundCardId ?? '')
  const [offlineScenarioId, setOfflineScenarioId] = useState(scenario.scenario_id)
  const [speedMs, setSpeedMs] = useState(2000)
  const { steps, error, running, run } = useStepRunner()

  const effectiveCardId = (boundCardId ?? cardId).trim()
  const runActiveDetail =
    error instanceof ApiError && error.code === 'run_active' ? (error.detail as { run_id?: string } | undefined) : null

  async function startLive(force: boolean) {
    if (!effectiveCardId) return
    await run([
      {
        label: `Ensure an active policy on card ${effectiveCardId}`,
        action: async () => {
          const existing = await getPolicy(effectiveCardId)
          if (existing && existing.status === 'active') return `Already active: ${existing.mandate_id}`
          const draft = await draftFromInstruction(effectiveCardId, scenario.cardholder_instruction)
          const mandate = await confirmDraft(draft.draft_id, draft)
          return `Compiled + confirmed ${mandate.mandate_id} (${mandate.checks.length} check(s), compiler: ${draft.compiler ?? 'llm'})`
        },
      },
      {
        label: force ? 'Start the live run at Viseca — forced (D3)' : 'Start the live run at Viseca (D3)',
        action: async () => {
          const liveRun = await createRun({ scenario_id: scenario.scenario_id, card_id: effectiveCardId, force })
          const who = liveRun.customer_name ? ` — ${liveRun.customer_name} (${liveRun.customer_id})` : ''
          return `Run ${liveRun.run_id} on card ${liveRun.card_id}${who}. Watch it in "Now running" above.`
        },
      },
    ])
  }

  async function startOffline() {
    const scenarioId = offlineScenarioId.trim()
    const targetCard = effectiveCardId
    if (!scenarioId || !targetCard) return
    await run([
      {
        label: `Replay ${scenarioId} on card ${targetCard} offline (D2)`,
        action: async () => {
          const status = await restartReplay({ scenario_id: scenarioId, card_id: targetCard, speed_ms: speedMs })
          return `${status.total} purchase(s) queued, one every ${speedMs} ms. Watch it in "Now running" above.`
        },
      },
    ])
  }

  return (
    <div className="space-y-4">
      <div className="flex gap-1 rounded-pill bg-surface-sunken p-1 text-[12.5px] font-semibold">
        {(['live', 'offline'] as const).map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => setMode(m)}
            className={`flex-1 rounded-pill py-1.5 transition-colors ${
              mode === m ? 'bg-surface text-ink shadow-sm' : 'text-ink-muted'
            }`}
          >
            {m === 'live' ? 'Live run (Viseca)' : 'Offline replay'}
          </button>
        ))}
      </div>

      {mode === 'live' ? (
        <div className="space-y-3">
          <label className="block text-[12.5px] font-medium text-ink-soft">
            Card
            {boundCardId ? (
              <p className="mt-1 rounded-tile bg-surface-sunken px-3 py-2 text-ink tabular-nums">{boundCardId} (bound to this scenario)</p>
            ) : (
              <>
                <input
                  list="admin-card-ids"
                  value={cardId}
                  onChange={(e) => setCardId(e.target.value)}
                  placeholder="e.g. CA1331"
                  className="mt-1 w-full rounded-tile border border-border-quiet px-3 py-2 text-ink tabular-nums outline-none focus:border-cord-accent"
                />
                <datalist id="admin-card-ids">
                  {customers
                    .filter((c) => c.card_id)
                    .map((c) => (
                      <option key={c.customer_id} value={c.card_id ?? ''}>
                        {c.name}
                      </option>
                    ))}
                </datalist>
                <span className="mt-1 block text-[11px] font-normal text-ink-muted">
                  Not run yet — pick any card from Customers. D3 refuses an unknown card or the wrong one for this scenario.
                </span>
              </>
            )}
          </label>
          <button
            type="button"
            disabled={!effectiveCardId || running}
            onClick={() => startLive(false)}
            className="w-full rounded-button bg-ink py-2.5 text-[13px] font-semibold text-on-ink disabled:opacity-40"
          >
            {running ? 'Running…' : 'Provision & run'}
          </button>
          {runActiveDetail?.run_id && (
            <button
              type="button"
              disabled={running}
              onClick={() => startLive(true)}
              className="w-full rounded-button border border-destructive-border py-2 text-[12.5px] font-semibold text-destructive disabled:opacity-40"
            >
              Force start anyway (run {runActiveDetail.run_id} is in progress)
            </button>
          )}
        </div>
      ) : (
        <div className="space-y-3">
          <label className="block text-[12.5px] font-medium text-ink-soft">
            Local pack scenario id
            <input
              value={offlineScenarioId}
              onChange={(e) => setOfflineScenarioId(e.target.value)}
              className="mt-1 w-full rounded-tile border border-border-quiet px-3 py-2 text-ink outline-none focus:border-cord-accent"
            />
            <span className="mt-1 block text-[11px] font-normal text-ink-muted">
              D2 replays `data/`, the local pack — its scenario ids may differ from the served catalogue above
              (docs/judging-pack.md).
            </span>
          </label>
          <label className="block text-[12.5px] font-medium text-ink-soft">
            Card
            <input
              list="admin-card-ids"
              value={cardId}
              onChange={(e) => setCardId(e.target.value)}
              placeholder="e.g. CA1331"
              className="mt-1 w-full rounded-tile border border-border-quiet px-3 py-2 text-ink tabular-nums outline-none focus:border-cord-accent"
            />
          </label>
          <label className="block text-[12.5px] font-medium text-ink-soft">
            Speed (ms between purchases)
            <input
              type="number"
              min={0}
              step={250}
              value={speedMs}
              onChange={(e) => setSpeedMs(Number(e.target.value))}
              className="mt-1 w-full rounded-tile border border-border-quiet px-3 py-2 text-ink tabular-nums outline-none focus:border-cord-accent"
            />
          </label>
          <button
            type="button"
            disabled={!offlineScenarioId.trim() || !cardId.trim() || running}
            onClick={startOffline}
            className="w-full rounded-button bg-ink py-2.5 text-[13px] font-semibold text-on-ink disabled:opacity-40"
          >
            {running ? 'Starting…' : 'Start offline replay'}
          </button>
        </div>
      )}

      <StepList steps={steps} />
      {error && !runActiveDetail ? <ErrorState error={error} /> : null}
    </div>
  )
}
