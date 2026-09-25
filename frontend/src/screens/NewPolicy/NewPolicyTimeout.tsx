import { CrossIcon, WarningIcon } from '../../components/icons/lucide'
import { NewPolicyShell } from './NewPolicyShell'

/** DESIGN.md #16: step-1 timeout state. */
export function NewPolicyTimeout({
  onBack,
  errorMessage,
  timedOut,
  fallback,
  onRetry,
}: {
  onBack: () => void
  errorMessage: string
  timedOut: boolean
  fallback: boolean
  onRetry: () => void
}) {
  return (
    <NewPolicyShell
      step={1}
      stepLabel="Describe"
      title="What may your agent do?"
      onCancel={onBack}
      backLabel="Back"
      footer={null}
    >
      <div className="flex flex-col items-center gap-3 rounded-card bg-surface p-8 text-center">
        <span className={`flex size-14 items-center justify-center rounded-full ${timedOut ? 'bg-stopped-tint text-stopped' : 'bg-asked-tint text-asked'}`}>
          {timedOut ? <CrossIcon size={22} strokeWidth={2.2} /> : <WarningIcon size={22} strokeWidth={2.2} />}
        </span>
        <p className="text-[17px] font-semibold text-ink">Something went wrong</p>
        <p className="text-[13px] text-ink-muted">
          {timedOut
            ? 'AI reading is unavailable. Your words are still here.'
            : fallback
              ? errorMessage
              : `AI returned an error: ${errorMessage}`}
        </p>
        <button
          type="button"
          onClick={onRetry}
          className="mt-2 min-h-12 w-full rounded-button bg-ink px-4 text-[15px] font-semibold text-on-ink"
        >
          Try again
        </button>
      </div>
    </NewPolicyShell>
  )
}
