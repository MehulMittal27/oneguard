import { SpinnerIcon } from '../../components/icons/lucide'
import { NewPolicyShell } from './NewPolicyShell'

/** DESIGN.md #15: step-1 reading state, cancel-only footer. */
export function NewPolicyReading({
  instruction,
  onCancel,
}: {
  instruction: string
  onCancel: () => void
}) {
  return (
    <NewPolicyShell
      step={1}
      stepLabel="Describe"
      title="Tell us what your agent may buy"
      onCancel={onCancel}
      footer={
        <button
          type="button"
          onClick={onCancel}
          className="min-h-11 text-[15px] font-semibold text-ink-muted"
        >
          Cancel
        </button>
      }
    >
      <div
        className="flex flex-col items-center gap-3 rounded-card bg-surface p-8 text-center"
        aria-live="polite"
        aria-busy="true"
      >
        <SpinnerIcon size={28} strokeWidth={2.5} />
        <p className="text-[17px] font-semibold text-ink">Reading your words…</p>
        <p className="text-[13px] text-ink-muted">
          This usually takes a few seconds. Nothing is active yet.
        </p>
        <div className="mt-1 h-1.5 w-full overflow-hidden rounded-pill bg-surface-sunken">
          <div className="h-full w-1/3 animate-pulse rounded-pill bg-cord-accent" />
        </div>
      </div>

      <div className="rounded-card border border-hairline bg-surface p-4">
        <p className="text-[11px] font-semibold tracking-[0.08em] text-ink-muted uppercase">
          Your words
        </p>
        <p className="mt-2 text-[15px] text-ink-soft">{instruction}</p>
      </div>
    </NewPolicyShell>
  )
}
