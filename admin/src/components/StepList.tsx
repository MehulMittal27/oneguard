import type { ReactElement } from 'react'
import { Check, CircleDashed, LoaderCircle, X } from 'lucide-react'
import type { Step } from '../lib/useStepRunner'

const ICON: Record<Step['status'], (props: { size: number }) => ReactElement> = {
  pending: (p) => <CircleDashed {...p} className="text-ink-muted/60" />,
  active: (p) => <LoaderCircle {...p} className="animate-spin text-cord-accent" />,
  done: (p) => <Check {...p} className="text-approved" />,
  error: (p) => <X {...p} className="text-stopped" />,
}

/** The visible trail of calls a launch made — `useStepRunner`'s state, rendered. */
export function StepList({ steps }: { steps: Step[] }) {
  if (steps.length === 0) return null
  return (
    <ol className="space-y-2 rounded-tile bg-surface-sunken p-3">
      {steps.map((step, i) => (
        <li key={i} className="flex items-start gap-2.5 text-[12.5px]">
          <span className="mt-0.5 shrink-0">{ICON[step.status]({ size: 15 })}</span>
          <span className={step.status === 'error' ? 'text-stopped' : 'text-ink-soft'}>
            {step.label}
            {step.note && <span className="block text-[11.5px] text-ink-muted">{step.note}</span>}
          </span>
        </li>
      ))}
    </ol>
  )
}
