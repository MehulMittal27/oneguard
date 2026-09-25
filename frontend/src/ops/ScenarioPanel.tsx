import type { ScenarioSummary, SoftSignalsState } from '../api/types'
import { CheckIcon, InfoIcon } from '../components/icons/lucide'
import { groupScenarios, scenarioOptionLabel, type ConsoleRun, type ReplayGuard } from '../lib/opsConsole'
import { softSignalsLabel } from '../lib/softSignals'
import { BUTTON_PRIMARY as PRIMARY, BUTTON_SECONDARY as SECONDARY, TEXT_L, TEXT_M } from './style'

/**
 * Which scenario to start and how: the picker (D9, grouped by customer, the
 * current run's scenario marked), its instruction verbatim, whom to sign in as
 * for it and, on its own line, the run D7 names, and the start buttons (D2
 * replay at two speeds: the pack's purchases, or a served scenario's stored
 * events "from record", disabled with the reason before any live run of it; D3 behind a typed confirmation and only for a
 * scenario the platform serves now (D8) while `/healthz` shows the worker polling,
 * saying why when not, and warning when the scenario already has a finished live
 * run on record). Also the
 * chaos toggle (D5) and a health refresh.
 *
 * While `LIVE_RUNS_FROM_TERMINAL`, the judging run button is hidden: a note says live
 * runs start from the operator terminal (`make demo-live`), and the console follows
 * whichever run D7 names; the reasons a live run cannot start stay shown.
 */
// Set false to bring back "Judging run (live)" (D3 from the console, JudgingRunDialog).
const LIVE_RUNS_FROM_TERMINAL = true

export function ScenarioPanel({
  scenarios,
  selected,
  onSelect,
  run,
  busy,
  onReplay,
  replay,
  onJudgingRun,
  judgingBlocked,
  judgingWarning,
  signIn,
  lastRun,
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
  // Whether D2 can replay the selected scenario, and what the replay is.
  replay: ReplayGuard
  onJudgingRun: () => void
  // Why a judging run cannot start (replay only, or the worker is off or not polling), or null.
  judgingBlocked: string | null
  // A served scenario whose finished live run is already on record; never disables the button.
  judgingWarning: string | null
  // Whom to sign in as for the selected scenario; the run D7 names, on its own
  // line; the backend's refusal of a start, verbatim.
  signIn: string | null
  lastRun: string | null
  refusal: string | null
  signals: SoftSignalsState | null
  onToggleSignals: () => void
  onRefreshHealth: () => void
  showPhone: boolean
  onShowPhone: (show: boolean) => void
}) {
  const runActive = run !== null && (run.state === 'starting' || run.state === 'running')
  const canStart = selected !== null && selected.card_id !== null && !busy
  const canReplay = canStart && replay.blocked === null

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
            className="size-5 accent-[var(--ops-gold)]"
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
        {[3000, 15000].map((ms) => (
          <button
            key={ms}
            type="button"
            className={PRIMARY}
            disabled={!canReplay}
            title={replay.blocked ?? undefined}
            aria-describedby={
              [replay.blocked && 'ops-replay-blocked', replay.note && 'ops-replay-note'].filter(Boolean).join(' ') ||
              undefined
            }
            onClick={() => onReplay(ms)}
          >
            Replay {ms / 1000} s
          </button>
        ))}
        {!LIVE_RUNS_FROM_TERMINAL && (
          <button
            type="button"
            className={SECONDARY}
            disabled={!canStart || judgingBlocked !== null}
            title={judgingBlocked ?? undefined}
            aria-describedby={
              [judgingBlocked && 'ops-judging-blocked', judgingWarning && 'ops-judging-warning'].filter(Boolean).join(' ') ||
              undefined
            }
            onClick={onJudgingRun}
          >
            Judging run (live)
          </button>
        )}

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

      {LIVE_RUNS_FROM_TERMINAL && (
        <p id="ops-live-from-terminal" className={`${TEXT_M} flex items-start gap-2 text-ink-muted`}>
          <span className="mt-[3px] shrink-0">
            <InfoIcon size={16} />
          </span>
          <span>
            Live runs are started from the operator terminal (
            <code className="font-mono text-ink">make demo-live SCEN=&lt;id&gt;</code>); this console follows the run
            automatically.
          </span>
        </p>
      )}
      {replay.blocked && (
        <p id="ops-replay-blocked" className={`${TEXT_M} text-ink-muted`}>
          {replay.blocked}
        </p>
      )}
      {replay.note && (
        <p id="ops-replay-note" className={`${TEXT_M} text-ink-muted`}>
          {replay.note}
        </p>
      )}
      {judgingBlocked && (
        <p id="ops-judging-blocked" className={`${TEXT_M} text-ink-muted`}>
          {judgingBlocked}
        </p>
      )}
      {judgingWarning && (
        <p id="ops-judging-warning" className={`${TEXT_M} flex items-center gap-2 font-semibold text-asked-ink`}>
          <InfoIcon size={16} />
          {judgingWarning}
        </p>
      )}
      {signIn && (
        <p role="status" className={`${TEXT_M} flex items-center gap-2 font-semibold text-approved`}>
          <CheckIcon size={16} />
          {signIn}
        </p>
      )}
      {lastRun && <p className={`${TEXT_M} text-ink-muted tabular-nums`}>{lastRun}</p>}
      {refusal && (
        <p role="alert" className={`${TEXT_M} rounded-row bg-stopped-tint px-4 py-3 text-stopped`}>
          {refusal}
        </p>
      )}
    </section>
  )
}
