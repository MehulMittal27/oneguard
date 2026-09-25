import type { ReactNode } from 'react'
import { BackChevronIcon } from '../../components/icons/lucide'

/**
 * Shared chrome for the new-policy flow (DESIGN.md #7/#13-16): cancel link,
 * 3-segment progress bar, step label, title/subtitle, scrollable body and a
 * sticky footer. No bottom tab bar on these screens.
 */
export function NewPolicyShell({
  step,
  stepLabel,
  title,
  subtitle,
  onCancel,
  backLabel = 'Cancel',
  footer,
  children,
}: {
  step: 1 | 2 | 3
  stepLabel: string
  title: string
  subtitle?: string
  onCancel: () => void
  backLabel?: string
  footer: ReactNode
  children: ReactNode
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="scrollbar-none flex-1 overflow-y-auto px-8 pt-4 pb-9 sm:pt-5">
        <button
          type="button"
          onClick={onCancel}
          className="flex min-h-11 items-center gap-1 text-[15px] font-semibold text-ink-muted"
        >
          <BackChevronIcon size={20} strokeWidth={2} />
          {backLabel}
        </button>

        <div className="mt-3 flex gap-2" role="progressbar" aria-valuenow={step} aria-valuemin={1} aria-valuemax={3}>
          {[1, 2, 3].map((segment) => (
            <span
              key={segment}
              className={`h-1 flex-1 rounded-pill ${segment <= step ? 'bg-cord-accent' : 'bg-border-quiet'}`}
            />
          ))}
        </div>
        <p className="mt-3 text-[11px] font-semibold tracking-[0.08em] text-ink-muted uppercase">
          Step {step} of 3 · {stepLabel}
        </p>

        <h1 className="mt-2 font-display text-[30px] font-bold text-ink">{title}</h1>
        {subtitle && <p className="mt-1 text-[15px] text-ink-muted">{subtitle}</p>}

        <div className="mt-7 flex flex-col gap-4">{children}</div>
      </div>

      <div className="flex shrink-0 flex-col gap-3 border-t border-hairline bg-surface px-8 pt-3 pb-6">
        {footer}
      </div>
    </div>
  )
}
