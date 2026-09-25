import { useEffect, useState, type FormEvent } from 'react'
import type { ScenarioSummary } from '../api/types'
import { BUTTON_PRIMARY, BUTTON_SECONDARY, TEXT_L, TEXT_M } from './style'

/**
 * D3 behind a typed confirmation: a judging run talks to the payment platform
 * and holds the team's one delivery slot, so the operator types the scenario id
 * before it starts. The backend's refusal (409 `run_active`, `runs_disabled`,
 * …) is shown in its own words, and the dialog stays open.
 */
export function JudgingRunDialog({
  scenario,
  busy,
  refusal,
  onStart,
  onClose,
}: {
  scenario: ScenarioSummary
  busy: boolean
  refusal: string | null
  onStart: () => void
  onClose: () => void
}) {
  const [typed, setTyped] = useState('')
  const matches = typed.trim() === scenario.scenario_id

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  function submit(e: FormEvent) {
    e.preventDefault()
    if (matches && !busy) onStart()
  }

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center p-8">
      <button type="button" aria-label="Cancel" onClick={onClose} className="absolute inset-0 bg-scrim" />
      <form
        role="dialog"
        aria-modal="true"
        aria-labelledby="ops-judging-title"
        onSubmit={submit}
        className={`${TEXT_M} relative flex w-[min(560px,100%)] flex-col gap-5 rounded-card bg-surface p-9 shadow-[0_30px_60px_-20px_rgba(0,0,0,0.45)]`}
      >
        <h2 id="ops-judging-title" className={`${TEXT_L} font-bold text-ink`}>
          Judging run (live)
        </h2>
        <p className="text-ink-soft">
          This starts {scenario.scenario_id} · {scenario.name} on the Viseca sandbox, on card {scenario.card_id}{' '}
          ({scenario.customer_name ?? scenario.customer_id}). It needs the card&apos;s active policy, and only one run
          can be open at a time. Type the scenario id to start it.
        </p>
        <label className="flex flex-col gap-2 font-semibold text-ink">
          Scenario id
          <input
            autoFocus
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder={scenario.scenario_id}
            autoComplete="off"
            spellCheck={false}
            className="min-h-11 rounded-button border border-border-quiet px-4 font-normal tabular-nums"
          />
        </label>
        {refusal && (
          <p role="alert" className="rounded-row bg-stopped-tint px-4 py-3 text-stopped">
            {refusal}
          </p>
        )}
        <div className="flex justify-end gap-3">
          <button
            type="button"
            onClick={onClose}
            className={BUTTON_SECONDARY}
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={!matches || busy}
            className={BUTTON_PRIMARY}
          >
            {busy ? 'Starting…' : 'Start judging run'}
          </button>
        </div>
      </form>
    </div>
  )
}
