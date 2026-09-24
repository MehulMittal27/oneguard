import { ClockIcon } from '../../components/icons/lucide'
import { NewPolicyShell } from './NewPolicyShell'

/** DESIGN.md #16: step-1 timeout state. */
export function NewPolicyTimeout({
  instruction,
  onCancel,
  onRetry,
  onUseForm,
}: {
  instruction: string
  onCancel: () => void
  onRetry: () => void
  onUseForm: () => void
}) {
  return (
    <NewPolicyShell
      step={1}
      stepLabel="Describe"
      title="Tell us what your agent may buy"
      onCancel={onCancel}
      footer={
        <>
          <button
            type="button"
            onClick={onRetry}
            className="h-14 rounded-row bg-ink text-[16px] font-semibold text-on-ink enabled:hover:opacity-90"
          >
            Try again
          </button>
          <button
            type="button"
            onClick={onUseForm}
            className="h-13 rounded-button border-2 border-ink text-[15px] font-semibold text-ink"
          >
            Use the form instead
          </button>
        </>
      }
    >
      <div className="flex flex-col items-center gap-3 rounded-card bg-surface p-8 text-center">
        <span className="flex size-14 items-center justify-center rounded-full bg-surface-sunken text-ink-muted">
          <ClockIcon size={26} strokeWidth={1.8} />
        </span>
        <p className="text-[17px] font-semibold text-ink">The AI took too long</p>
        <p className="text-[13px] text-ink-muted">
          We stopped reading and saved nothing. Nothing was approved while we were offline. No
          rule is active. Your words are still here.
        </p>
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
