import type { ScenarioSummary, SoftSignalsState } from '../api/types'
import { CheckIcon } from '../components/icons/lucide'
import { groupScenarios, scenarioOptionLabel, type ConsoleRun } from '../lib/opsConsole'
import { softSignalsLabel } from '../lib/softSignals'
import { TEXT_L, TEXT_M } from './style'

const BUTTON = `${TEXT_M} inline-flex min-h-10 items-center justify-center gap-2 rounded-button px-5 font-semibold transition-opacity disabled:cursor-not-allowed disabled:opacity-40`
const PRIMARY = `${BUTTON} bg-ink text-on-ink enabled:hover:opacity-90`
const SECONDARY = `${BUTTON} border border-border-quiet bg-surface text-ink enabled:hover:bg-surface-sunken`

/**
 * Which scenario to start and how: the picker (D9, grouped by customer, the
 * current run's scenario marked), its instruction verbatim, and the start
 * buttons (D2 replay at two speeds, D3 behind a typed confirmation and only while
 * `/healthz` shows the worker polling, saying why when not). Also the
 * chaos toggle (D5) and a health refresh.
 */
export function ScenarioPanel({
  scenarios,
  selected,
  onSelect,
  run,
  busy,
  onReplay,
  onJudgingRun,
  judgingBlocked,
  signIn,
  refusal,
  signals,
  onToggleSignals,
  onRefreshHealth,
  showPhone,
  onShowPhone,
}: {
  scenarios: ScenarioSummary[] | null
  selected: ScenarioSummary | null
  onSelect: (scenarioId: string) => void
  run: ConsoleRun | null
  busy: boolean
  onReplay: (speedMs: number) => void
  onJudgingRun: () => void
  // Why a judging run cannot start (the worker is off or not polling), or null.
  judgingBlocked: string | null
  // Whom to sign in as for the current run; the backend's refusal of a start, verbatim.
  signIn: string | null
  refusal: string | null
  signals: SoftSignalsState | null
  onToggleSignals: () => void
  onRefreshHealth: () => void
  showPhone: boolean
  onShowPhone: (show: boolean) => void
}) {
  const runActive = run !== null && (run.state === 'starting' || run.state === 'running')
  const canStart = selected !== null && selected.card_id !== null && !busy

  return (
    <section aria-label="Scenario" className="flex flex-col gap-5 rounded-card border border-hairline bg-surface p-8">
      <div className="flex items-center gap-4">
        <label htmlFor="ops-scenario" className={`${TEXT_M} font-semibold text-ink-muted`}>
          Scenario
        </label>
        <select
          id="ops-scenario"
          value={selected?.scenario_id ?? ''}
          disabled={!scenarios || scenarios.length === 0}
          onChange={(e) => onSelect(e.target.value)}
          className={`${TEXT_M} min-h-10 max-w-[560px] min-w-0 flex-1 rounded-button border border-border-quiet bg-surface px-4 font-semibold text-ink tabular-nums`}
        >
          {!scenarios && <option value="">Reading scenarios…</option>}
          {scenarios?.length === 0 && <option value="">No scenarios in the store</option>}
          {scenarios &&
            groupScenarios(scenarios).map((group) => (
              <optgroup key={group.key} label={group.label}>
                {group.scenarios.map((s) => {
                  const current = run?.scenarioId === s.scenario_id
                  const mark = current ? (runActive ? '  ● running' : '  · last run') : ''
                  return (
                    <option key={s.scenario_id} value={s.scenario_id}>
                      {scenarioOptionLabel(s)}
                      {mark}
                    </option>
                  )
                })}
              </optgroup>
            ))}
        </select>
        <label className={`${TEXT_M} ml-auto flex min-h-10 shrink-0 cursor-pointer items-center gap-3 font-semibold text-ink`}>
          <input
            type="checkbox"
            checked={showPhone}
            onChange={(e) => onShowPhone(e.target.checked)}
            className="size-5 accent-ink"
          />
          Show customer phone
        </label>
      </div>

      {selected && (
        <div className="flex flex-col gap-2">
          {/* The cardholder's instruction, verbatim: the customer's own words. */}
          <p className={`${TEXT_L} font-medium text-ink`}>“{selected.instruction}”</p>
          <p className={`${TEXT_M} text-ink-muted`}>
            {selected.customer_id
              ? `${selected.customer_name ?? selected.customer_id} (${selected.customer_id}) · card ${selected.card_id}`
              : 'No customer or card named for this scenario yet'}
          </p>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <button type="button" className={PRIMARY} disabled={!canStart} onClick={() => onReplay(3000)}>
          Replay 3 s
        </button>
        <button type="button" className={PRIMARY} disabled={!canStart} onClick={() => onReplay(15000)}>
          Replay 15 s
        </button>
        <button
          type="button"
          className={SECONDARY}
          disabled={!canStart || judgingBlocked !== null}
          title={judgingBlocked ?? undefined}
          aria-describedby={judgingBlocked ? 'ops-judging-blocked' : undefined}
          onClick={onJudgingRun}
        >
          Judging run (live)
        </button>

        <span aria-hidden="true" className="mx-2 h-6 w-px bg-hairline" />

        <button
          type="button"
          className={SECONDARY}
          disabled={!signals}
          aria-pressed={Boolean(signals?.live && signals.replay)}
          onClick={onToggleSignals}
        >
          Soft signals: {softSignalsLabel(signals)}
        </button>
        <button type="button" className={SECONDARY} onClick={onRefreshHealth}>
          Refresh health
        </button>
      </div>

      {judgingBlocked && (
        <p id="ops-judging-blocked" className={`${TEXT_M} text-ink-muted`}>
          {judgingBlocked}
        </p>
      )}
      {signIn && (
        <p role="status" className={`${TEXT_M} flex items-center gap-2 font-semibold text-approved`}>
          <CheckIcon size={16} />
          {signIn}
        </p>
      )}
      {refusal && (
        <p role="alert" className={`${TEXT_M} rounded-row bg-stopped-tint px-4 py-3 text-stopped`}>
          {refusal}
        </p>
      )}
    </section>
  )
}
